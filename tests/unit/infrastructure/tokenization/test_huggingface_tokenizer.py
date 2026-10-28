from hashlib import sha256

import pytest
from tokenizers import Tokenizer, models, pre_tokenizers

from investigation_engine.infrastructure.tokenization.huggingface_tokenizer import HuggingFaceTokenizer


def test_file_adapter_checks_identity_and_disables_padding_and_truncation(tmp_path):
    backend = Tokenizer(models.WordLevel({"[UNK]": 0, "hello": 1, "😀": 2}, unk_token="[UNK]"))
    backend.pre_tokenizer = pre_tokenizers.WhitespaceSplit()
    backend.enable_truncation(max_length=1)
    backend.enable_padding(length=8, pad_id=0, pad_token="[UNK]")
    path = tmp_path / "tokenizer.json"
    path.write_text(backend.to_str(), encoding="utf-8")
    digest = sha256(path.read_bytes()).hexdigest()
    adapter = HuggingFaceTokenizer(path, name="synthetic", revision="1", expected_sha256=digest)
    assert adapter.offsets("hello 😀 hello") == ((0, 5), (6, 7), (8, 13))
    with pytest.raises(ValueError, match="pinned identity"):
        HuggingFaceTokenizer(path, name="synthetic", revision="1", expected_sha256="0" * 64)
    with pytest.raises(FileNotFoundError):
        HuggingFaceTokenizer(tmp_path / "absent", name="synthetic", revision="1", expected_sha256=digest)
