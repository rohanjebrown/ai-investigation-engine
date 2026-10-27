# ADR-002: Python Environment and Dependency Management

**Project:** AI Investigation Engine  
**Date:** October 24, 2026  
**Status:** CONFIRMED for uv-managed development in [A01](register.md#a01); unapproved implementation and deployment details remain open.  
**Repository path:** `docs/decisions/002-environment-and-dependency-management.md`

## Approval reconciliation (2026-10-26)

The user explicitly ratified the existing uv-managed project environment: [A01](register.md#a01), approval [E01](register.md#e01). The historical "Accepted for initial implementation" label did not independently approve every command or future integration in this record.

References below to FastAPI are proposals ([U16](register.md#u16)). Commands installing pytest or changing dependency groups are historical examples, not authorization to add tooling now ([A10](register.md#a10), [U17](register.md#u17)). CI, deployment, and operational details remain [U18](register.md#u18). The repository review found a lockfile but no commits; statements that files should be committed are intended policy, not evidence of existing history. No dependency or configuration changes are part of this reconciliation.

## Context

The AI Investigation Engine is a single-repository Python application comprising dataset ingestion, document management, a FastAPI backend, vector and keyword retrieval, LLM integration, and automated evaluation. The Upgaming assignment requires reliable, modular Python code, reproducible evaluation, and a publicly deployed application running on free infrastructure.

The project requires isolated Python packages and a repeatable way to manage both direct and transitive dependencies across local Windows development, automated testing, and a future Linux deployment.

## Decision

Use **uv** for Python runtime, virtual-environment, and dependency management, with a standard **`.venv`** environment in the repository root. Continue using **CPython 3.14.9** as the initial interpreter selection, subject to compatibility testing.

- `pyproject.toml` declares project metadata, supported Python versions, application dependencies, and development dependency groups.
- `.python-version` specifies the selected development interpreter (`3.14.9`).
- `uv.lock` records the resolved dependency graph and must be committed to Git.
- `.venv/` contains locally installed packages and must **not** be committed.
- `uv run` launches commands in the managed environment; `uv sync` brings that environment into line with the lockfile.

**uv and venv are not mutually exclusive:** uv is the management tool; `.venv` is the isolated Python environment that uv creates and manages.

## Justification

### 1. Reproducibility and testability

A declared dependency list alone does not fix all versions of indirect dependencies. uv maintains a lockfile with resolved dependencies, and a locked sync can fail when `pyproject.toml` and `uv.lock` are inconsistent instead of silently changing the environment. This is useful for repeatable ingestion audits, benchmark evaluations, and debugging.

The lockfile is necessary but not sufficient for complete experiment reproducibility. We will additionally record the dataset revision and checksums, model identifiers and revisions, evaluation settings, random seeds where applicable, and runtime/platform information.

### 2. Simplicity within one repository

The same tool handles interpreter selection, environment creation, dependency resolution, installation, and command execution. This reduces the number of setup steps and avoids combining an unmanaged `pip install` workflow with another lockfile manager. One `.venv` is sufficient for the initial monolithic Python application.

### 3. Separation of development and production requirements

Runtime packages belong in `[project].dependencies` in `pyproject.toml`. Test and lint tools belong in the standardized `[dependency-groups].dev` group. Production installation can exclude development packages with `uv sync --locked --no-dev`.

### 4. Compatibility with continuous integration and deployment

The repository can be checked out on a build machine, given the selected interpreter and uv version, and installed from the committed lockfile. uv has documented Docker and CI workflows, but a particular free host's support must still be tested. If the selected host cannot use uv, standard Python package metadata can be installed by an alternative supported build workflow without changing the application's source architecture.

### 5. Faster iteration without claiming speed guarantees

uv is designed for fast dependency resolution and installation, which is useful while we test data, embedding, database, and API libraries. Speed is a secondary convenience rather than proof that uv will be faster for every workload or platform.

## Alternatives considered

| Alternative | Assessment |
| --- | --- |
| `python -m venv` + `pip` | Reliable standard baseline and a valid fallback. By themselves, however, they do not provide the integrated project lock/sync workflow we want; tools such as pip-tools would be needed for comparable dependency locking. |
| Poetry | A credible full-featured choice with virtual environments and lockfiles. Not selected because uv covers the functions this project currently needs with a straightforward command workflow; the decision is not based on Poetry being unsuitable. |
| Conda / Mamba | Useful when managing substantial non-Python native dependencies or scientific stacks. Not currently justified for this mainly Python/PyPI application; we will reassess if native runtime constraints require it. |
| System Python without isolation | Rejected because unrelated installed packages can affect the project and make reproducing results harder. |

## Risks and mitigations

1. **Python 3.14 and native wheels:** uv cannot make an incompatible library compatible. Test the exact combination of Python version, CPU architecture, operating system, and required packages, especially ML libraries, before finalizing deployment.
2. **Host limitations:** Some free hosting services may not provide uv or persistent disks by default. Validate deployment early and rebuild the environment during deployment rather than relying on a local `.venv`.
3. **Tool changes:** Record and pin the uv version used in CI/container builds. Review dependency updates deliberately and run regression tests before accepting lockfile changes.
4. **Reproducibility limits:** A lockfile does not control datasets, remote LLM behavior, external service versions, or operating-system dependencies. Record those independently.
5. **Supply-chain and secrets:** Review dependency changes; never commit credentials, local environments, downloaded private data, or API keys.

## Repository policy

```text
ai-investigation-engine/
├── .python-version       # Commit: selected Python interpreter
├── pyproject.toml        # Commit: project and dependency declarations
├── uv.lock               # Commit: resolved dependency versions
├── .venv/                # Ignore: recreated local environment
├── src/
├── tests/
└── docs/decisions/
    └── 002-environment-and-dependency-management.md
```

The root `.gitignore` must include `.venv/` and `.env`. Use standard `[dependency-groups]` for test/lint tools rather than treating them as published package extras.

## Initial commands (PowerShell, from the repository root)

```powershell
uv python install 3.14.9
uv python pin 3.14.9
uv add huggingface-hub pyarrow
uv add --dev pytest
uv sync
uv run python --version
uv run pytest
uv lock --check
```

The existing repository already has a `pyproject.toml`, so we should **not** run `uv init` on top of it. Remove the previous `[project.optional-dependencies].dev` declaration when switching to `[dependency-groups].dev` to avoid keeping two competing development-dependency mechanisms.

For automated deployment builds, after installing the selected interpreter and uv version:

```powershell
uv sync --locked --no-dev
```

This is a dependency-install command, not a complete hosting solution; actual platform configuration and persistence still need to be designed.

## Validation criteria

Before calling the decision fully validated, confirm that: (1) the full required dependency stack resolves and installs on Python 3.14.9 on both local and target platforms; (2) tests pass from a fresh checkout using `uv sync --locked`; (3) `uv lock --check` passes in CI; (4) the deployed application starts using runtime-only dependencies; and (5) the environment can be recreated without committing `.venv/`.

## References

- Upgaming, *Applied AI – AI Investigation Engine Task*, sections on dataset evaluation, technical quality, and free deployment (provided assignment).
- Astral, *Working on projects*: https://docs.astral.sh/uv/guides/projects/
- Astral, *Locking and syncing*: https://docs.astral.sh/uv/concepts/projects/sync/
- Astral, *Managing dependencies*: https://docs.astral.sh/uv/concepts/dependencies/
- Astral, *Using uv in Docker*: https://docs.astral.sh/uv/guides/integration/docker/
- Python, *venv — Creation of virtual environments*: https://docs.python.org/3/library/venv.html
- Poetry, *Basic usage*: https://python-poetry.org/docs/basic-usage/
