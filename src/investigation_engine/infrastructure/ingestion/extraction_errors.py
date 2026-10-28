"""File extraction failures, before registration or persistence."""


class DocumentExtractionError(ValueError):
    pass


class InsufficientTextError(DocumentExtractionError):
    """No non-whitespace readable text; image extraction/OCR is not provided."""


class UnsupportedDocumentError(DocumentExtractionError):
    pass


class DocumentLimitExceededError(DocumentExtractionError):
    """A parser exceeded a configured page or extracted-text bound."""


