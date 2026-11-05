"""Exact pgvector search with SQL visibility/completeness before LIMIT."""

import logging

from sqlalchemy import and_, exists, func, literal, literal_column, or_, select, text, tuple_, union_all
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

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
    ChunkPlanReadinessRow,
    ContentRow,
    DocumentRow,
    DocumentVersionRow,
    EmbeddingConfigurationRow,
    MembershipChunkEmbeddingRow,
    SearchableChunkRow,
    VersionChunkPlanRow,
)
from investigation_engine.infrastructure.persistence.postgresql_chunk_plan_repository import _configuration_values

logger = logging.getLogger(__name__)

# Exactly Python 3.14 str.isspace()/strip(): POSIX space or SQL trim defaults
# differ for NBSP, NEL and the four ASCII information separators.
PYTHON_WHITESPACE = (
    "\t\n\v\f\r\x1c\x1d\x1e\x1f \x85\xa0\u1680"
    "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029\u202f\u205f\u3000"
)


def _eligible_memberships(
    chunking: ChunkingConfig, embedding: EmbeddingConfig, owner_id: str | None, candidate_keys=None, suffix=""
):
    hashes = None
    if candidate_keys is not None:
        hashes = (
            select(candidate_keys.c.content_sha256)
            .distinct()
            .cte(f"candidate_contents{suffix}")
            .prefix_with("MATERIALIZED")
        )

    # Canonicalize equivalent legacy profile rows exactly as embedding reads do.
    # Keys only: never materialize all vectors in application memory.
    key_query = select(
        ChunkEmbeddingRow.content_sha256,
        ChunkEmbeddingRow.start,
        ChunkEmbeddingRow.end,
        func.min(ChunkEmbeddingRow.embedding_configuration_id).label("embedding_id"),
    )
    if hashes is not None:
        # Scope by content only: noncandidate sibling ranges are still needed
        # to establish complete embedding coverage for each selected plan.
        key_query = key_query.select_from(hashes).join(
            ChunkEmbeddingRow, ChunkEmbeddingRow.content_sha256 == hashes.c.content_sha256
        )
    else:
        key_query = key_query.select_from(ChunkEmbeddingRow)
    keys = (
        key_query.join(EmbeddingConfigurationRow)
        .where(*configuration_filter(embedding))
        .group_by(ChunkEmbeddingRow.content_sha256, ChunkEmbeddingRow.start, ChunkEmbeddingRow.end)
        .cte(f"compatible_embedding_keys{suffix}")
    )

    values = _configuration_values(chunking)
    visibility = DocumentRow.owner_id.is_(None)
    if owner_id is not None:
        visibility = or_(visibility, DocumentRow.owner_id == owner_id)
    if candidate_keys is not None:
        # Find occurrences through the content index once, rather than probing
        # the candidate CTE separately for every active document. Keep every
        # matching plan/membership of these versions for completeness checks.
        candidate_versions = (
            select(DocumentVersionRow.id, DocumentVersionRow.document_id, DocumentVersionRow.content_sha256)
            .select_from(hashes)
            .join(DocumentVersionRow, DocumentVersionRow.content_sha256 == hashes.c.content_sha256)
            .cte(f"candidate_versions{suffix}")
            .prefix_with("MATERIALIZED")
        )
        version = aliased(DocumentVersionRow, candidate_versions)
    else:
        version = DocumentVersionRow
    plans = (
        select(
            DocumentRow.id.label("document_id"),
            version.id.label("version_id"),
            version.content_sha256,
            VersionChunkPlanRow.chunking_configuration_id.label("configuration_id"),
            VersionChunkPlanRow.expected_chunk_count,
            ChunkingConfigurationRow.strategy,
            ChunkingConfigurationRow.overlap,
            func.count().over(partition_by=version.id).label("matching_plans"),
        )
        .select_from(DocumentRow)
        .join(version, version.id == DocumentRow.current_version_id)
        .join(VersionChunkPlanRow, VersionChunkPlanRow.document_version_id == version.id)
        .join(ChunkingConfigurationRow, ChunkingConfigurationRow.id == VersionChunkPlanRow.chunking_configuration_id)
        .where(
            DocumentRow.deleted_at.is_(None),
            visibility,
            version.document_id == DocumentRow.id,
            *(getattr(ChunkingConfigurationRow, key) == value for key, value in values.items()),
        )
        .cte(f"visible_serving_plans{suffix}")
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
        .cte(f"membership_values{suffix}")
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
        .cte(f"serving_memberships{suffix}")
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
    complete = union_all(single_complete, multi_complete).cte(f"complete_serving_plans{suffix}")
    if candidate_keys is not None:
        # Otherwise the planner can repeat single-plan validation for each
        # occurrence/candidate join, instead of once per selected plan.
        complete = complete.prefix_with("MATERIALIZED")

    # Select one authorized, complete occurrence for each exact canonical range
    # BEFORE channel limits. Preserve its own version/provenance for hydration.
    # Different ranges, even overlapping ones, remain independent candidates.
    m = values.c
    representative_query = select(
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
    ).where(m.eligible)
    if candidate_keys is None:
        representative_query = representative_query.join(
            complete, and_(complete.c.version_id == m.version_id, complete.c.configuration_id == m.configuration_id)
        )
    else:
        # Keep this as a boolean membership test so PostgreSQL can hash the
        # complete keys once; NULL/unknown membership must fail closed.
        representative_query = representative_query.where(
            tuple_(m.version_id, m.configuration_id)
            .in_(select(complete.c.version_id, complete.c.configuration_id))
            .is_(True)
        )
    if candidate_keys is not None:
        representative_query = representative_query.join(
            candidate_keys,
            and_(
                candidate_keys.c.embedding_configuration_id == m.embedding_id,
                candidate_keys.c.content_sha256 == m.content_sha256,
                candidate_keys.c.start == m.start,
                candidate_keys.c.end == m.end,
            ),
        )
    representatives = representative_query.cte(f"eligible_occurrences{suffix}")
    diverse = (
        select(representatives)
        .where(representatives.c.occurrence_rank == 1)
        .cte(f"diverse_serving_memberships{suffix}")
    )
    return diverse


def _ready_memberships(
    chunking: ChunkingConfig, embedding: EmbeddingConfig, owner_id: str | None, candidate_keys=None, suffix=""
):
    """Use validated readiness markers instead of reconstructing complete plans.

    The marker is authoritative for plan geometry, ordered membership coverage,
    and equivalent-profile embedding completeness. This query still applies
    live visibility, authorization, exact blank-chunk eligibility, canonical
    embedding selection, and occurrence deduplication.
    """
    values = _configuration_values(chunking)
    visibility = DocumentRow.owner_id.is_(None)
    if owner_id is not None:
        visibility = or_(visibility, DocumentRow.owner_id == owner_id)

    canonical_configuration_id = (
        select(func.min(EmbeddingConfigurationRow.id)).where(*configuration_filter(embedding)).scalar_subquery()
    )
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
        .cte(f"compatible_embedding_keys{suffix}")
    )

    # Keep the authorized, current, ready plan occurrences on the inside of the
    # membership expansion. Without this boundary PostgreSQL can
    # start from all embeddings and memberships, merge them on just the chunk
    # range, and defer the content hash join until after millions of version
    # lookups. Readiness has already validated these exact plans, so restricting
    # to this set first preserves eligibility while avoiding that join order.
    candidate_plan_exists = True
    if candidate_keys is not None:
        candidate_plan_exists = exists(
            select(1)
            .select_from(ChunkMembershipRow)
            .join(
                candidate_keys,
                and_(
                    candidate_keys.c.content_sha256 == DocumentVersionRow.content_sha256,
                    candidate_keys.c.start == ChunkMembershipRow.start,
                    candidate_keys.c.end == ChunkMembershipRow.end,
                ),
            )
            .where(
                ChunkMembershipRow.document_version_id == DocumentVersionRow.id,
                ChunkMembershipRow.chunking_configuration_id == ChunkPlanReadinessRow.chunking_configuration_id,
            )
        )
    ready_plans = (
        select(
            DocumentRow.id.label("document_id"),
            DocumentVersionRow.id.label("version_id"),
            DocumentVersionRow.content_sha256,
            ChunkPlanReadinessRow.chunking_configuration_id.label("configuration_id"),
        )
        .select_from(DocumentRow)
        .join(DocumentVersionRow, DocumentVersionRow.id == DocumentRow.current_version_id)
        .join(
            ChunkPlanReadinessRow,
            and_(
                ChunkPlanReadinessRow.document_version_id == DocumentVersionRow.id,
                ChunkPlanReadinessRow.embedding_configuration_id == canonical_configuration_id,
            ),
        )
        .join(
            ChunkingConfigurationRow,
            ChunkingConfigurationRow.id == ChunkPlanReadinessRow.chunking_configuration_id,
        )
        .where(
            DocumentRow.deleted_at.is_(None),
            DocumentVersionRow.document_id == DocumentRow.id,
            visibility,
            *(getattr(ChunkingConfigurationRow, key) == value for key, value in values.items()),
            candidate_plan_exists,
        )
        .cte(f"ready_serving_plans{suffix}")
        .prefix_with("MATERIALIZED")
    )
    eligible_query = (
        select(
            ready_plans.c.document_id,
            ready_plans.c.version_id,
            ready_plans.c.content_sha256,
            ChunkMembershipRow.start,
            ChunkMembershipRow.end,
        )
        .select_from(ready_plans)
        .join(
            ChunkMembershipRow,
            and_(
                ChunkMembershipRow.document_version_id == ready_plans.c.version_id,
                ChunkMembershipRow.chunking_configuration_id == ready_plans.c.configuration_id,
            ),
        )
    )
    if candidate_keys is not None:
        eligible_query = eligible_query.join(
            candidate_keys,
            and_(
                candidate_keys.c.embedding_configuration_id == canonical_configuration_id,
                candidate_keys.c.content_sha256 == ready_plans.c.content_sha256,
                candidate_keys.c.start == ChunkMembershipRow.start,
                candidate_keys.c.end == ChunkMembershipRow.end,
            ),
        )
    eligible = eligible_query.cte(f"ready_eligible_occurrences{suffix}")
    occurrence = eligible.c
    ranked = (
        select(
            occurrence.document_id,
            occurrence.version_id,
            occurrence.content_sha256,
            occurrence.start,
            occurrence.end,
        )
        .distinct(occurrence.content_sha256, occurrence.start, occurrence.end)
        .order_by(
            occurrence.content_sha256,
            occurrence.start,
            occurrence.end,
            occurrence.document_id,
            occurrence.version_id,
        )
        .cte(f"ranked_ready_occurrences{suffix}")
    )
    representatives = select(ranked).cte(f"ranked_ready_representatives{suffix}")
    # Blankness and length depend only on the content hash and exact range.
    # Resolve them once, after choosing the canonical occurrence.
    nonblank_representatives = (
        select(
            representatives.c.document_id,
            representatives.c.version_id,
            representatives.c.content_sha256,
            representatives.c.start,
            representatives.c.end,
            func.char_length(ContentRow.text).label("content_length"),
        )
        .select_from(representatives)
        .join(ContentRow, ContentRow.sha256 == representatives.c.content_sha256)
        .where(
            func.btrim(
                _chunk_text(ContentRow.text, representatives.c.start, representatives.c.end),
                PYTHON_WHITESPACE,
            )
            != ""
        )
        .cte(f"nonblank_ready_representatives{suffix}")
    )
    return (
        select(nonblank_representatives, keys.c.embedding_id)
        .join(
            keys,
            and_(
                keys.c.content_sha256 == nonblank_representatives.c.content_sha256,
                keys.c.start == nonblank_representatives.c.start,
                keys.c.end == nonblank_representatives.c.end,
            ),
        )
        .cte(f"diverse_serving_memberships{suffix}")
    )


