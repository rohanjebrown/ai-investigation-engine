import pytest

from investigation_engine.application.ingestion.extracted_document import ExtractedDocument
from investigation_engine.application.ports.repository_errors import RegistrationConflict, RepositoryError
from investigation_engine.application.use_cases.register_document import RegisterDocument


async def test_use_case_returns_actual_stored_registration_and_inactive_candidates(registration):
    stored = registration(source_id="stored")
    _, source_version = registration(source_id="input")
    extracted = ExtractedDocument(text=source_version.content.text, source=source_version.source, title="Input title")

    class Repository:
        async def register(self, document, version):
            assert document.owner_id == "principal"
            assert document.current_version_id is None
            assert document.title == extracted.title
            assert version.document_id == document.id and version.version_number == 1
            assert version.content.text == extracted.text and version.source == extracted.source
            return stored

    assert await RegisterDocument(Repository()).execute(extracted, owner_id="principal") is stored
    with pytest.raises(TypeError, match="owner_id"):
        await RegisterDocument(Repository()).execute(extracted)


@pytest.mark.parametrize("error_type", [RegistrationConflict, PermissionError, RepositoryError])
async def test_repository_errors_propagate_without_becoming_success(registration, error_type):
    _, version = registration()
    extracted = ExtractedDocument(text=version.content.text, source=version.source)
    error = error_type("failure")

    class Repository:
        async def register(self, document, version):
            raise error

    with pytest.raises(error_type) as caught:
        await RegisterDocument(Repository()).execute(extracted, owner_id=None)
    assert caught.value is error
