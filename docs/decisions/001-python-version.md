# Architecture Decision Record: Python Runtime Selection

**Project:** Applied AI – AI Investigation Engine  
**Decision ID:** ADR-001  
**Decision date:** 2026-10-24  
**Status:** CONFIRMED for the initial runtime in [A01](register.md#a01); other policies and compatibility claims are qualified below.  
**Selected runtime:** **CPython 3.14**, standard GIL-enabled build  
**Initial development patch release:** **Python 3.14.9** (released 2026-10-21)

## Approval reconciliation (2026-10-26)

The user explicitly ratified Python 3.14.9 initially and uv-managed development: [A01](register.md#a01), approval [E01](register.md#e01). The historical status was "Accepted for implementation, subject to dependency and deployment smoke tests"; that label alone is not approval evidence.

The rationale and examples below are retained as historical context. The approval does not independently verify every dated release/support claim or approve future runtime changes, container images, CI policy, or unselected dependency combinations. Deployment compatibility and operational choices remain [U18](register.md#u18); baseline changes require explicit approval. Example pytest commands do not override the immediate test deferral [A10](register.md#a10) or select the later test milestone [U17](register.md#u17).

## Context

The Upgaming assignment requires a Python backend that can process and index at least 50,000 documents or independent text records, ingest at least three file formats, provide vector and hybrid retrieval, integrate an LLM, support multi-step investigations, expose a functional interface, run automated evaluation, and deploy publicly on free infrastructure with persistent data.

Python is the orchestration language for ingestion, API endpoints, retrieval, evaluation, and integration with the selected vector database and LLM. Consequently, interpreter selection must prioritize **library compatibility, supported maintenance life, reproducibility, and deployment practicality**, rather than simply choosing the newest release or maximizing speculative speed improvements.

## Decision

Use **CPython 3.14** as the single supported Python minor version for the application's backend, ingestion jobs, evaluation scripts, tests, and deployment. Start development with **3.14.9**, the latest 3.14 maintenance release available on the decision date, and apply later security/bugfix patch releases within the 3.14 line after testing.

Use the standard CPython build rather than the optional free-threaded (`3.14t`) runtime. This avoids introducing a separate compatibility variable without demonstrated performance benefit to the project.

## Rationale

### 1. Maintained runtime with adequate project lifetime

Python 3.14 was released on October 7, 2025, is in active bugfix maintenance on the decision date, and has a scheduled end of support in October 2030. This gives the project about four years of remaining support. Python 3.13 is already security-fixes-only with an October 2029 end-of-life date, and Python 3.12 has an October 2028 end-of-life date. A maintained version with a longer support horizon reduces the risk of requiring an interpreter upgrade during demonstration, continuation, or future maintenance.

### 2. Compatibility with the project's likely technology stack

As of the decision date, official release metadata or documentation lists Python 3.14 compatibility for the core tooling being considered:

| Application concern | Relevant package | Compatibility evidence |
|---|---|---|
| Dataset downloads | `huggingface_hub` | PyPI includes Python 3.14 |
| Parquet and columnar data ingestion | `pyarrow` | Apache Arrow and PyPI include Python 3.14, including the v24 line allowed by the current starter project |
| API layer | `fastapi` | PyPI lists Python 3.14 |
| Embedding inference (if local) | `torch` / `sentence-transformers` | PyTorch provides CPython 3.14 wheels; Sentence Transformers recommends Python 3.10+ but the selected dependency combination must be tested |
| Vector database client (if Qdrant selected) | `qdrant-client` | PyPI lists Python 3.14 |
| Local vector-search alternative | `faiss-cpu` | PyPI lists Python 3.14 and publishes platform-specific wheels |

These checks demonstrate that **Python 3.14 is a viable starting point**, not that every eventual transitive dependency, operating system, model runtime, or hosting platform has been tested. The final dependency combination and target hosting image will undergo an installation and smoke test before the technology stack is frozen.

### 3. Maturity versus newest releases

Python 3.15 became stable on October 9, 2026, only two weeks before this decision. It is too new to assume the entire embedding and native-extension stack supports it. In particular, the PyTorch 2.14.1 release metadata lists support through Python 3.14, not 3.15. Choosing 3.14 avoids taking on new-release compatibility risk without a requirement-driven benefit. Python 3.12 and 3.13 remain reasonable fallbacks, but they offer shorter support windows and no necessary advantage for the current implementation.

### 4. Reproducible development and deployment

Using one minor version across local development, CI, background ingestion jobs, and production reduces interpreter-related differences in dependency resolution and runtime behavior. We will declare the supported range in packaging metadata, pin the actual runtime for builds, and lock third-party dependencies for reproducible installations. Generated artifacts, dataset revisions, and evaluation configurations will be versioned separately.

### 5. Appropriate for an I/O-heavy backend

The application is expected to spend significant time waiting on network calls to external LLM services and databases, and performing vector search or embedding work in dedicated libraries. Standard Python 3.14 with asynchronous I/O and bounded worker tasks is sufficient for the first version. Free-threaded execution, custom interpreter builds, or a version-specific language feature are not prerequisites for meeting the assignment requirements. Performance claims will be based on measurements rather than inferred from a Python release number.

## Alternatives considered

| Version | Assessment |
|---|---|
| Python 3.12 | Widely established and compatible, but already in security-only maintenance; support ends October 2028. No project-specific need to stay on it. |
| Python 3.13 | Mature and supported, but security-only maintenance as of October 2026; support ends October 2029. Defensible fallback if a required dependency breaks on 3.14. |
| **Python 3.14** | **Selected:** in active bugfix maintenance, supported until October 2030, and supported by major libraries in the planned stack. |
| Python 3.15 | Released October 9, 2026. Too recent for confidently assuming full native-library support; not selected for the initial implementation. |

## Implementation policy

- **Project runtime:** CPython **3.14.x**; start with **3.14.9**.
- **`pyproject.toml`:** `requires-python = ">=3.14,<3.15"`.
- **Developer environment:** use `uv` to manage the repository `.venv` with Python 3.14; do not install project dependencies globally.
- **Runtime reproducibility:** record `3.14.9` in `.python-version` or an equivalent version-manager file; use a corresponding version-specific Docker image or a platform runtime setting for deployment.
- **Dependency reproducibility:** declare dependencies in `pyproject.toml` and commit `uv.lock`; keep development dependencies separate from runtime dependencies where practical.
- **CI and deployment:** run dependency installation, test suite, and a small ingestion/API/vector/embedding smoke test on the exact target operating system and Python runtime.
- **Patch updates:** move to newer Python 3.14 security/maintenance patches after tests pass; do not upgrade to 3.15 without an explicit compatibility review.
- **Project alignment:** use `uv sync` and `uv run` consistently. The details of dependency and environment management are specified separately in ADR-002.

Example Windows PowerShell setup:

```powershell
uv python install 3.14.9
uv sync
uv run python --version
uv run python -m pytest -q
```

## Risks and validation criteria

**Risk:** A dependency or free-hosting provider may have Python 3.14 limitations despite upstream package classifiers.  
**Mitigation:** Verify all resolved packages and the target provider; if a *required* package is incompatible, evaluate Python 3.13 before adding workaround complexity.

**Risk:** New patches may alter behavior or update bundled packaging tools.  
**Mitigation:** Update within the same minor line through tested builds, with reproducible dependency resolution and tests.

**Acceptance criteria before locking the stack:**

1. The existing dataset audit dependencies install and the unit tests pass on Python 3.14.
2. A sample 2WikiMultihopQA Parquet file downloads and is read successfully.
3. The chosen embedding backend and vector database client complete an end-to-end indexing and retrieval smoke test.
4. A minimal FastAPI endpoint runs in the selected free deployment environment; data survives an application restart.
5. CI and deployment report the same Python minor version, and evaluation runs record the runtime and dependency versions.

## Conclusion

**Python 3.14 is the best balance for this project as of October 24, 2026:** it is recent but not newly released, remains in active maintenance until its later security phase, has a multi-year support horizon, and is supported by the major data, API, and AI libraries under consideration. Python 3.14.9 will be used initially; the decision is validated by reproducible installation and end-to-end tests rather than assumed package compatibility.

## References (checked 2026-10-24)

1. Python Developer's Guide — Status of Python versions: https://devguide.python.org/versions/
2. Python.org — Python 3.14.9 release notes: https://www.python.org/downloads/release/python-3149/
3. Python.org — Python 3.15.0 release notes: https://www.python.org/downloads/release/python-3150/
4. Apache Arrow — Installing PyArrow: https://arrow.apache.org/docs/python/install.html
5. PyPI — PyArrow 24.0.0: https://pypi.org/project/pyarrow/24.0.0/
6. PyPI — huggingface-hub: https://pypi.org/project/huggingface-hub/
7. PyPI — FastAPI: https://pypi.org/project/fastapi/
8. PyPI — PyTorch: https://pypi.org/project/torch/
9. Sentence Transformers — Installation: https://sbert.net/docs/installation.html
10. PyPI — qdrant-client: https://pypi.org/project/qdrant-client/
11. PyPI — faiss-cpu: https://pypi.org/project/faiss-cpu/
12. Upgaming — Applied AI / AI Investigation Engine Task, project assignment document (provided with application).