def _membership_owned_memberships(chunking: ChunkingConfig, embedding: EmbeddingConfig, owner_id: str | None):
    """Select complete, authorized ready plans using direct membership vectors.

    Readiness still validates the legacy representation, so this path separately
    requires every nonblank membership in each plan to have its own selected
    compatible vector before that plan can contribute results.
    """
    values = _configuration_values(chunking)
    visibility = DocumentRow.owner_id.is_(None)
    if owner_id is not None:
        visibility = or_(visibility, DocumentRow.owner_id == owner_id)
    embedding_id = (
        select(func.min(EmbeddingConfigurationRow.id)).where(*configuration_filter(embedding)).scalar_subquery()
    )
    plans = (
        select(
            DocumentRow.id.label("document_id"),
            DocumentVersionRow.id.label("version_id"),
            DocumentVersionRow.content_sha256,
            ChunkPlanReadinessRow.chunking_configuration_id.label("configuration_id"),
        )
        .select_from(DocumentRow)
        .join(DocumentVersionRow, DocumentVersionRow.id == DocumentRow.current_version_id)
        .join(
            ChunkPlanReadinessRow,
            and_(
                ChunkPlanReadinessRow.document_version_id == DocumentVersionRow.id,
                ChunkPlanReadinessRow.embedding_configuration_id == embedding_id,
            ),
        )
        .join(
            ChunkingConfigurationRow,
            ChunkingConfigurationRow.id == ChunkPlanReadinessRow.chunking_configuration_id,
        )
        .where(
            DocumentRow.deleted_at.is_(None),
            DocumentVersionRow.document_id == DocumentRow.id,
            visibility,
            *(getattr(ChunkingConfigurationRow, key) == value for key, value in values.items()),
        )
        .cte("membership_ready_plans")
        .prefix_with("MATERIALIZED")
    )
    chunk_text = _chunk_text(ContentRow.text, ChunkMembershipRow.start, ChunkMembershipRow.end)
    memberships = (
        select(
            plans.c.document_id,
            plans.c.version_id,
            plans.c.content_sha256,
            plans.c.configuration_id,
            ChunkMembershipRow.ordinal,
            ChunkMembershipRow.start,
            ChunkMembershipRow.end,
            func.char_length(ContentRow.text).label("content_length"),
            (func.btrim(chunk_text, PYTHON_WHITESPACE) != "").label("eligible"),
            MembershipChunkEmbeddingRow.embedding_configuration_id.label("owned_embedding_id"),
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
            MembershipChunkEmbeddingRow,
            and_(
                MembershipChunkEmbeddingRow.document_version_id == ChunkMembershipRow.document_version_id,
                MembershipChunkEmbeddingRow.chunking_configuration_id == ChunkMembershipRow.chunking_configuration_id,
                MembershipChunkEmbeddingRow.ordinal == ChunkMembershipRow.ordinal,
                MembershipChunkEmbeddingRow.embedding_configuration_id == embedding_id,
            ),
        )
        .cte("membership_embedding_coverage")
        .prefix_with("MATERIALIZED")
    )
    m = memberships.c
    complete_plans = (
        select(m.version_id, m.configuration_id)
        .group_by(m.version_id, m.configuration_id)
        .having(
            func.count().filter(m.eligible) > 0,
            func.count().filter(m.eligible & m.owned_embedding_id.is_not(None)) == func.count().filter(m.eligible),
        )
        .cte("complete_membership_embedding_plans")
    )
    occurrences = (
        select(
            m.document_id,
            m.version_id,
            m.content_sha256,
            m.configuration_id,
            m.ordinal,
            m.start,
            m.end,
            m.content_length,
            embedding_id.label("embedding_id"),
            func.row_number()
            .over(
                partition_by=(m.content_sha256, m.start, m.end),
                order_by=(m.document_id, m.version_id),
            )
            .label("occurrence_rank"),
        )
        .select_from(memberships)
        .join(
            complete_plans,
            and_(
                complete_plans.c.version_id == m.version_id,
                complete_plans.c.configuration_id == m.configuration_id,
            ),
        )
        .where(m.eligible)
        .cte("membership_owned_occurrences")
    )
    return select(occurrences).where(occurrences.c.occurrence_rank == 1).cte("membership_owned_representatives")


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


