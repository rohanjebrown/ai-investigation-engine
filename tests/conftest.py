"""Suite selection and the small registration fixture shared across categories."""

import asyncio
import sys

import pytest

from investigation_engine.domain.content import Content
from investigation_engine.domain.document import Document
from investigation_engine.domain.document_version import DocumentVersion
from investigation_engine.domain.provenance import SourceKind, SourceReference


def pytest_addoption(parser):
    parser.addoption("--run-external", action="store_true", help="Enable live service smoke tests")
    parser.addoption("--run-dataset", action="store_true", help="Enable local pinned-corpus checks")
    parser.addoption(
        "--dataset-path", default="data/raw/2wiki/dev.parquet", help="Existing pinned dev.parquet; never downloaded"
    )
    parser.addoption(
        "--tokenizer-path",
        default="artifacts/tokenizers/voyage-4/tokenizer.json",
        help="Existing pinned reference tokenizer; never downloaded",
    )


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config, items):
    for item in items:
        parts = item.path.relative_to(config.rootpath).parts
        for category in ("unit", "integration", "external"):
            if category in parts:
                item.add_marker(getattr(pytest.mark, category))
        if "dataset" in item.keywords:
            if not config.getoption("--run-dataset"):
                item.add_marker(pytest.mark.skip(reason="Use --run-dataset; no dataset is downloaded automatically"))
        elif "external" in item.keywords and not config.getoption("--run-external"):
            item.add_marker(pytest.mark.skip(reason="Live services require --run-external, even when a key exists"))


def pytest_asyncio_loop_factories(config, item):
    if sys.platform == "win32":
        # psycopg requires selectors; avoid deprecated global loop policies.
        return {"selector": asyncio.SelectorEventLoop}
    return {"asyncio": asyncio.new_event_loop}


@pytest.fixture
def registration():
    """Build initial candidates; independent calls have independent UUIDv7 IDs."""

    def make(
        *,
        body="A😀 B.",
        source_id="synthetic-source",
        locator=None,
        owner_id=None,
        revision=None,
        structure=(),
        source_spans=(),
    ):
        document = Document(owner_id=owner_id, title="Original title")
        version = DocumentVersion(
            document_id=document.id,
            version_number=1,
            content=Content(body),
            source=SourceReference(
                kind=SourceKind.DATASET,
                source_id=source_id,
                revision=revision,
                record_locator={"record": 1} if locator is None else locator,
                metadata={"labels": ["original"]},
                display_name="Original display",
            ),
            structure=structure,
            source_spans=source_spans,
        )
        return document, version

    return make


@pytest.fixture
def synthetic_provider():
    """Deterministic implementation of the real provider port, without IO."""
    from hashlib import sha256

    from investigation_engine.application.embeddings import EmbeddingConfig

    class SyntheticProvider:
        configuration = EmbeddingConfig(provider="synthetic", model_id="deterministic", model_revision="1")

        def __init__(self):
            self.calls = []
            self.call_batches = []
            self.failures = []
            self.before_call = None

        async def embed_documents(self, texts):
            self.call_batches.append(tuple(texts))
            self.calls.extend(texts)
            if self.before_call is not None:
                await self.before_call()
            if self.failures:
                error = self.failures.pop(0)
                if error is not None:
                    raise error
            return tuple(
                (float(sha256((repr(self.configuration) + "|title: none | text: " + value).encode()).digest()[0] + 1),)
                + (0.0,) * 767
                for value in texts
            )

        async def embed_query(self, query):
            return (await self.embed_documents((query,)))[0]

    return SyntheticProvider()
