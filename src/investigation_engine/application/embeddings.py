"""Embedding identity, eligibility and the current storage representation."""

from dataclasses import dataclass
from math import hypot, isfinite
from struct import pack, unpack

from investigation_engine.application.chunking import Chunk

type EmbeddingVector = tuple[float, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class EmbeddingConfig:
    provider: str
    model_id: str
    model_revision: str | None = None
    dimensions: int = 768
    document_format: str = "gemini_no_title_v1"
    normalization: str = "none"

    def __post_init__(self) -> None:
        for value in (self.provider, self.model_id):
            if not isinstance(value, str) or not value.strip():
                raise ValueError("Embedding provider/model must be nonblank identifiers")
        if self.model_revision is not None and (
            not isinstance(self.model_revision, str) or not self.model_revision.strip()
        ):
            raise ValueError("Model revision must be absent or nonblank")
        if type(self.dimensions) is not int or self.dimensions != 768:
            raise ValueError("Embedding storage requires 768 dimensions")
        if self.document_format != "gemini_no_title_v1" or self.normalization != "none":
            raise ValueError("Unsupported document embedding encoding configuration")


def embedding_eligible(chunk: Chunk) -> bool:
    """Eligibility never changes the original text or complete plan membership."""
    return bool(chunk.text.strip())


def storage_vector(values: tuple[float, ...] | list[float]) -> EmbeddingVector:
    """Validate and round to float32, without mathematical normalization."""
    if not isinstance(values, (tuple, list)) or len(values) != 768:
        raise ValueError("An embedding must have 768 numeric components")
    if any(type(value) not in (float, int) for value in values):
        raise ValueError("Embedding components must be finite numbers")
    try:
        vector = tuple(float(value) for value in values)
        if not all(isfinite(value) for value in vector):
            raise ValueError("Non-finite embedding component")
        result = unpack("!768f", pack("!768f", *vector))
    except (OverflowError, ValueError) as error:
        raise ValueError("Embedding cannot be represented as finite float32 values") from error
    if not all(isfinite(value) for value in result) or hypot(*result) == 0:
        raise ValueError("Stored embedding must have a finite nonzero float32 norm")
    return result
