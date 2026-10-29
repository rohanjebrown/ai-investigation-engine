"""The one tokenization boundary needed by deterministic text chunking."""

from dataclasses import dataclass
from typing import Protocol

SHA256_HEX_LENGTH = 64


@dataclass(frozen=True, slots=True, kw_only=True)
class TokenizerIdentity:
    name: str
    revision: str
    artifact_sha256: str
    runtime_version: str

    def __post_init__(self) -> None:
        for identity_part in (self.name, self.revision, self.runtime_version):
            if not isinstance(identity_part, str) or not identity_part.strip():
                raise ValueError("Tokenizer identity requires nonblank name, revision, and runtime")
        if (
            not isinstance(self.artifact_sha256, str)
            or len(self.artifact_sha256) != SHA256_HEX_LENGTH
            or any(hex_digit not in "0123456789abcdef" for hex_digit in self.artifact_sha256)
        ):
            raise ValueError("Tokenizer artifact digest must be a lowercase SHA-256 hex string")


class TextTokenizer(Protocol):
    @property
    def identity(self) -> TokenizerIdentity: ...

    def offsets(self, text: str) -> tuple[tuple[int, int], ...]:
        """Return one original Python-character range per content token.

        Encoding must not truncate, pad, or insert special tokens. Multiple
        tokens may overlap the same character (for example UTF-8 byte tokens).
        Offsets refer to the supplied text, never decoded or normalized text.
        This contract does not equate different models' token counts.
        """
        ...
