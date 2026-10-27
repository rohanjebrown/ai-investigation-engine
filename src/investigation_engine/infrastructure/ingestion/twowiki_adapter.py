from investigation_engine.application.ingestion.extracted_document import (
    ExtractedDocument,
)
from investigation_engine.domain.provenance import SourceKind, SourceReference
from investigation_engine.domain.source_span import (
    LocatorKind,
    SourceLocator,
    SourceSpan,
)
from investigation_engine.domain.structural_span import (
    StructuralSpan,
    StructureKind,
)


DATASET_ID = "xanhho/2WikiMultihopQA"


def extract_2wiki(
    record: dict[str, object],
    *,
    split: str,
    revision: str,
) -> list[ExtractedDocument]:
    """Extract context occurrences from a parsed 2Wiki record, in order.

    The caller decodes context JSON and supplies the actual dataset revision.
    Only _id and context are required; gold question/answer/supporting-fact
    fields are not consumed. No partial result is returned on invalid input.
    Offsets are half-open Python string ranges; sentence indices are zero-based.
    """
    if not isinstance(record, dict):
        raise ValueError(f"2Wiki split {split!r}: record must be a dictionary")

    example_id = record.get("_id")
    location = f"2Wiki record {example_id!r}, split {split!r}"
    if not isinstance(example_id, str) or not example_id.strip():
        raise ValueError(f"{location}: _id must be a nonblank string")
    if not isinstance(split, str) or not split.strip():
        raise ValueError(f"{location}: split must be a nonblank string")
    if not isinstance(revision, str) or not revision.strip():
        raise ValueError(f"{location}: revision must be a nonblank string")

    context = record.get("context")
    if not isinstance(context, list) or not context:
        raise ValueError(f"{location}: context must be a nonempty parsed list")

    documents = []
    for context_position, passage in enumerate(context):
        passage_location = f"{location}, context position {context_position}"
        if not isinstance(passage, list) or len(passage) != 2:
            raise ValueError(
                f"{passage_location}: passage must be a [title, sentences] list"
            )

        title, sentences = passage
        if not isinstance(title, str) or not title.strip():
            raise ValueError(f"{passage_location}: title must be a nonblank string")
        if not isinstance(sentences, list) or not sentences:
            raise ValueError(
                f"{passage_location}: sentences must be a nonempty list"
            )

        structure = []
        source_spans = []
        offset = 0
        for sentence_index, sentence in enumerate(sentences):
            if not isinstance(sentence, str) or not sentence.strip():
                raise ValueError(
                    f"{passage_location}, sentence index {sentence_index}: "
                    "sentence must be a nonblank string"
                )

            end = offset + len(sentence)
            structure.append(
                StructuralSpan(kind=StructureKind.SENTENCE, start=offset, end=end)
            )
            source_spans.append(
                SourceSpan(
                    start=offset,
                    end=end,
                    locators=(
                        SourceLocator(kind=LocatorKind.SENTENCE, index=sentence_index),
                    ),
                )
            )
            # The inserted ASCII space is outside both adjacent sentence spans.
            offset = end + 1

        documents.append(
            ExtractedDocument(
                text=" ".join(sentences),
                title=title,
                source=SourceReference(
                    kind=SourceKind.DATASET,
                    source_id=DATASET_ID,
                    revision=revision,
                    record_locator={
                        "split": split,
                        "example_id": example_id,
                        "context_position": context_position,
                    },
                    display_name=title,
                ),
                structure=tuple(structure),
                source_spans=tuple(source_spans),
            )
        )

    return documents
