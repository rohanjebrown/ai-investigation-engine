from dataclasses import replace
from uuid import UUID

import pytest

from investigation_engine.application.retrieval import Evidence
from investigation_engine.application.rrf import reciprocal_rank_fusion
from investigation_engine.domain.provenance import SourceKind, SourceReference


def hit(number, *, channel="semantic", score=.8, start=0, end=1):
    return Evidence(document_id=UUID(int=number), document_version_id=UUID(int=number + 100),
        content_sha256="0" * 64, start=start, end=end, text="x" * (end-start),
        source=SourceReference(kind=SourceKind.DATASET, source_id=f"source-{number}", record_locator={"record": number}),
        source_spans=(), rank=99, score=score,
        score_kind="cosine_similarity" if channel == "semantic" else "keyword_rank")


def test_hand_calculated_rrf_example_uses_channel_positions_not_incoming_rank_fields():
    a, b = hit(1), hit(2)
    semantic = (a, hit(3), hit(4), b)
    keyword = tuple(hit(n, channel="keyword") for n in (11,12,13,2,14,15,16,17,18,1))
    fused = reciprocal_rank_fusion(semantic, keyword, limit=10)
    assert [item.identity for item in fused[:2]] == [b.identity, a.identity]
    assert fused[0].score == pytest.approx(2 / 64)
    assert fused[1].score == pytest.approx(1 / 61 + 1 / 70)
    assert fused[0].semantic_rank == fused[0].keyword_rank == 4
    assert fused[1].semantic_rank == 1 and fused[1].keyword_rank == 10
    assert fused[0].score_kind == "rrf" and fused[0].semantic_score == .8 and fused[0].keyword_score == .8
    assert [item.rank for item in fused] == list(range(1,11))


def test_first_duplicate_contributes_once_without_changing_other_unique_ranks():
    a, b = hit(1), hit(2)
    result = reciprocal_rank_fusion((a, a, replace(a, score=.9), b), (), limit=10)
    assert len(result) == 2
    assert result[0].score == pytest.approx(1 / 61) and result[0].semantic_score == .8
    assert result[1].score == pytest.approx(1 / 62) and result[1].semantic_rank == 2
    assert result[0].keyword_rank is None and result[0].keyword_score is None


def test_empty_single_channels_ties_limits_and_zero_constant_are_deterministic():
    assert reciprocal_rank_fusion((), (), limit=1) == ()
    # Both first-ranked, same fused score: stable document/version/range tie keys.
    result = reciprocal_rank_fusion((hit(2),), (hit(1, channel="keyword", score=.1),), limit=1, k=0)
    assert result[0].document_id == UUID(int=1) and result[0].score == 1.0
    assert result[0].semantic_score is None and result[0].keyword_rank == 1


def test_shared_content_is_not_shared_occurrence_and_overlapping_ranges_remain_distinct():
    a, b = hit(1), hit(2)
    overlap = replace(a, start=1, end=2)
    result = reciprocal_rank_fusion((a,b,overlap), (replace(b, score_kind="keyword_rank"),), limit=10)
    assert len(result) == 3 and len({item.identity for item in result}) == 3
    assert result[0].identity == b.identity and result[0].source.source_id == "source-2"
    result[0].source.record_locator["modified"] = True
    assert "modified" not in b.source.record_locator


def test_conflicting_source_payload_under_same_identity_is_not_silently_merged():
    a = hit(1)
    forged = replace(a, score_kind="keyword_rank", source=replace(a.source, source_id="wrong-source"))
    with pytest.raises(ValueError, match="disagree"):
        reciprocal_rank_fusion((a,), (forged,), limit=5)


@pytest.mark.parametrize("k", [-1, True, 1.5, 10001])
def test_invalid_constants_are_rejected(k):
    with pytest.raises(ValueError, match="RRF k"):
        reciprocal_rank_fusion((), (), limit=1, k=k)


@pytest.mark.parametrize("limit", [0, True, 101])
def test_invalid_result_limits_are_rejected(limit):
    with pytest.raises(ValueError):
        reciprocal_rank_fusion((), (), limit=limit)


def test_fused_or_mislabelled_input_cannot_be_mistaken_for_cosine_or_keyword_scores():
    with pytest.raises(ValueError, match="score kind"):
        reciprocal_rank_fusion((), (hit(1),), limit=1)
