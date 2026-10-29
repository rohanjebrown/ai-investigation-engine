"""Exact pgvector search with SQL visibility/completeness before LIMIT."""

from sqlalchemy import and_, func, literal, literal_column, or_, select, union_all
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from investigation_engine.application.chunking import ChunkingConfig
from investigation_engine.application.embeddings import EmbeddingConfig, EmbeddingVector, storage_vector
from investigation_engine.application.ports.repository_errors import RepositoryError
from investigation_engine.application.retrieval import (
    Evidence,
    intersecting_source_spans,
    validate_search_question,
    validate_search_scope,
)
from investigation_engine.domain.provenance import SourceKind, SourceReference
from investigation_engine.domain.source_span import LocatorKind, SourceLocator, SourceSpan
from investigation_engine.infrastructure.persistence._document_mapping import _validate_json_object
from investigation_engine.infrastructure.persistence._embedding_mapping import configuration_filter
from investigation_engine.infrastructure.persistence.models import (
    ChunkEmbeddingRow,
    ChunkingConfigurationRow,
    ChunkMembershipRow,
    ContentRow,
    DocumentRow,
    DocumentVersionRow,
    EmbeddingConfigurationRow,
    VersionChunkPlanRow,
)
from investigation_engine.infrastructure.persistence.postgresql_chunk_plan_repository import _configuration_values

# Exactly Python 3.14 str.isspace()/strip(): POSIX space or SQL trim defaults
# differ for NBSP, NEL and the four ASCII information separators.
PYTHON_WHITESPACE = (
    "\t\n\v\f\r\x1c\x1d\x1e\x1f \x85\xa0\u1680"
    "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029\u202f\u205f\u3000"
)


def _eligible_memberships(chunking: ChunkingConfig, embedding: EmbeddingConfig, owner_id: str | None):
    # Canonicalize equivalent legacy profile rows exactly as embedding reads do.
    # Keys only: never materialize all vectors in application memory.
    keys = (
        select(
            ChunkEmbeddingRow.content_sha256,
            ChunkEmbeddingRow.start,
            ChunkEmbeddingRow.end,
            func.min(ChunkEmbeddingRow.embedding_configuration_id).label("embedding_id"),
        )
        .join(EmbeddingConfigurationRow)
        .where(*configuration_filter(embedding))
        .group_by(ChunkEmbeddingRow.content_sha256, ChunkEmbeddingRow.start, ChunkEmbeddingRow.end)
        .cte("compatible_embedding_keys")
    )

    values = _configuration_values(chunking)
    visibility = DocumentRow.owner_id.is_(None)
    if owner_id is not None:
        visibility = or_(visibility, DocumentRow.owner_id == owner_id)
    plans = (
        select(
            DocumentRow.id.label("document_id"),
            DocumentVersionRow.id.label("version_id"),
            DocumentVersionRow.content_sha256,
            VersionChunkPlanRow.chunking_configuration_id.label("configuration_id"),
            VersionChunkPlanRow.expected_chunk_count,
            ChunkingConfigurationRow.strategy,
            ChunkingConfigurationRow.overlap,
            func.char_length(ContentRow.text).label("content_length"),
            func.count().over(partition_by=DocumentVersionRow.id).label("matching_plans"),
        )
        .select_from(DocumentRow)
        .join(DocumentVersionRow, DocumentVersionRow.id == DocumentRow.current_version_id)
        .join(VersionChunkPlanRow, VersionChunkPlanRow.document_version_id == DocumentVersionRow.id)
        .join(ChunkingConfigurationRow, ChunkingConfigurationRow.id == VersionChunkPlanRow.chunking_configuration_id)
        .join(ContentRow, ContentRow.sha256 == DocumentVersionRow.content_sha256)
        .where(
            DocumentRow.deleted_at.is_(None),
            visibility,
            DocumentVersionRow.document_id == DocumentRow.id,
            *(getattr(ChunkingConfigurationRow, key) == value for key, value in values.items()),
        )
        .cte("visible_serving_plans")
    )

    partition = (ChunkMembershipRow.document_version_id, ChunkMembershipRow.chunking_configuration_id)
    chunk_text = _chunk_text(ContentRow.text, ChunkMembershipRow.start, ChunkMembershipRow.end)
    members = (
        select(
            plans,
            ChunkMembershipRow.ordinal,
            ChunkMembershipRow.start,
            ChunkMembershipRow.end,
            keys.c.embedding_id,
            # Only blankness matters. btrim stops at the first non-whitespace
            # boundary; translate needlessly scans every character of the chunk.
            (func.btrim(chunk_text, PYTHON_WHITESPACE) != "").label("eligible"),
            func.lag(ChunkMembershipRow.start)
            .over(
                partition_by=partition,
                order_by=ChunkMembershipRow.ordinal,
            )
            .label("previous_start"),
            func.max(ChunkMembershipRow.end)
            .over(
                partition_by=partition,
                order_by=ChunkMembershipRow.ordinal,
                rows=(None, -1),
            )
            .label("covered_before"),
        )
        .select_from(plans)
        .join(
            ChunkMembershipRow,
            and_(
                ChunkMembershipRow.document_version_id == plans.c.version_id,
                ChunkMembershipRow.chunking_configuration_id == plans.c.configuration_id,
            ),
        )
        .join(ContentRow, ContentRow.sha256 == plans.c.content_sha256)
        .outerjoin(
            keys,
            and_(
                keys.c.content_sha256 == plans.c.content_sha256,
                keys.c.start == ChunkMembershipRow.start,
                keys.c.end == ChunkMembershipRow.end,
            ),
        )
        .cte("serving_memberships")
    )
    m = members.c
    before = func.coalesce(m.covered_before, 0)
    geometry = and_(
        m.start >= 0,
        m.end > m.start,
        m.end <= m.content_length,
        m.start > func.coalesce(m.previous_start, -1),
        m.start <= before,
        or_(and_(m.strategy == "fixed_tokens", m.overlap > 0), m.start == before),
    )
    # Match validate_chunk_plan's coverage rules without fetching/reconstructing
    # every plan. Count ALL memberships, but require vectors only for nonblank ones.
    complete = (
        select(m.version_id, m.configuration_id)
        .group_by(m.version_id, m.configuration_id, m.expected_chunk_count, m.content_length, m.matching_plans)
        .having(
            m.matching_plans == 1,
            func.count() == m.expected_chunk_count,
            func.min(m.ordinal) == 0,
            func.max(m.ordinal) == m.expected_chunk_count - 1,
            func.max(m.end) == m.content_length,
            func.bool_and(geometry),
            func.count().filter(m.eligible) > 0,
            func.bool_and(or_(~m.eligible, m.embedding_id.is_not(None))),
        )
        .cte("complete_serving_plans")
    )

    return members, complete