def _hnsw_candidates(vector, embedding_profile, candidate_limit):
    if candidate_limit is None:
        raise ValueError("HNSW retrieval requires a candidate limit")
    distance = ChunkEmbeddingRow.embedding.cosine_distance(list(vector))
    return (
        select(
            ChunkEmbeddingRow.embedding_configuration_id,
            ChunkEmbeddingRow.content_sha256,
            ChunkEmbeddingRow.start,
            ChunkEmbeddingRow.end,
            distance.label("distance"),
        )
        .where(
            ChunkEmbeddingRow.embedding_configuration_id.in_(
                select(EmbeddingConfigurationRow.id).where(*configuration_filter(embedding_profile))
            )
        )
        # Keep this ordering expression as the leading HNSW index key.
        .order_by(distance)
        .limit(candidate_limit)
        .cte("hnsw_candidates")
        .prefix_with("MATERIALIZED")
    )


def _semantic_statement(
    members,
    vector,
    limit,
    embedding_backend="legacy_shared",
    *,
    semantic_backend="exact",
    candidate_limit=None,
    embedding_profile=None,
    candidate_rows=None,
):
    member_columns = members.c
    if embedding_backend == "membership_owned":
        score = 1.0 - MembershipChunkEmbeddingRow.embedding.cosine_distance(list(vector))
        return (
            _candidate_statement(members, score, 1.0 - score, "cosine_similarity")
            .join(
                MembershipChunkEmbeddingRow,
                and_(
                    MembershipChunkEmbeddingRow.document_version_id == member_columns.version_id,
                    MembershipChunkEmbeddingRow.chunking_configuration_id == member_columns.configuration_id,
                    MembershipChunkEmbeddingRow.ordinal == member_columns.ordinal,
                    MembershipChunkEmbeddingRow.embedding_configuration_id == member_columns.embedding_id,
                ),
            )
            .limit(limit)
        )
    if embedding_backend != "legacy_shared":
        raise ValueError("Unsupported embedding backend")
    if semantic_backend == "hnsw":
        candidates = (
            candidate_rows
            if candidate_rows is not None
            else _hnsw_candidates(vector, embedding_profile, candidate_limit)
        )
        return (
            _candidate_statement(members, 1.0 - candidates.c.distance, candidates.c.distance, "cosine_similarity")
            .join(
                candidates,
                and_(
                    candidates.c.embedding_configuration_id == member_columns.embedding_id,
                    candidates.c.content_sha256 == member_columns.content_sha256,
                    candidates.c.start == member_columns.start,
                    candidates.c.end == member_columns.end,
                ),
            )
            .limit(limit)
        )
    if semantic_backend != "exact":
        raise ValueError("Unsupported semantic search backend")
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


