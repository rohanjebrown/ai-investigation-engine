import pytest

from investigation_engine.domain.provenance import SourceKind, SourceReference


def test_provenance_copies_nested_inputs_without_claiming_deep_immutability():
    locator = {"original": {"indices": [0, 1]}}
    metadata = {"tags": ["original"]}
    source = SourceReference(kind=SourceKind.DATASET, source_id="dataset", record_locator=locator, metadata=metadata)
    locator["original"]["indices"].append(2)
    metadata["tags"].clear()
    assert source.record_locator == {"original": {"indices": [0, 1]}}
    assert source.metadata == {"tags": ["original"]}
    other = SourceReference(kind=SourceKind.DATASET, source_id="other")
    source.metadata["mutable-by-contract"] = True
    assert other.metadata == {}


def test_blank_source_identity_is_rejected():
    with pytest.raises(ValueError, match="Source ID"):
        SourceReference(kind=SourceKind.UPLOAD, source_id=" ")
