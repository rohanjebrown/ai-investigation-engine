"""Embedding identity, eligibility and the current storage representation."""

from dataclasses import dataclass
from math import hypot, isfinite
from struct import pack, unpack

from investigation_engine.application.chunking import Chunk

type EmbeddingVector = tuple[float, ...]
EMBEDDING_DIMENSIONS = 768


@dataclass(frozen=True, slots=True, kw_only=True)
class EmbeddingConfig:
    provider: str
    model_id: str
    model_revision: str | None = None
    dimensions: int = EMBEDDING_DIMENSIONS
    document_format: str = "gemini_no_title_v1"
    normalization: str = "none"

    def __post_init__(self) -> None:
        _validate_embedding_identity(self.provider, self.model_id, self.model_revision)
        _validate_storage_profile(self.dimensions, self.document_format, self.normalization)


def embedding_eligible(chunk: Chunk) -> bool:
    """Eligibility never changes the original text or complete plan membership."""
    return bool(chunk.text.strip())


def storage_vector(values: tuple[float, ...] | list[float]) -> EmbeddingVector:  # noqa: WPS110 - public keyword retained
    """Validate and round to float32, without mathematical normalization."""
    _validate_vector_input(values)
    try:
        rounded_vector = _float32_storage_vector(values)
    except (OverflowError, ValueError) as error:
        raise ValueError("Embedding cannot be represented as finite float32 values") from error
    _validate_stored_vector(rounded_vector)
    return rounded_vector


def _validate_embedding_identity(provider: str, model_id: str, model_revision: str | None) -> None:
    if not _is_nonblank_identifier(provider) or not _is_nonblank_identifier(model_id):
        raise ValueError("Embedding provider/model must be nonblank identifiers")
    if model_revision is not None and not _is_nonblank_identifier(model_revision):
        raise ValueError("Model revision must be absent or nonblank")


def _is_nonblank_identifier(identifier: object) -> bool:
    return isinstance(identifier, str) and bool(identifier.strip())


def _validate_storage_profile(dimensions: int, document_format: str, normalization: str) -> None:
    # Reject bool, since bool subclasses int.
    if type(dimensions) is not int or dimensions != EMBEDDING_DIMENSIONS:  # noqa: WPS516
        raise ValueError(f"Embedding storage requires {EMBEDDING_DIMENSIONS} dimensions")
    if document_format != "gemini_no_title_v1" or normalization != "none":
        raise ValueError("Unsupported document embedding encoding configuration")


def _validate_vector_input(components: tuple[float, ...] | list[float]) -> None:
    if not isinstance(components, (tuple, list)) or len(components) != EMBEDDING_DIMENSIONS:
        raise ValueError(f"An embedding must have {EMBEDDING_DIMENSIONS} numeric components")
    # Reject bool values even though bool subclasses int.
    if any(type(component) not in (float, int) for component in components):  # noqa: WPS516
        raise ValueError("Embedding components must be finite numbers")


def _float32_storage_vector(components: tuple[float, ...] | list[float]) -> EmbeddingVector:
    vector = tuple(float(component) for component in components)
    if not all(isfinite(component) for component in vector):
        raise ValueError("Non-finite embedding component")
    struct_format = f"!{EMBEDDING_DIMENSIONS}f"
    return unpack(struct_format, pack(struct_format, *vector))


def _validate_stored_vector(rounded_vector: EmbeddingVector) -> None:
    # Keep finiteness and nonzero-norm requirements on the stored vector together.
    if not _finite_nonzero_norm(rounded_vector):
        raise ValueError("Stored embedding must have a finite nonzero float32 norm")


def _finite_nonzero_norm(vector: EmbeddingVector) -> bool:
    if not all(isfinite(component) for component in vector):
        return False
    return hypot(*vector) != 0
