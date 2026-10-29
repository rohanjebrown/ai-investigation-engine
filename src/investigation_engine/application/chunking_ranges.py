"""Pure token and sentence range algorithms for chunking plans."""

from investigation_engine.application.ports.text_tokenizer import TextTokenizer
from investigation_engine.domain.document_version import DocumentVersion
from investigation_engine.domain.structural_span import StructuralSpan, StructureKind

type CharacterRange = tuple[int, int]
type CharacterRanges = tuple[CharacterRange, ...]


def _offsets(text: str, tokenizer: TextTokenizer) -> CharacterRanges:
    offsets = tokenizer.offsets(text)
    previous_start = 0
    previous_end = 0
    for start, end in offsets:
        _validate_token_offset(text, start, end, previous_start, previous_end)
        previous_start, previous_end = start, end
    return offsets


def _validate_token_offset(text: str, start: int, end: int, previous_start: int, previous_end: int) -> None:
    if type(start) is not int or type(end) is not int:  # noqa: WPS516 - reject bool offsets
        raise ValueError("Tokenizer must return ordered Python-character offsets")
    # Chained comparisons keep token offsets in the canonical half-open range form.
    if not 0 <= start < end <= len(text):  # noqa: WPS228
        raise ValueError("Tokenizer must return ordered Python-character offsets")
    if start < previous_start or end < previous_end:
        raise ValueError("Tokenizer must return ordered Python-character offsets")


def _fixed_ranges(
    text: str,
    budget: int,
    overlap: int,
    tokenizer: TextTokenizer,
) -> CharacterRanges:
    ranges = []
    start = 0
    while start < len(text):
        end, chunk_offsets = _next_fitting_boundary(text, start, budget, tokenizer)
        ranges.append((start, end))
        if end == len(text):
            break
        if overlap:
            overlap_index = max(0, len(chunk_offsets) - overlap)
            # Keep token boundaries in original-character units. A byte-token
            # overlap can contain more than the target count at one character.
            following = start + chunk_offsets[overlap_index][0]
            start = following if following > start else end
        else:
            start = end
    return tuple(ranges)


def _next_fitting_boundary(
    text: str,
    start: int,
    budget: int,
    tokenizer: TextTokenizer,
) -> tuple[int, CharacterRanges]:
    """Choose the furthest original-character boundary within the token budget."""
    offsets = _offsets(text[start:], tokenizer)
    if len(offsets) <= budget:
        return len(text), offsets

    # Duplicate starts are possible when several tokens encode one emoji.
    candidate_ends = set()
    for token_start, _ in offsets[: budget + 1]:
        if token_start > 0:
            candidate_ends.add(start + token_start)
    for boundary in sorted(candidate_ends, reverse=True):
        chunk_offsets = _offsets(text[start:boundary], tokenizer)
        if len(chunk_offsets) <= budget:
            return boundary, chunk_offsets
    raise ValueError(
        f"Token budget {budget} cannot fit an indivisible character or token boundary at input offset {start}"
    )


def _sentence_ranges(
    version: DocumentVersion,
    budget: int,
    tokenizer: TextTokenizer,
) -> CharacterRanges:
    text = version.content.text
    sentences = tuple(sentence for sentence in version.structure if sentence.kind == StructureKind.SENTENCE)
    if not _sentence_spans_cover_content(text, sentences):
        return _fixed_ranges(text, budget, 0, tokenizer)
    return _pack_sentence_units(text, sentences, budget, tokenizer)


def _sentence_spans_cover_content(text: str, sentences: tuple[StructuralSpan, ...]) -> bool:
    """Validate sentence ranges and report whether they cover all non-whitespace text."""
    previous_end = 0
    missing_text = not sentences
    for sentence in sentences:
        _validate_sentence_span(text, sentence, previous_end)
        if text[previous_end : sentence.start].strip():
            missing_text = True
        previous_end = sentence.end
    if text[previous_end:].strip():
        missing_text = True
    return not missing_text


def _validate_sentence_span(text: str, sentence: StructuralSpan, previous_end: int) -> None:
    # Chained comparisons keep sentence spans in the canonical half-open range form.
    if not 0 <= sentence.start < sentence.end <= len(text):  # noqa: WPS228
        raise ValueError("Sentence spans must be ordered, nonoverlapping Content ranges")
    if sentence.start < previous_end:
        raise ValueError("Sentence spans must be ordered, nonoverlapping Content ranges")


def _pack_sentence_units(
    text: str,
    sentences: tuple[StructuralSpan, ...],
    budget: int,
    tokenizer: TextTokenizer,
) -> CharacterRanges:
    """Pack adjacent sentence units, splitting only units over budget."""
    ranges = []
    start = 0
    end = 0
    for index, _sentence in enumerate(sentences):
        # Assign the separator to the preceding sentence's packing unit.
        # This changes neither authoritative spans nor original source indices.
        unit_end = _sentence_unit_end(sentences, index, len(text))
        if _fits_token_budget(text[start:unit_end], budget, tokenizer):
            end = unit_end
            continue
        if end > start:
            ranges.append((start, end))
            start = end
        if _fits_token_budget(text[start:unit_end], budget, tokenizer):
            end = unit_end
            continue
        ranges.extend(
            (start + chunk_start, start + chunk_end)
            for chunk_start, chunk_end in _fixed_ranges(text[start:unit_end], budget, 0, tokenizer)
        )
        start = unit_end
        end = unit_end
    if end > start:
        ranges.append((start, end))
    return tuple(ranges)


def _fits_token_budget(text: str, budget: int, tokenizer: TextTokenizer) -> bool:
    return len(_offsets(text, tokenizer)) <= budget


def _sentence_unit_end(sentences: tuple[StructuralSpan, ...], index: int, text_length: int) -> int:
    if index + 1 < len(sentences):
        return sentences[index + 1].start
    return text_length