def _search_statement(
    vector,
    chunking,
    embedding,
    owner_id,
    limit,
    eligibility_backend="reference",
    embedding_backend="legacy_shared",
    semantic_backend="exact",
    candidate_limit=None,
):
    candidates = _hnsw_candidates(vector, embedding, candidate_limit) if semantic_backend == "hnsw" else None
    if embedding_backend == "membership_owned":
        members = _membership_owned_memberships(chunking, embedding, owner_id)
    elif embedding_backend == "legacy_shared":
        members = _memberships(chunking, embedding, owner_id, eligibility_backend, candidates)
    else:
        raise ValueError("Unsupported embedding backend")
    return _hydrate(
        _semantic_statement(
            members,
            vector,
            limit,
            embedding_backend,
            semantic_backend=semantic_backend,
            candidate_limit=candidate_limit,
            embedding_profile=embedding,
            candidate_rows=candidates,
        )
    )


def _hybrid_statement(
    vector,
    question,
    chunking,
    embedding,
    owner_id,
    semantic_limit,
    keyword_limit,
    lexical_backend="postgres_fts",
    eligibility_backend="reference",
    embedding_backend="legacy_shared",
    semantic_backend="exact",
    candidate_limit=None,
):
    candidates = _hnsw_candidates(vector, embedding, candidate_limit) if semantic_backend == "hnsw" else None
    if embedding_backend == "membership_owned":
        semantic_members = _membership_owned_memberships(chunking, embedding, owner_id)
        # Lexical search retains the existing legacy eligibility path.
        lexical_members = _memberships(chunking, embedding, owner_id, eligibility_backend)
    else:
        lexical_members = _memberships(chunking, embedding, owner_id, eligibility_backend)
        semantic_members = (
            _memberships(chunking, embedding, owner_id, eligibility_backend, candidates, "_hnsw")
            if candidates is not None
            else lexical_members
        )
    hits = union_all(
        _semantic_statement(
            semantic_members,
            vector,
            semantic_limit,
            embedding_backend,
            semantic_backend=semantic_backend,
            candidate_limit=candidate_limit,
            embedding_profile=embedding,
            candidate_rows=candidates,
        ),
        (
            _bm25_statement(lexical_members, question, keyword_limit)
            if lexical_backend == "bm25"
            else _keyword_statement(lexical_members, question, keyword_limit)
        ),
    )
    return _hydrate(hits)


