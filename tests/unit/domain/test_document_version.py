from dataclasses import replace

import pytest

from investigation_engine.domain.document import Document
from investigation_engine.domain.source_span import LocatorKind, SourceLocator, SourceSpan
from investigation_engine.domain.structural_span import StructuralSpan, StructureKind


@pytest.mark.parametrize("owner", ["", "  ", 7])
def test_private_owner_requires_an_unambiguous_principal(owner):
    with pytest.raises(ValueError, match="Owner ID"):
        Document(owner_id=owner)


def test_ownership_cannot_be_implicitly_shared():
    with pytest.raises(TypeError, match="owner_id"):
        Document()


@pytest.mark.parametrize("number", [0, -1])
def test_version_numbers_must_be_positive(registration, number):
    _, version = registration()
    with pytest.raises(ValueError, match="positive"):
        replace(version, version_number=number)


@pytest.mark.parametrize("field", ["structure", "source_spans"])
def test_spans_cannot_reference_characters_beyond_content(registration, field):
    _, version = registration(body="abc")
    span = (
        StructuralSpan(kind=StructureKind.SENTENCE, start=0, end=4)
        if field == "structure"
        else SourceSpan(start=0, end=4, locators=(SourceLocator(kind=LocatorKind.SENTENCE, index=0),))
    )
    with pytest.raises(ValueError, match="exceeds content length"):
        replace(version, **{field: (span,)})


@pytest.mark.parametrize("start,end", [(-1, 1), (0, 0), (2, 1)])
def test_structural_and_source_spans_require_nonempty_forward_ranges(start, end):
    with pytest.raises(ValueError):
        StructuralSpan(kind=StructureKind.SENTENCE, start=start, end=end)
    with pytest.raises(ValueError):
        SourceSpan(start=start, end=end, locators=(SourceLocator(kind=LocatorKind.LINE, index=1),))


def test_source_spans_require_a_valid_original_locator():
    with pytest.raises(ValueError, match="at least one"):
        SourceSpan(start=0, end=1, locators=())
    with pytest.raises(ValueError, match="negative"):
        SourceLocator(kind=LocatorKind.SENTENCE, index=-1)
