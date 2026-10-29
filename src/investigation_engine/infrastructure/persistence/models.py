"""Approved PostgreSQL storage mappings, independent of domain dataclasses.

These models provide no registration, authorization, activation, or deletion
workflow. Ownership must be assigned explicitly by trusted application code.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID as PythonUUID

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    MetaData,
    Text,
    UniqueConstraint,
    and_,
    text,
)
from sqlalchemy.dialects.postgresql import BYTEA, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention={"pk": "pk_%(table_name)s"})


class ContentRow(Base):
    __tablename__ = "contents"
    __table_args__ = (
        CheckConstraint("octet_length(sha256) = 32", name="ck_contents_sha256_length"),
        CheckConstraint(
            "sha256 = pg_catalog.sha256(pg_catalog.convert_to(text, 'UTF8'))",
            name="ck_contents_sha256_matches_text",
        ),
    )

    sha256: Mapped[bytes] = mapped_column(BYTEA, primary_key=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)

    versions: Mapped[list[DocumentVersionRow]] = relationship(
        back_populates="content", passive_deletes="all", lazy="raise"
    )


class DocumentRow(Base):
    __tablename__ = "documents"
    __table_args__ = (
        ForeignKeyConstraint(
            ["id", "current_version_id"],
            ["document_versions.document_id", "document_versions.id"],
            name="fk_documents_current_owned_version",
            use_alter=True,
        ),
    )

    # IDs and creation timestamps are supplied from the domain objects.
    id: Mapped[PythonUUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    owner_id: Mapped[str | None] = mapped_column(Text(collation="C"), nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_version_id: Mapped[PythonUUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    versions: Mapped[list[DocumentVersionRow]] = relationship(
        back_populates="document",
        foreign_keys=lambda: [DocumentVersionRow.document_id],
        passive_deletes="all",
        lazy="raise",
    )
    # A read relationship only: activation must set the pointer explicitly
    # through the future indexing/activation workflow, not ORM synchronization.
    current_version: Mapped[DocumentVersionRow | None] = relationship(
        primaryjoin=lambda: and_(
            DocumentRow.id == DocumentVersionRow.document_id,
            DocumentRow.current_version_id == DocumentVersionRow.id,
        ),
        foreign_keys=lambda: [DocumentRow.current_version_id],
        viewonly=True,
        uselist=False,
        lazy="raise",
    )


class DocumentVersionRow(Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        ForeignKeyConstraint(["document_id"], ["documents.id"], name="fk_document_versions_document"),
        ForeignKeyConstraint(["content_sha256"], ["contents.sha256"], name="fk_document_versions_content"),
        UniqueConstraint("document_id", "version_number", name="uq_document_versions_number"),
        UniqueConstraint("document_id", "id", name="uq_document_versions_owned_id"),
        UniqueConstraint(
            "kind",
            "source_id",
            "revision",
            "record_locator",
            name="uq_document_versions_source_occurrence",
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint("version_number > 0", name="ck_document_versions_positive_number"),
        CheckConstraint("kind IN ('upload', 'dataset')", name="ck_document_versions_kind"),
        CheckConstraint(
            "jsonb_typeof(record_locator) = 'object'",
            name="ck_document_versions_record_locator_object",
        ),
        CheckConstraint(
            "jsonb_typeof(metadata) = 'object'",
            name="ck_document_versions_metadata_object",
        ),
        CheckConstraint(
            "jsonb_typeof(structure) = 'array'",
            name="ck_document_versions_structure_array",
        ),
        CheckConstraint(
            "jsonb_typeof(source_spans) = 'array'",
            name="ck_document_versions_source_spans_array",
        ),
        Index("ix_document_versions_content_sha256", "content_sha256"),
    )

    id: Mapped[PythonUUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    document_id: Mapped[PythonUUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    content_sha256: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    kind: Mapped[str] = mapped_column(Text(collation="C"), nullable=False)
    source_id: Mapped[str] = mapped_column(Text(collation="C"), nullable=False)
    revision: Mapped[str | None] = mapped_column(Text(collation="C"), nullable=True)
    record_locator: Mapped[dict[str, Any]] = mapped_column(
        JSONB(none_as_null=True), nullable=False, server_default=text("'{}'::jsonb")
    )
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB(none_as_null=True),
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    structure: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB(none_as_null=True), nullable=False, server_default=text("'[]'::jsonb")
    )
    source_spans: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB(none_as_null=True), nullable=False, server_default=text("'[]'::jsonb")
    )

    document: Mapped[DocumentRow] = relationship(back_populates="versions", foreign_keys=[document_id], lazy="raise")
    content: Mapped[ContentRow] = relationship(back_populates="versions", lazy="raise")


class ChunkingConfigurationRow(Base):
    __tablename__ = "chunking_configurations"
    __table_args__ = (
        CheckConstraint(
            "strategy IN ('whole_passage', 'fixed_tokens', 'sentence_packing')",
            name="ck_chunking_configurations_strategy",
        ),
        CheckConstraint(
            "btrim(algorithm_version) <> '' AND btrim(tokenizer_name) <> '' "
            "AND btrim(tokenizer_revision) <> '' AND btrim(tokenizer_runtime_version) <> ''",
            name="ck_chunking_configurations_identifiers",
        ),
        CheckConstraint(
            "octet_length(tokenizer_artifact_sha256) = 32",
            name="ck_chunking_configurations_tokenizer_digest",
        ),
        CheckConstraint("max_tokens > 0", name="ck_chunking_configurations_budget"),
        CheckConstraint(
            "overlap >= 0 AND overlap < max_tokens AND (strategy = 'fixed_tokens' OR overlap = 0)",
            name="ck_chunking_configurations_overlap",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(always=True), primary_key=True)
    strategy: Mapped[str] = mapped_column(Text(collation="C"), nullable=False)
    algorithm_version: Mapped[str] = mapped_column(Text(collation="C"), nullable=False)
    tokenizer_name: Mapped[str] = mapped_column(Text(collation="C"), nullable=False)
    tokenizer_revision: Mapped[str] = mapped_column(Text(collation="C"), nullable=False)
    tokenizer_artifact_sha256: Mapped[bytes] = mapped_column(BYTEA, nullable=False)
    tokenizer_runtime_version: Mapped[str] = mapped_column(Text(collation="C"), nullable=False)
    max_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    overlap: Mapped[int] = mapped_column(Integer, nullable=False)


class EmbeddingConfigurationRow(Base):
    __tablename__ = "embedding_configurations"
    __table_args__ = (
        CheckConstraint(
            "btrim(provider) <> '' AND btrim(model_id) <> '' "
            "AND (model_revision IS NULL OR btrim(model_revision) <> '')",
            name="ck_embedding_configurations_identifiers",
        ),
        CheckConstraint("dimensions = 768", name="ck_embedding_configurations_dimensions"),
        CheckConstraint(
            "document_format = 'gemini_no_title_v1'",
            name="ck_embedding_configurations_document_format",
        ),
        CheckConstraint("normalization = 'none'", name="ck_embedding_configurations_normalization"),
    )

    id: Mapped[int] = mapped_column(Integer, Identity(always=True), primary_key=True)
    provider: Mapped[str] = mapped_column(Text(collation="C"), nullable=False)
    model_id: Mapped[str] = mapped_column(Text(collation="C"), nullable=False)
    model_revision: Mapped[str | None] = mapped_column(Text(collation="C"), nullable=True)
    dimensions: Mapped[int] = mapped_column(Integer, nullable=False)
    document_format: Mapped[str] = mapped_column(Text(collation="C"), nullable=False)
    normalization: Mapped[str] = mapped_column(Text(collation="C"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class VersionChunkPlanRow(Base):
    __tablename__ = "version_chunk_plans"
    __table_args__ = (
        ForeignKeyConstraint(
            ["document_version_id"],
            ["document_versions.id"],
            name="fk_version_chunk_plans_version",
        ),
        ForeignKeyConstraint(
            ["chunking_configuration_id"],
            ["chunking_configurations.id"],
            name="fk_version_chunk_plans_configuration",
        ),
        CheckConstraint(
            "expected_chunk_count >= 0",
            name="ck_version_chunk_plans_count",
        ),
    )

    document_version_id: Mapped[PythonUUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    chunking_configuration_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    expected_chunk_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    version: Mapped[DocumentVersionRow] = relationship(lazy="raise")
    configuration: Mapped[ChunkingConfigurationRow] = relationship(lazy="raise")
    memberships: Mapped[list[ChunkMembershipRow]] = relationship(
        back_populates="plan",
        order_by="ChunkMembershipRow.ordinal",
        passive_deletes="all",
        lazy="raise",
    )


class ChunkMembershipRow(Base):
    __tablename__ = "chunk_memberships"
    __table_args__ = (
        ForeignKeyConstraint(
            ["document_version_id", "chunking_configuration_id"],
            ["version_chunk_plans.document_version_id", "version_chunk_plans.chunking_configuration_id"],
            name="fk_chunk_memberships_plan",
        ),
        UniqueConstraint(
            "document_version_id",
            "chunking_configuration_id",
            "start",
            "end",
            name="uq_chunk_memberships_range",
        ),
        CheckConstraint("ordinal >= 0", name="ck_chunk_memberships_ordinal"),
        CheckConstraint('start >= 0 AND "end" > start', name="ck_chunk_memberships_range"),
    )

    document_version_id: Mapped[PythonUUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    chunking_configuration_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ordinal: Mapped[int] = mapped_column(Integer, primary_key=True)
    start: Mapped[int] = mapped_column(Integer, nullable=False)
    end: Mapped[int] = mapped_column(Integer, nullable=False)

    plan: Mapped[VersionChunkPlanRow] = relationship(back_populates="memberships", lazy="raise")


class ChunkEmbeddingRow(Base):
    __tablename__ = "chunk_embeddings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["embedding_configuration_id"],
            ["embedding_configurations.id"],
            name="fk_chunk_embeddings_configuration",
        ),
        ForeignKeyConstraint(
            ["content_sha256"],
            ["contents.sha256"],
            name="fk_chunk_embeddings_content",
        ),
        CheckConstraint('start >= 0 AND "end" > start', name="ck_chunk_embeddings_range"),
        CheckConstraint("vector_norm(embedding) > 0", name="ck_chunk_embeddings_nonzero"),
    )

    embedding_configuration_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    content_sha256: Mapped[bytes] = mapped_column(BYTEA, primary_key=True)
    start: Mapped[int] = mapped_column(Integer, primary_key=True)
    end: Mapped[int] = mapped_column(Integer, primary_key=True)
    embedding: Mapped[list[float]] = mapped_column(VECTOR(768), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    configuration: Mapped[EmbeddingConfigurationRow] = relationship(lazy="raise")
    content: Mapped[ContentRow] = relationship(lazy="raise")


class SearchableChunkRow(Base):
    __tablename__ = "searchable_chunks"
    __table_args__ = (
        CheckConstraint('start >= 0 AND "end" > start', name="ck_searchable_chunks_range"),
        ForeignKeyConstraint(["content_sha256"], ["contents.sha256"], name="fk_searchable_chunks_content"),
    )

    content_sha256: Mapped[bytes] = mapped_column(BYTEA, primary_key=True)
    start: Mapped[int] = mapped_column(Integer, primary_key=True)
    end: Mapped[int] = mapped_column(Integer, primary_key=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
