from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from investigation_engine.application.answers import (
    INSUFFICIENT_MESSAGE,
    AnswerStatus,
    GenerationResult,
    generation_result,
)
from investigation_engine.application.ports.generation_errors import (
    EvidenceContextLimitError,
    GenerationProviderError,
    GenerationResponseError,
    GenerationTimeoutError,
)
from investigation_engine.application.ports.repository_errors import RepositoryError
from investigation_engine.application.retrieval import Evidence
from investigation_engine.application.use_cases.answer_question import AnswerQuestion
from investigation_engine.domain.source_span import LocatorKind, SourceLocator, SourceSpan


@pytest.fixture
def rag(registration):
    def evidence(text="Paris is in France."):
        span = SourceSpan(start=0, end=len(text), locators=(SourceLocator(kind=LocatorKind.SENTENCE, index=0),))
        document, version = registration(body=text, source_spans=(span,))
        return Evidence(
            document_id=document.id,
            document_version_id=version.id,
            content_sha256=version.content.sha256,
            start=0,
            end=len(text),
            text=text,
            source=version.source,
            source_spans=version.source_spans,
            score=1.0,
            rank=1,
        )

    hits = (evidence(), evidence("France is in Europe."))
    search = SimpleNamespace(execute=AsyncMock(return_value=hits))
    generator = SimpleNamespace(
        generate=AsyncMock(
            return_value=GenerationResult(
                status=AnswerStatus.ANSWERED, answer="Paris is in France, in Europe. [E1] [E2]", citations=("E1", "E2")
            )
        )
    )
    return SimpleNamespace(
        search=search, generator=generator, hits=hits, evidence=evidence, service=AnswerQuestion(search, generator)
    )


async def test_grounded_answer_resolves_only_server_evidence_with_exact_locations(rag):
    result = await rag.service.execute("Where is Paris?", owner_id=None)
    assert result.status == AnswerStatus.ANSWERED and result.message is None
    assert [citation.evidence_id for citation in result.citations] == ["E1", "E2"]
    assert tuple(citation.evidence for citation in result.citations) == rag.hits
    rag.search.execute.assert_awaited_once_with("Where is Paris?", 5, owner_id=None)
    request = rag.generator.generate.await_args.args[0]
    assert request.question == "Where is Paris?"
    assert [item.text for item in request.evidence] == [hit.text for hit in rag.hits]
    assert "source_id" not in request.context_json() and "document_id" not in request.context_json()
    result.citations[0].evidence.source.metadata["modified"] = True
    assert "modified" not in rag.hits[0].source.metadata


async def test_empty_retrieval_abstains_without_generation(rag):
    rag.search.execute.return_value = ()
    result = await rag.service.execute("Unknown?", owner_id=None)
    assert result.status == AnswerStatus.INSUFFICIENT_EVIDENCE
    assert result.answer is None and result.citations == () and result.message == INSUFFICIENT_MESSAGE
    rag.generator.generate.assert_not_called()


async def test_explicit_model_abstention_is_not_forced_into_answer(rag):
    rag.generator.generate.return_value = GenerationResult(
        status=AnswerStatus.INSUFFICIENT_EVIDENCE, answer=None, citations=()
    )
    assert (await rag.service.execute("Unknown?", owner_id=None)).answer is None


@pytest.mark.parametrize(
    "citations,answer",
    [(("E99",), "Unsupported"), (("E2",), "Unknown inline [E99]"), (("E1",), "Malformed inline [E01]")],
)
async def test_unknown_and_unlisted_inline_citations_are_errors(rag, citations, answer):
    rag.generator.generate.return_value = GenerationResult(
        status=AnswerStatus.ANSWERED, answer=answer, citations=citations
    )
    with pytest.raises(GenerationResponseError):
        await rag.service.execute("q", owner_id=None)


@pytest.mark.parametrize("citations", [(), ("E1", "E1"), (" E1",), ("e1",), ("E0",), (1,), (["E1"],)])
def test_missing_duplicate_and_malformed_references_are_rejected(citations):
    with pytest.raises(GenerationResponseError):
        GenerationResult(status=AnswerStatus.ANSWERED, answer="Claim", citations=citations)


