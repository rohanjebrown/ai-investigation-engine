"""Exact pgvector search with SQL visibility/completeness before LIMIT."""

from sqlalchemy import and_, exists, func, literal, literal_column, or_, select, text, union_all
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
from investigation_engine.application.timing import stage_timing
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
    SearchableChunkRow,
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
            func.count().over(partition_by=DocumentVersionRow.id).label("matching_plans"),
        )
        .select_from(DocumentRow)
        .join(DocumentVersionRow, DocumentVersionRow.id == DocumentRow.current_version_id)
        .join(VersionChunkPlanRow, VersionChunkPlanRow.document_version_id == DocumentVersionRow.id)
        .join(ChunkingConfigurationRow, ChunkingConfigurationRow.id == VersionChunkPlanRow.chunking_configuration_id)
        .where(
            DocumentRow.deleted_at.is_(None),
            visibility,
            DocumentVersionRow.document_id == DocumentRow.id,
            *(getattr(ChunkingConfigurationRow, key) == value for key, value in values.items()),
        )
        .cte("visible_serving_plans")
    )

    chunk_text = _chunk_text(ContentRow.text, ChunkMembershipRow.start, ChunkMembershipRow.end)
    # Evaluate text-dependent scalars before windows/sorts. The materialization
    # boundary prevents PostgreSQL carrying canonical text through those sorts.
    values = (
        select(
            plans,
            func.char_length(ContentRow.text).label("content_length"),
            ChunkMembershipRow.ordinal,
            ChunkMembershipRow.start,
            ChunkMembershipRow.end,
            keys.c.embedding_id,
            # Only blankness matters. btrim stops at the first non-whitespace
            # boundary; translate needlessly scans every character of the chunk.
            (func.btrim(chunk_text, PYTHON_WHITESPACE) != "").label("eligible"),
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
        .cte("membership_values")
        .prefix_with("MATERIALIZED")
    )
    partition = (values.c.version_id, values.c.configuration_id)
    members = (
        select(
            values,
            func.lag(values.c.start).over(partition_by=partition, order_by=values.c.ordinal).label("previous_start"),
            func.max(values.c.end)
            .over(partition_by=partition, order_by=values.c.ordinal, rows=(None, -1))
            .label("covered_before"),
        )
        .where(values.c.expected_chunk_count > 1)
        .cte("serving_memberships")
    )
    m = members.c
    before = func.coalesce(m.covered_before, 0)
    # The validated ck_chunk_memberships_range constraint already enforces
    # nonnegative start and end > start for every stored membership.
    geometry = and_(
        m.end <= m.content_length,
        m.start > func.coalesce(m.previous_start, -1),
        m.start <= before,
        or_(and_(m.strategy == "fixed_tokens", m.overlap > 0), m.start == before),
    )
    # Match validate_chunk_plan's coverage rules without fetching/reconstructing
    # every plan. Count ALL memberships, but require vectors only for nonblank ones.
    multi_complete = (
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
    )
    # A one-membership plan needs no coverage window. Verify its actual shape,
    # including absence of extra ordinals, rather than trusting the stored count.
    v = values.c
    extra_membership = exists(
        select(1).where(
            ChunkMembershipRow.document_version_id == v.version_id,
            ChunkMembershipRow.chunking_configuration_id == v.configuration_id,
            ChunkMembershipRow.ordinal != 0,
        )
    )
    single_complete = select(v.version_id, v.configuration_id).where(
        v.expected_chunk_count == 1,
        v.matching_plans == 1,
        v.ordinal == 0,
        v.start == 0,
        v.end == v.content_length,
        v.eligible,
        v.embedding_id.is_not(None),
        ~extra_membership,
    )
    complete = union_all(single_complete, multi_complete).cte("complete_serving_plans")

    # Select one authorized, complete occurrence for each exact canonical range
    # BEFORE channel limits. Preserve its own version/provenance for hydration.
    # Different ranges, even overlapping ones, remain independent candidates.
    m = values.c
    representatives = (
        select(
            m.document_id,
            m.version_id,
            m.content_sha256,
            m.start,
            m.end,
            m.content_length,
            m.embedding_id,
            func.row_number()
            .over(
                partition_by=(m.content_sha256, m.start, m.end),
                order_by=(m.document_id, m.version_id),
            )
            .label("occurrence_rank"),
        )
        .join(complete, and_(complete.c.version_id == m.version_id, complete.c.configuration_id == m.configuration_id))
        .where(m.eligible)
        .cte("eligible_occurrences")
    )
    diverse = select(representatives).where(representatives.c.occurrence_rank == 1).cte("diverse_serving_memberships")
    return diverse


def _candidate_statement(members, score, ordering, channel):
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
        .order_by(ordering, m.document_id, m.version_id, m.start, m.end)
    )