def _memberships(chunking, embedding, owner_id, eligibility_backend, candidate_keys=None, suffix=""):
    if eligibility_backend == "reference":
        return _eligible_memberships(chunking, embedding, owner_id, candidate_keys, suffix)
    if eligibility_backend == "readiness":
        return _ready_memberships(chunking, embedding, owner_id, candidate_keys, suffix)
    raise ValueError("Unsupported eligibility backend")


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
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        lexical_backend: str = "postgres_fts",
        eligibility_backend: str = "reference",
        embedding_backend: str = "legacy_shared",
        semantic_backend: str = "exact",
        hnsw_ef_search: int = 400,
        hnsw_candidate_limits: tuple[int, ...] = (400,),
        hnsw_iterative_scan: bool = False,
    ) -> None:
        if lexical_backend not in ("postgres_fts", "bm25"):
            raise ValueError("Unsupported lexical search backend")
        if eligibility_backend not in ("reference", "readiness"):
            raise ValueError("Unsupported eligibility backend")
        if embedding_backend not in ("legacy_shared", "membership_owned"):
            raise ValueError("Unsupported embedding backend")
        if semantic_backend not in ("exact", "hnsw"):
            raise ValueError("Unsupported semantic search backend")
        if semantic_backend == "hnsw" and embedding_backend != "legacy_shared":
            raise ValueError("HNSW semantic retrieval requires the legacy shared-vector backend")
        if (
            not 1 <= hnsw_ef_search <= 1000
            or not hnsw_candidate_limits
            or any(not 1 <= value <= 100000 for value in hnsw_candidate_limits)
        ):
            raise ValueError("HNSW search settings must be positive")
        if tuple(sorted(set(hnsw_candidate_limits))) != hnsw_candidate_limits:
            raise ValueError("HNSW candidate limits must be strictly increasing")
        if semantic_backend == "hnsw" and not hnsw_iterative_scan and max(hnsw_candidate_limits) > hnsw_ef_search:
            raise ValueError("HNSW candidate limits cannot exceed ef_search with iterative scanning disabled")
        self._sessions = sessions
        self._lexical_backend = lexical_backend
        self._eligibility_backend = eligibility_backend
        self._embedding_backend = embedding_backend
        self._semantic_backend = semantic_backend
        self._hnsw_ef_search = hnsw_ef_search
        self._hnsw_candidate_limits = hnsw_candidate_limits
        self._hnsw_iterative_scan = hnsw_iterative_scan

    async def validate_backend(self) -> None:
        """Fail closed when selected retrieval schema prerequisites are absent."""
        if self._semantic_backend == "hnsw":
            try:
                async with self._sessions() as session:
                    async with session.begin():
                        available = await session.scalar(
                            text("""
                            SELECT EXISTS (
                                SELECT 1
                                FROM pg_catalog.pg_index i
                                JOIN pg_catalog.pg_class t ON t.oid = i.indrelid
                                JOIN pg_catalog.pg_namespace n ON n.oid = t.relnamespace
                                JOIN pg_catalog.pg_class x ON x.oid = i.indexrelid
                                JOIN pg_catalog.pg_am a ON a.oid = x.relam
                                WHERE n.nspname = 'public' AND t.relname = 'chunk_embeddings'
                                  AND a.amname = 'hnsw' AND i.indisvalid AND i.indisready AND i.indislive
          AND i.indpred IS NULL AND i.indexprs IS NULL AND i.indnatts = 1
          AND i.indkey[0] = (SELECT attnum FROM pg_catalog.pg_attribute
              WHERE attrelid=t.oid AND attname='embedding' AND NOT attisdropped)
                                  AND pg_catalog.pg_get_indexdef(i.indexrelid)
                                      ILIKE '%USING hnsw (embedding vector_cosine_ops)%'
                                  AND x.reloptions @> ARRAY['m=16', 'ef_construction=64']
                            )
                        """)
                        )
                if not available:
                    raise RuntimeError("HNSW retrieval requires a valid cosine index on chunk_embeddings")
            except SQLAlchemyError as error:
                raise RuntimeError("HNSW backend validation failed; verify the HNSW migration") from error
        if self._embedding_backend == "membership_owned":
            try:
                async with self._sessions() as session:
                    async with session.begin():
                        available = await session.scalar(
                            text("SELECT pg_catalog.to_regclass('public.membership_chunk_embeddings') IS NOT NULL")
                        )
                if not available:
                    raise RuntimeError("Membership-owned retrieval requires migration 0007")
            except SQLAlchemyError as error:
                raise RuntimeError("Membership-owned retrieval validation failed; verify migration 0007") from error
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
        if self._semantic_backend == "hnsw":
            rows = await self._read_hnsw_retries(
                lambda candidate_limit: _search_statement(
                    vector,
                    chunking,
                    embedding,
                    owner_id,
                    limit,
                    eligibility_backend=self._eligibility_backend,
                    embedding_backend=self._embedding_backend,
                    semantic_backend="hnsw",
                    candidate_limit=candidate_limit,
                ),
                required_limit=limit,
                stage="semantic_sql_roundtrip",
            )
        else:
            statement = _search_statement(
                vector,
                chunking,
                embedding,
                owner_id,
                limit,
                eligibility_backend=self._eligibility_backend,
                embedding_backend=self._embedding_backend,
            )
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
        members = _memberships(chunking, embedding, owner_id, self._eligibility_backend)
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

        def statement_factory(candidate_limit):
            return _hybrid_statement(
                vector,
                question,
                chunking,
                embedding,
                owner_id,
                semantic_limit,
                keyword_limit,
                self._lexical_backend,
                self._eligibility_backend,
                self._embedding_backend,
                semantic_backend="hnsw" if self._semantic_backend == "hnsw" else "exact",
                candidate_limit=candidate_limit,
            )

        rows = (
            await self._read_hnsw_retries(
                statement_factory,
                required_limit=semantic_limit,
                stage="hybrid_sql_roundtrip",
                channel="cosine_similarity",
            )
            if self._semantic_backend == "hnsw"
            else await self._read(statement_factory(None), "hybrid_sql_roundtrip")
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

    async def _read_hnsw_retries(self, statement_factory, *, required_limit, stage, channel=None):
        """Retry bounded ANN candidates in one snapshot, always reapplying eligibility."""
        try:
            async with self._sessions() as session:
                async with session.begin():
                    with stage_timing("connection_acquisition"):
                        await session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
                    rows = []
                    candidate_limit = self._hnsw_candidate_limits[-1]
                    for candidate_limit in self._hnsw_candidate_limits:
                        await session.execute(
                            text("SELECT set_config('hnsw.ef_search', :value, true)"),
                            {"value": str(self._hnsw_ef_search)},
                        )
                        await session.execute(
                            text("SELECT set_config('hnsw.iterative_scan', :value, true)"),
                            {"value": "strict_order" if self._hnsw_iterative_scan else "off"},
                        )
                        with stage_timing(stage):
                            rows = (await session.execute(statement_factory(candidate_limit))).mappings().all()
                        eligible_count = sum(row["channel"] == channel for row in rows) if channel else len(rows)
                        if eligible_count >= required_limit:
                            break
            eligible_count = sum(row["channel"] == channel for row in rows) if channel else len(rows)
            if eligible_count < required_limit:
                logger.warning(
                    "HNSW candidate retries exhausted: requested=%d eligible=%d final_candidate_limit=%d",
                    required_limit,
                    eligible_count,
                    candidate_limit,
                )
            return rows
        except SQLAlchemyError as error:
            raise RepositoryError("Evidence search could not be completed") from error
