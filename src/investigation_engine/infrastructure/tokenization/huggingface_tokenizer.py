"""Load an explicitly supplied tokenizer artifact; never download or infer."""

import hashlib
from pathlib import Path

from tokenizers import Tokenizer, __version__

from investigation_engine.application.ports.text_tokenizer import TokenizerIdentity


# Experimental reference only; these do not select an embedding provider.
VOYAGE_REFERENCE_NAME = "MongoDB/voyage-4"
VOYAGE_REFERENCE_REVISION = "44f3b2ae4ddf33403ed4dd66bec3fa48ff7dbbf9"
VOYAGE_REFERENCE_SHA256 = "c0382117ea329cdf097041132f6d735924b697924d6f6fc3945713e96ce87539"


class HuggingFaceTokenizer:
    def __init__(
        self,
        path: Path,
        *,
        name: str,
        revision: str,
        expected_sha256: str,
    ) -> None:
        artifact = path.read_bytes()
        digest = hashlib.sha256(artifact).hexdigest()
        if digest != expected_sha256:
            raise ValueError("Tokenizer artifact SHA-256 does not match its pinned identity")
        self._identity = TokenizerIdentity(
            name=name,
            revision=revision,
            artifact_sha256=digest,
            runtime_version=__version__,
        )
        self._tokenizer = Tokenizer.from_str(artifact.decode("utf-8"))
        self._tokenizer.no_truncation()
        self._tokenizer.no_padding()

    @property
    def identity(self) -> TokenizerIdentity:
        return self._identity

    def offsets(self, text: str) -> tuple[tuple[int, int], ...]:
        # Python bindings return original-character offsets, including when
        # several byte tokens share one non-ASCII character. Never decode IDs.
        encoding = self._tokenizer.encode(text, add_special_tokens=False)
        return tuple(encoding.offsets)