def _chunk_text(content_text, start, end):
    """Translate a half-open Python range to PostgreSQL's one-based substring."""
    return func.substr(content_text, start + 1, end - start)


def _semantic_statement(members, vector, limit):
    member_columns = members.c
    # Materialize only narrow identity/score rows, not vector payloads. Score
    # each compatible stored vector once and join to eligible representatives.
    # No LIMIT is applied before visibility/completeness have been established.
    scores = (
        select(
            ChunkEmbeddingRow.embedding_configuration_id,
            ChunkEmbeddingRow.content_sha256,
            ChunkEmbeddingRow.start,
            ChunkEmbeddingRow.end,
            ChunkEmbeddingRow.embedding.cosine_distance(list(vector)).label("distance"),
        )
        .where(ChunkEmbeddingRow.embedding_configuration_id.in_(select(member_columns.embedding_id).distinct()))
        .cte("semantic_scores")
        .prefix_with("MATERIALIZED")
    )
    distance = scores.c.distance
    return (
        _candidate_statement(members, 1.0 - distance, distance, "cosine_similarity")
        .join(
            scores,
            and_(
                scores.c.embedding_configuration_id == member_columns.embedding_id,
                scores.c.content_sha256 == member_columns.content_sha256,
                scores.c.start == member_columns.start,
                scores.c.end == member_columns.end,
            ),
        )
        .limit(limit)
    )


def _keyword_statement(members, question, limit):
    m = members.c
    # English question/corpus baseline: built-in stemming and stop words apply
    # only to the derived lexical representation, never canonical Content.
    configuration = literal_column("'pg_catalog.english'::regconfig")
    chunk_text = _chunk_text(ContentRow.text, ChunkEmbeddingRow.start, ChunkEmbeddingRow.end)
    vector = func.to_tsvector(configuration, chunk_text)
    query = func.plainto_tsquery(configuration, question)
    score = func.ts_rank(vector, query)
    # Compute each matching canonical range once before joining its authorized
    # occurrence. The matching set has no pre-eligibility result limit.
    matches = (
        select(
            ChunkEmbeddingRow.embedding_configuration_id,
            ChunkEmbeddingRow.content_sha256,
            ChunkEmbeddingRow.start,
            ChunkEmbeddingRow.end,
            score.label("score"),
        )
        .join(ContentRow, ContentRow.sha256 == ChunkEmbeddingRow.content_sha256)
        .where(
            ChunkEmbeddingRow.embedding_configuration_id.in_(select(m.embedding_id).distinct()),
            vector.op("@@")(query),
        )
        .cte("lexical_matches")
        .prefix_with("MATERIALIZED")
    )
    match_score = matches.c.score
    return (
        _candidate_statement(members, match_score, -match_score, "keyword_rank")
        .join(
            matches,
            and_(
                matches.c.embedding_configuration_id == m.embedding_id,
                matches.c.content_sha256 == m.content_sha256,
                matches.c.start == m.start,
                matches.c.end == m.end,
            ),
        )
        .limit(limit)
    )


def _bm25_statement(members, question, limit):
    m = members.c
    bm25_query = func.to_bm25query(question, "searchable_chunks_bm25_idx")
    score = SearchableChunkRow.text.op("<@>")(bm25_query)
    matches = (
        select(
            SearchableChunkRow.content_sha256,
            SearchableChunkRow.start,
            SearchableChunkRow.end,
            score.label("score"),
        )
        .select_from(SearchableChunkRow)
        .join(
            members,
            and_(
                members.c.content_sha256 == SearchableChunkRow.content_sha256,
                members.c.start == SearchableChunkRow.start,
                members.c.end == SearchableChunkRow.end,
            ),
        )
        .order_by(score, SearchableChunkRow.content_sha256, SearchableChunkRow.start, SearchableChunkRow.end)
        .limit(limit)
        .cte("bm25_eligible_matches")
    )
    return (
        _candidate_statement(members, matches.c.score, matches.c.score, "keyword_rank")
        .join(
            matches,
            and_(matches.c.content_sha256 == m.content_sha256, matches.c.start == m.start, matches.c.end == m.end),
        )
        .order_by(matches.c.score, m.document_id, m.version_id, m.start, m.end)
        .limit(limit)
    )