def _candidate_statement(members, complete, score, ordering, channel):
    m = members.c
    return (
        select(
            m.document_id,
            m.version_id,
            m.content_sha256,
            m.start,
            m.end,
            m.content_length,
            score.label("retrieval_score"),
            ordering.label("ordering_value"),
            literal(channel).label("channel"),
        )
        .select_from(members)
        .join(complete, and_(complete.c.version_id == m.version_id, complete.c.configuration_id == m.configuration_id))
        .where(m.eligible)
        .order_by(ordering, m.document_id, m.version_id, m.start, m.end)
    )


def _chunk_text(content_text, start, end):
    """Translate a half-open Python range to PostgreSQL's one-based substring."""
    return func.substr(content_text, start + 1, end - start)


def _semantic_statement(members, complete, vector, limit):
    m = members.c
    distance = ChunkEmbeddingRow.embedding.cosine_distance(list(vector))
    return (
        _candidate_statement(members, complete, 1.0 - distance, distance, "cosine_similarity")
        .join(
            ChunkEmbeddingRow,
            and_(
                ChunkEmbeddingRow.embedding_configuration_id == m.embedding_id,
                ChunkEmbeddingRow.content_sha256 == m.content_sha256,
                ChunkEmbeddingRow.start == m.start,
                ChunkEmbeddingRow.end == m.end,
            ),
        )
        .limit(limit)
    )


def _keyword_statement(members, complete, question, limit):
    m = members.c
    # English question/corpus baseline: built-in stemming and stop words apply
    # only to the derived lexical representation, never canonical Content.
    configuration = literal_column("'pg_catalog.english'::regconfig")
    chunk_text = _chunk_text(ContentRow.text, m.start, m.end)
    vector = func.to_tsvector(configuration, chunk_text)
    query = func.plainto_tsquery(configuration, question)
    score = func.ts_rank(vector, query)
    return (
        _candidate_statement(members, complete, score, -score, "keyword_rank")
        .where(vector.op("@@")(query))
        .join(ContentRow, ContentRow.sha256 == m.content_sha256)
        .limit(limit)
    )


def _hydrate(statement):
    """Resolve trusted payloads only for already eligible, ranked candidates.

    All selection remains in the same SQL statement/snapshot. No eligibility
    predicate moves after LIMIT and no source occurrence is grouped away.
    """
    hits = statement.subquery("ranked_hits")
    return (
        select(
            hits,
            _chunk_text(ContentRow.text, hits.c.start, hits.c.end).label("text"),
            DocumentRow.title,
            DocumentVersionRow.kind,
            DocumentVersionRow.source_id,
            DocumentVersionRow.revision,
            DocumentVersionRow.display_name,
            DocumentVersionRow.record_locator,
            DocumentVersionRow.source_metadata,
            DocumentVersionRow.source_spans,
        )
        .select_from(hits)
        .join(DocumentRow, DocumentRow.id == hits.c.document_id)
        .join(DocumentVersionRow, DocumentVersionRow.id == hits.c.version_id)
        .join(ContentRow, ContentRow.sha256 == hits.c.content_sha256)
        .order_by(
            hits.c.channel, hits.c.ordering_value, hits.c.document_id, hits.c.version_id, hits.c.start, hits.c.end
        )
    )