@pytest.mark.parametrize(
    "value",
    [
        None,
        [],
        {},
        {"status": "answered", "answer": "Claim", "citations": "E1"},
        {"status": "answered", "answer": "Claim", "citations": ["E1"], "source": "invented"},
        {"status": "unknown", "answer": None, "citations": []},
        {"status": "answered", "answer": " ", "citations": ["E1"]},
        {"status": "insufficient_evidence", "answer": "guess", "citations": []},
        {"status": "answered", "answer": "\ud800", "citations": ["E1"]},
    ],
)
def test_structured_result_rejects_coercion_extra_metadata_and_inconsistent_states(value):
    with pytest.raises(GenerationResponseError):
        generation_result(value)


async def test_provider_cannot_bypass_result_validation(rag):
    rag.generator.generate.return_value = {"answer": "unchecked"}
    with pytest.raises(GenerationResponseError, match="type"):
        await rag.service.execute("q", owner_id=None)
    forged = replace(GenerationResult(status=AnswerStatus.ANSWERED, answer="claim", citations=("E1",)))
    object.__setattr__(forged, "citations", ("E1", "E1"))
    rag.generator.generate.return_value = forged
    with pytest.raises(GenerationResponseError, match="duplicate"):
        await rag.service.execute("q", owner_id=None)


@pytest.mark.parametrize(
    "error",
    [GenerationProviderError("Unavailable"), GenerationTimeoutError("Timed out"), GenerationResponseError("Malformed")],
)
async def test_generation_failure_never_becomes_insufficient_evidence(rag, error):
    rag.generator.generate.side_effect = error
    with pytest.raises(type(error)):
        await rag.service.execute("q", owner_id=None)


async def test_retrieval_failure_never_calls_generator(rag):
    rag.search.execute.side_effect = RepositoryError("Failed")
    with pytest.raises(RepositoryError):
        await rag.service.execute("q", owner_id=None)
    rag.generator.generate.assert_not_called()


async def test_context_selection_is_ranked_bounded_and_never_clips_excerpts(rag):
    rag.search.execute.return_value = (
        rag.evidence("😀" * 500),
        rag.evidence(" Exact 😀 text.\n"),
        rag.evidence("x" * 500),
    )
    rag.generator.generate.return_value = GenerationResult(
        status=AnswerStatus.ANSWERED, answer="Claim [E1]", citations=("E1",)
    )
    service = AnswerQuestion(rag.search, rag.generator, max_evidence=3, max_context_chars=256)
    result = await service.execute("q", owner_id=None)
    request = rag.generator.generate.await_args.args[0]
    assert len(request.context_json()) <= 256 and len(request.evidence) == 1
    assert request.evidence[0].text == " Exact 😀 text.\n"
    assert result.citations[0].evidence.start == 0 and result.citations[0].evidence.end == len(" Exact 😀 text.\n")


async def test_context_budget_failure_is_distinct_from_no_evidence(rag):
    rag.search.execute.return_value = (rag.evidence("x" * 1000),)
    with pytest.raises(EvidenceContextLimitError):
        await AnswerQuestion(rag.search, rag.generator, max_context_chars=256).execute("q", owner_id=None)
    rag.generator.generate.assert_not_called()


async def test_evidence_count_and_exact_question_are_bounded_before_generation(rag):
    rag.generator.generate.return_value = GenerationResult(
        status=AnswerStatus.ANSWERED, answer="Claim", citations=("E1",)
    )
    await AnswerQuestion(rag.search, rag.generator, max_evidence=1).execute("  q?\n", owner_id="trusted")
    rag.search.execute.assert_awaited_once_with("  q?\n", 1, owner_id="trusted")
    assert len(rag.generator.generate.await_args.args[0].evidence) == 1


@pytest.mark.parametrize("question", ["", " \n", "x" * 4001, None, "\ud800"])
async def test_invalid_question_rejected_before_any_io(rag, question):
    with pytest.raises(ValueError):
        await rag.service.execute(question, owner_id=None)
    rag.search.execute.assert_not_called()
    rag.generator.generate.assert_not_called()