def _hydrate(statement):
    """Resolve trusted payloads only for already eligible, ranked candidates.

    All selection remains in the same SQL statement/snapshot. No eligibility
    predicate moves after LIMIT. The selected representative retains its own
    source occurrence; other stored occurrences are unchanged.
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
    members = _eligible_memberships(chunking, embedding, owner_id)
    return _hydrate(_semantic_statement(members, vector, limit))


def _hybrid_statement(
    vector, question, chunking, embedding, owner_id, semantic_limit, keyword_limit, lexical_backend="postgres_fts"
):
    members = _eligible_memberships(chunking, embedding, owner_id)
    hits = union_all(
        _semantic_statement(members, vector, semantic_limit),
        (
            _bm25_statement(members, question, keyword_limit)
            if lexical_backend == "bm25"
            else _keyword_statement(members, question, keyword_limit)
        ),
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
    def __init__(self, sessions: async_sessionmaker[AsyncSession], *, lexical_backend: str = "postgres_fts") -> None:
        if lexical_backend not in ("postgres_fts", "bm25"):
            raise ValueError("Unsupported lexical search backend")
        self._sessions = sessions
        self._lexical_backend = lexical_backend

    async def validate_backend(self) -> None:
        """Fail closed when BM25 is selected but extension/index/backfill is absent."""
        if self._lexical_backend != "bm25":
            return
        try:
            async with self._sessions() as session:
                async with session.begin():
                    state = (
                        await session.execute(
                            text("""
                        SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_extension WHERE extname='pg_textsearch'),
                               pg_catalog.to_regclass('public.searchable_chunks') IS NOT NULL,
                               pg_catalog.to_regclass('public.searchable_chunks_bm25_idx') IS NOT NULL,
                               (SELECT count(*) FROM searchable_chunks),
                               (SELECT count(*) FROM (
                                  SELECT DISTINCT v.content_sha256, m.start, m.end
                                  FROM chunk_memberships m
                                  JOIN document_versions v ON v.id=m.document_version_id
                               ) expected),
                               (SELECT count(*) FROM (
                                  SELECT DISTINCT v.content_sha256, m.start, m.end,
                                         pg_catalog.substr(c.text, m.start + 1, m.end - m.start) expected_text
                                  FROM chunk_memberships m
                                  JOIN document_versions v ON v.id=m.document_version_id
                                  JOIN contents c ON c.sha256=v.content_sha256
                               ) expected
                               LEFT JOIN searchable_chunks s USING (content_sha256, start, "end")
                               WHERE s.content_sha256 IS NULL OR s.text IS DISTINCT FROM expected.expected_text)
                    """)
                        )
                    ).one()
            if not state[0] or not state[1] or not state[2] or state[3] != state[4] or state[5]:
                raise RuntimeError("BM25 requires pg_textsearch, its index, and a complete searchable_chunks backfill")
        except SQLAlchemyError as error:
            raise RuntimeError("BM25 backend validation failed; verify migrations and pg_textsearch") from error

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
        rows = await self._read(statement, "semantic_sql_roundtrip")
        with stage_timing("evidence_hydration"):
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
        members = _eligible_memberships(chunking, embedding, owner_id)
        statement = (
            _bm25_statement(members, question, limit)
            if self._lexical_backend == "bm25"
            else _keyword_statement(members, question, limit)
        )
        rows = await self._read(_hydrate(statement), "lexical_sql_roundtrip")
        with stage_timing("evidence_hydration"):
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
                self._lexical_backend,
            ),
            "hybrid_sql_roundtrip",
        )
        channels = {"cosine_similarity": [], "keyword_rank": []}
        with stage_timing("evidence_hydration"):
            for row in rows:
                channel = channels[row["channel"]]
                channel.append(_evidence(row, len(channel) + 1))
        return tuple(channels["cosine_similarity"]), tuple(channels["keyword_rank"])

    async def _read(self, statement, stage):
        try:
            async with self._sessions() as session:
                async with session.begin():
                    with stage_timing("connection_acquisition"):
                        await session.connection(execution_options={"isolation_level": "READ COMMITTED"})
                    # Includes driver transfer/decoding and server work. Server-only
                    # and channel branch timings come from EXPLAIN, not this clock.
                    with stage_timing(stage):
                        rows = (await session.execute(statement)).mappings().all()
            return rows
        except SQLAlchemyError as error:
            raise RepositoryError("Evidence search could not be completed") from error
