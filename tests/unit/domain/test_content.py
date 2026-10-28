import hashlib
from dataclasses import FrozenInstanceError

import pytest

from investigation_engine.domain.content import Content


def test_exact_utf8_identity_preserves_whitespace_and_unicode():
    text = " é\r\n😀 "
    content = Content(text)
    assert content.sha256 == hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert content == Content(text)
    assert len({Content(text).sha256, Content(text.strip()).sha256, Content(" e\u0301\r\n😀 ").sha256}) == 3
    with pytest.raises(FrozenInstanceError):
        content.text = "would invalidate digest"


def test_empty_content_has_the_standard_empty_digest():
    assert Content("").sha256 == hashlib.sha256(b"").hexdigest()
