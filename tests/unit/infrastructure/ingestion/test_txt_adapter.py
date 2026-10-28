import pytest

from investigation_engine.infrastructure.ingestion.txt_adapter import extract_txt


def test_txt_preserves_original_newlines_unicode_and_line_locators(tmp_path):
    path = tmp_path / "source.txt"
    path.write_bytes("A😀\r\n\r\nlast\n".encode("utf-8"))
    document = extract_txt(path, source_id="upload-1")
    assert document.text == "A😀\r\n\r\nlast\n"
    assert [document.text[s.start:s.end] for s in document.source_spans] == ["A😀\r\n", "\r\n", "last\n"]
    assert [s.locators[0].index for s in document.source_spans] == [1, 2, 3]
    assert document.source.source_id == "upload-1"


def test_empty_txt_is_preserved_and_missing_files_are_not_substituted(tmp_path):
    path = tmp_path / "empty.txt"
    path.write_bytes(b"")
    document = extract_txt(path, source_id="empty")
    assert document.text == "" and document.source_spans == ()
    with pytest.raises(FileNotFoundError):
        extract_txt(tmp_path / "missing.txt", source_id="missing")
