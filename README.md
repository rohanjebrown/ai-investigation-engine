# AI Investigation Engine

A Python foundation for investigating questions across documents, developed for
the **Upgaming Applied AI Engineer practical assignment**. The intended system
will combine retrieval and multi-step reasoning with traceable evidence.

## Current implementation

- Database-independent document, immutable version, Content, and provenance models.
- TXT and 2WikiMultihopQA extraction with original character offsets and locators.
- Exact UTF-8 SHA-256 Content reuse while preserving independent source identities.
- Async registration through in-memory and PostgreSQL repositories, including
  same-source retries, conflict detection, and trusted ownership assignment.
- SQLAlchemy ORM mappings and one Alembic migration for the authoritative schema.
- Whole-passage, fixed-token, and sentence-aware chunking over exact Content ranges.
- An explicitly loaded, checksum-verified experimental tokenizer adapter.

PostgreSQL with pgvector, native full-text search, and application-owned RRF are
selected technologies. **Embedding generation/storage, retrieval, indexing and
activation, RAG/LLM investigation, authentication, API/UI, and deployment are not
implemented.** PDF/DOCX extraction and document update/delete workflows also remain
unfinished. The five derived tables are a proposal, not an approved migration.

## Setup

Use Python **3.14** (initially 3.14.9) and [uv](https://docs.astral.sh/uv/).
From the repository root, in PowerShell:

```powershell
uv sync --locked
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

Dependency installation requires access to the configured package sources unless
the packages are cached. Exact dependency versions are recorded in `uv.lock`.
The wheel contains the Python library; the source archive additionally includes
migrations, documentation, configuration examples and the small sample input.

## Configuration and PostgreSQL

Edit your ignored `.env` using `.env.example` as the variable-name reference.
`DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, and `DB_PASSWORD` are required when
importing settings. `GEMINI_API_KEY` is optional for database-only work;
there is no embedding client yet.

Run commands from the repository root. python-dotenv automatically loads `.env`
from the working directory without overriding process variables. Values are
literal (interpolation disabled), preserving credentials containing `${...}`.

```python
from investigation_engine.infrastructure.config import settings
```

This shared frozen dataclass is initialized on first import. Required DB variables
are validated immediately; DB_PORT is parsed as an integer in 1–65535. The optional
Gemini key defaults to None. No boolean settings are currently needed. Restart
after configuration changes. Secret fields are hidden from repr. URL.create builds
the database URL; imports open no connections. Domain components receive
dependencies rather than importing settings. Offline Alembic does not load settings.

The current connection helper supports the existing **Cloud SQL Auth Proxy at
127.0.0.1:15432**. Its local PostgreSQL connection uses `sslmode=disable`; the
proxy provides the encrypted remote tunnel. Start your already configured proxy
separately. There is no fallback to local PostgreSQL on port 5432 or to another
connection mode. Other deployment paths need a reviewed configuration.

The cloud instance was inspected read-only on 25 October 2026: PostgreSQL 18.6,
pgvector 0.8.5, and no application tables or Alembic revision table. The schema is
implemented in migration files, **not yet applied to that database**. Migration
execution is a separate operator action; the local smoke check below needs no DB.

## Local verification

After setup, this PowerShell command checks TXT extraction, registration retries,
independent document identities, and shared exact Content without network or DB I/O:

```powershell
@'
import asyncio
from dataclasses import replace
from pathlib import Path
from investigation_engine.application.use_cases.register_document import RegisterDocument
from investigation_engine.infrastructure.ingestion.txt_adapter import extract_txt
from investigation_engine.infrastructure.persistence.in_memory_document_repository import InMemoryDocumentRepository

async def main():
    extracted = extract_txt(Path("data/samples/incident.txt"), source_id="smoke-a")
    register = RegisterDocument(InMemoryDocumentRepository())
    first, version = await register.execute(extracted, owner_id=None)
    retry, repeated = await register.execute(extracted, owner_id=None)
    independent = replace(extracted, source=replace(extracted.source, source_id="smoke-b"))
    second, other = await register.execute(independent, owner_id=None)
    assert first.id == retry.id and version.id == repeated.id
    assert first.id != second.id and version.content is other.content
    assert version.content.text == extracted.text
    print("TXT extraction, idempotency, provenance separation, and Content reuse passed")

asyncio.run(main())
'@ | uv run --locked --no-env-file python -
```

Check dependency consistency with `uv lock --check --offline`.
No formatter, linter, or type checker is configured. The existing test module is
empty: there is **no permanent automated test coverage yet**. A10 defers adding
tests temporarily; automated tests and a separate >=40-question evaluation remain
assignment requirements. `main.py` is the original uv scaffold, not a web server.

## Dataset and tokenizer

The approved corpus is the complete pinned 2Wiki development split: 125,760
occurrences and 56,680 unique exact-text Contents. Exact uniqueness is a
conservative counting baseline, not proof of semantic independence. Questions,
answers, and gold evidence stay outside the retrieval corpus. See the
[dataset audit](docs/validation/2wiki-dev-audit.md).

Raw data is excluded from Git. An explicit download (not required by the smoke
check) is available through `uv run python -m investigation_engine.ingestion.download_dataset`.
It requires network access and uses the revision pinned in that module. The older
`audit_contexts` script compares sentence lists and exploratory normalized text;
it does not reproduce the approved exact extracted-text count.

Chunking requires a separately obtained reference tokenizer. To download it
explicitly, with network access:

```powershell
uv run python -c "from huggingface_hub import hf_hub_download; hf_hub_download('MongoDB/voyage-4', filename='tokenizer.json', revision='44f3b2ae4ddf33403ed4dd66bec3fa48ff7dbbf9', local_dir='artifacts/tokenizers/voyage-4')"
```

The adapter verifies SHA-256
`c0382117ea329cdf097041132f6d735924b697924d6f6fc3945713e96ce87539`
before loading. It performs no implicit downloads. These artifacts are ignored.
This Voyage tokenizer defines experimental chunk boundaries; its token counts are
not Gemini token counts. See [chunking usage and verification](docs/decisions/deterministic-chunking.md).

## Architecture and next work

The package uses domain dataclasses, application use cases/ports, and infrastructure
adapters. `src/investigation_engine/ingestion/` contains standalone dataset
inspection/download utilities; it is not the production extraction boundary.

Start with the [documentation index](docs/README.md),
[decision register](docs/decisions/register.md), and [AI usage record](AI_USAGE.md).
Next work requires approval of derived storage and encoding choices, followed by
the remaining indexing lifecycle, retrieval, evaluation, and product workflows.
