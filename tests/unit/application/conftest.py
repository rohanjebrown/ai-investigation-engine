import pytest

from investigation_engine.application.ports.text_tokenizer import TokenizerIdentity


@pytest.fixture
def tokenizer():
    class CharacterTokenizer:
        """One token per Python character, making expected ranges transparent."""
        identity = TokenizerIdentity(name="characters", revision="1", artifact_sha256="0" * 64, runtime_version="test")

        def offsets(self, text):
            return tuple((i, i + 1) for i in range(len(text)))
    return CharacterTokenizer()