def _search_statement(vector, chunking, embedding, owner_id, limit):
    members, complete = _eligible_memberships(chunking, embedding, owner_id)
    return _hydrate(_semantic_statement(members, complete, vector, limit))


def _hybrid_statement(vector, question, chunking, embedding, owner_id, semantic_limit, keyword_limit):
    members, complete = _eligible_memberships(chunking, embedding, owner_id)
    hits = union_all(
        _semantic_statement(members, complete, vector, semantic_limit),
        _keyword_statement(members, complete, question, keyword_limit),
    )
    return _hydrate(hits)


def _evidence(row, rank: int) -> Evidence:
    try:
        source = _source_reference(row)
        spans = _source_spans(row)
        return Evidence(
            document_id=row["document_id"],
            document_version_id=row["version_id"],
            content_sha256=row["content_sha256"].hex(),
            start=row["start"],
            end=row["end"],
            text=row["text"],
            source=source,
            title=row["title"],
            rank=rank,
            score=float(row["retrieval_score"]),
            score_kind=row["channel"],
            source_spans=intersecting_source_spans(tuple(spans), row["start"], row["end"]),
        )
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        raise RepositoryError("Retrieved evidence has invalid text, score or provenance") from error


def _source_reference(row) -> SourceReference:
    _validate_json_object(row["record_locator"])
    _validate_json_object(row["source_metadata"])
    return SourceReference(
        kind=SourceKind(row["kind"]),
        source_id=row["source_id"],
        revision=row["revision"],
        display_name=row["display_name"],
        record_locator=row["record_locator"],
        metadata=row["source_metadata"],
    )


def _source_spans(row) -> tuple[SourceSpan, ...]:
    spans = []
    for raw_span in row["source_spans"]:
        if (
            type(raw_span["start"]) is not int
            or type(raw_span["end"]) is not int
            or raw_span["end"] > row["content_length"]
        ):
            raise ValueError("Invalid stored source character bounds")
        locators = []
        for raw_locator in raw_span["locators"]:
            if type(raw_locator["index"]) is not int:
                raise ValueError("Invalid stored source index")
            locators.append(SourceLocator(**{**raw_locator, "kind": LocatorKind(raw_locator["kind"])}))
        spans.append(SourceSpan(**{**raw_span, "locators": tuple(locators)}))
    return tuple(spans)


class PostgreSQLEvidenceRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def search(
        self,
        vector: EmbeddingVector,
        chunking: ChunkingConfig,
        embedding: EmbeddingConfig,
        *,
        owner_id: str | None,
        limit: int,
    ) -> tuple[Evidence, ...]:
        validate_search_scope(owner_id, limit)
        vector = storage_vector(vector)
        statement = _search_statement(vector, chunking, embedding, owner_id, limit)
        rows = await self._read(statement)
        return tuple(_evidence(row, index) for index, row in enumerate(rows, 1))

    async def search_keywords(
        self,
        question: str,
        chunking: ChunkingConfig,
        embedding: EmbeddingConfig,
        *,
        owner_id: str | None,
        limit: int,
    ) -> tuple[Evidence, ...]:
        validate_search_scope(owner_id, limit)
        validate_search_question(question)
        members, complete = _eligible_memberships(chunking, embedding, owner_id)
        rows = await self._read(_hydrate(_keyword_statement(members, complete, question, limit)))
        return tuple(_evidence(row, index) for index, row in enumerate(rows, 1))

    async def search_channels(
        self,
        vector: EmbeddingVector,
        question: str,
        chunking: ChunkingConfig,
        embedding: EmbeddingConfig,
        *,
        owner_id: str | None,
        semantic_limit: int,
        keyword_limit: int,
    ) -> tuple[tuple[Evidence, ...], tuple[Evidence, ...]]:
        validate_search_scope(owner_id, semantic_limit)
        validate_search_scope(owner_id, keyword_limit)
        validate_search_question(question)
        vector = storage_vector(vector)
        rows = await self._read(
            _hybrid_statement(
                vector,
                question,
                chunking,
                embedding,
                owner_id,
                semantic_limit,
                keyword_limit,
            )
        )
        channels = {"cosine_similarity": [], "keyword_rank": []}
        for row in rows:
            channel = channels[row["channel"]]
            channel.append(_evidence(row, len(channel) + 1))
        return tuple(channels["cosine_similarity"]), tuple(channels["keyword_rank"])

    async def _read(self, statement):
        try:
            async with self._sessions() as session:
                async with session.begin():
                    await session.connection(execution_options={"isolation_level": "READ COMMITTED"})
                    rows = (await session.execute(statement)).mappings().all()
            return rows
        except SQLAlchemyError as error:
            raise RepositoryError("Evidence search could not be completed") from error
