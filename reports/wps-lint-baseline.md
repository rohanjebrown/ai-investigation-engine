# Ruff and WPS cleanup report

Date: 2026-10-29. Worktree: `ai-investigation-engine-wps`, branch `chore/wps-cleanup`, base commit `182b14f934c097f1edf22dc4809774454d417300`.

## Scope and revision

The original worktree was left untouched. Both worktrees point to the same commit. Its 603 collected unit tests include 27 uncommitted tests under `tests/unit/scripts/`; this worktree does not contain them and collects 576. This is a working-tree difference, not an older commit or missing committed application change.

## Current checks and counts

Ruff lint is clean and Ruff Formatter reports all 225 discovered Python files formatted. The refreshed WPS count is 2,798 over all 176 Python files checked (119 have findings), including deploy and migration code. Historical counts used different scopes and settings: initial WPS was 4,879 for `src/`, `scripts/`, and `tests/`; the earlier cleanup report recorded 4,465, and a later broader-scope report recorded 3,568. Do not treat those as strict like-for-like comparisons. Ruff started at 119 violations and is now zero.

Current high-frequency rules include WPS221 (570), WPS110 (327), WPS111 (283), WPS226 (161), WPS220 (158), WPS210 (87), WPS430 (86), WPS432 (82), WPS231 (70), WPS476 (64), WPS421 (62), WPS229 (60).

Current remaining findings by rule:

~~~text
2     WPS102 Found incorrect module name pattern
327   WPS110 Found wrong variable name: result
283   WPS111 Found too short name: c < 2
4     WPS114 Found underscored number name pattern: extract_2wiki
1     WPS117 Found name reserved for first argument: self
1     WPS120 Found regular name with trailing underscore: _fields_
1     WPS122 Found all unused variables definition: _last_generation_at
35    WPS201 Found module with too many imports: 35 > 12
12    WPS202 Found too many module members: 30 > 14
2     WPS203 Found module with too many imported names: 52 > 50
59    WPS204 Found overused expression: sa.Text(collation='C'); used 10 > 7
87    WPS210 Found too many local variables: 10 > 8
4     WPS211 Found too many arguments: 9 > 7
6     WPS212 Found too many return statements: 6 > 5
20    WPS213 Found too many expressions: 12 > 9
4     WPS214 Found too many methods: 10 > 7
32    WPS217 Found too many await expressions: 7 > 5
5     WPS218 Found too many `assert` statements: 11 > 5
56    WPS219 Found too deep access level: 6 > 4
158   WPS220 Found too deep nesting: 24 > 20
570   WPS221 Found line with high Jones Complexity: 15 > 14
3     WPS222 Found a condition with too much logic: 6 > 4
2     WPS223 Found too many `elif` branches: 4 > 3
1     WPS224 Found a comprehension with too many `for` statements
161   WPS226 Found string literal over-use: 'spec' 10 > 3
3     WPS228 Found too long compare: 3 > 2
60    WPS229 Found too long ``try`` body length: 21 > 1
1     WPS230 Found too many public instance attributes: 9 > 6
70    WPS231 Found function with too much cognitive complexity: 33 > 12
21    WPS232 Found module cognitive complexity that is too high: 8.3 > 8
3     WPS234 Found overly complex annotation: 4 > 3
4     WPS235 Found too many imported names from a module: 10 > 8
7     WPS236 Found too many variables used to unpack a tuple: 5 > 4
22    WPS237 Found a too complex formatted string
14    WPS238 Found too many raises in a function: 13 > 5
5     WPS239 Found too many exceptions in `except` case: 4 > 3
7     WPS243 Found too long `finally` block: 3 > 2
12    WPS301 Found dotted raw import: importlib.util
1     WPS332 Found improper use of a walrus operator
4     WPS335 Found incorrect `for` loop iter type
41    WPS336 Found explicit string concatenation
4     WPS338 Found incorrect order of methods in a class
4     WPS358 Found a float zero (0.0)
7     WPS363 Found `raise SystemExit`, instead of using `sys.exit`
2     WPS402 Found `noqa` comments overuse: 14
1     WPS404 Found complex default value
4     WPS407 Found mutable module constant
1     WPS409 Found heterogeneous compare
25    WPS414 Found incorrect unpacking target
22    WPS420 Found wrong keyword: nonlocal
62    WPS421 Found wrong function call: print
3     WPS426 Found `lambda` in loop's body
7     WPS429 Found multiple assign targets
86    WPS430 Found nested function: describe
36    WPS431 Found nested class: Counters
82    WPS432 Found magic number: 768
25    WPS435 Found list multiply
47    WPS441 Found control variable used after block: run_report
1     WPS444 Found incorrect keyword condition
9     WPS458 Found imports collision: time
15    WPS459 Found comparison with float or complex number
8     WPS462 Wrong multiline string usage
64    WPS476 Found `await` in `for` loop
54    WPS501 Found `finally` in `try` block without `except`
12    WPS504 Found negated condition
20    WPS505 Found nested `try` block
24    WPS509 Found incorrectly nested ternary
23    WPS516 Found `type()` used to compare types
20    WPS520 Found compare with falsy constant
1     WPS529 Found implicit `.get()` dict usage
1     WPS600 Found subclassing a builtin: dict
3     WPS601 Found shadowed class attribute: active
10    WPS602 Found using `@staticmethod`
2     WPS604 Found incorrect node inside `class` body
2     WPS613 Found incorrect `super()` call context: incorrect name access
~~~

## Work completed

Substantive refactors cover application answer validation, investigation evidence collection and decision validation, retrieval value validation, chunk range algorithms, reciprocal-rank fusion state, upload validation/extraction, shared single/bulk indexing retry logic, and PostgreSQL evidence provenance decoding. This continuation split chunk-plan validation responsibilities, separated embedding validation/conversion/norm checks, simplified investigation orchestration, decomposed answer generation and citation validation, separated provider diagnostic sanitization, and clarified SearchEvidence configuration assignments. These refactors preserve ordering, validation, error mapping, exception behavior, retries, and asynchronous sequencing.

Other changes replaced API status literals with `HTTPStatus`, removed dynamic imports, and simplified shared SQL range extraction. Ruff cleanup also corrected imports, formatting, and the remaining E731 lambda. Previously measured reductions include WPS221 (821 to 606), WPS432 (557 to 439), WPS110 (424 to 391), WPS516 (60 to 42), WPS220 (199 to 188), and WPS336 (40 to 37); these historical measurements predate later policy/scope changes. This continuation began at 2,878 and brought the count to 2,798: investigation refactoring eliminated 19 findings, indexing validation eliminated 11, generation error documentation eliminated 4, the prepare-document-indexing constructor cleanup eliminated 3 (WPS414), and the bulk indexing phase split and explicit pending-replacement state eliminated one finding each, extracting answer-quality report serialization eliminated four, and splitting answer-suite validation reduced five findings; extracting per-case execution reduced the main orchestration complexity but added one aggregate WPS finding; replacing the ten-value response tuple with a named record removed three further findings, descriptive paired-comparison names eliminated ten, and explicit evaluation/index variable names eliminated nine WPS111 findings, and answer-failure classification simplification removed three findings, and explicit review-map lookup plus scoped review names reduced six more, and a positive grounding-review predicate plus explicit basis assignment removed one WPS504; referencing the shared embedding dimension removed one WPS432. The preceding continuation reduced 2,907 to 2,878; earlier work reduced the prior-policy count from 3,238. The investigation-state refactor eliminated WPS110 -1, WPS202 -1, WPS210 -1, WPS212 -1, WPS221 -1, WPS231 -1, WPS236 -1, WPS237 -1, WPS358 -1, WPS414 -2, WPS429 -1, WPS476 -2, WPS504 -1, WPS509 -1, WPS516 -2, and WPS529 -1. Narrow exceptions retain the float duration and exact-int checks; the remaining reductions came from state grouping, sequential-flow clarity, and the value/orchestration split.  The index-constructor validation changes eliminated WPS110 -2, WPS221 -1, WPS414 -7, and WPS516 -1 through clearer target validation, separate assignments, and one documented exact-type check. The later prepare-document-indexing constructor cleanup eliminated three more WPS414 findings by assigning each dependency separately. The bulk indexing path was separated into sequential plan preparation and ordered report/activation phases; this improved responsibility boundaries with a net reduction of one WPS finding. The prior turn-by-rule count changes were WPS110 -2, WPS210 -1, WPS211 -2, WPS212 -1, WPS213 -1, WPS221 -1, WPS231 -2, WPS232 -1, WPS235 -1, WPS237 -1, WPS414 -13, WPS516 -2, and WPS602 -1. Four of those fewer findings are narrowly justified WPS211/WPS516 suppressions to preserve explicit APIs and exact-type semantics; the rest came from code changes.

## WPS policy

`.flake8` enables WPS across the repository with moderate limits for local variables, name length, module members, arguments, and raises. WPS221/WPS220/WPS231 remain enabled as complexity signals; WPS226 remains enabled in production, where repeated wire/schema keys may indicate a contract worth naming. Tests have narrow rule exceptions for long descriptive names, multiple assertions in behavior cases, repeated expected strings at assertion sites, explicit float-valued vector fixtures, and expected literals. WPS432 is ignored only in the settings-bound table and tests. WPS421 is ignored only in CLI scripts, whose `print` calls are command output; dynamic imports were replaced with normal imports. Inline suppressions are limited and explained where strict checks preserve short-circuit behavior or half-open range semantics. WPS430 and WPS476 remain enabled for app-local FastAPI closures and ordered asynchronous work. No broad rule group has been disabled.

## Validation

- `ruff check .`: passed.
- `ruff format --check .`: passed (224 files).
- Whole-repository WPS check: 2,800 findings across 176 Python files checked (119 with findings).
- Rescore and grounding review tests after dictionary lookup cleanup: 24 passed.
- Focused answer-quality/identity tests after classification simplification: 48 passed.
- Focused evaluation and indexing tests after descriptive-name cleanup: 71 passed.
- Focused answer-quality and identity/value tests after comparison-name cleanup: 48 passed.
- Focused answer-quality and identity/value tests after response-record change: 48 passed.
- Focused document lifecycle and index version tests after constructor cleanup: 28 passed.
- Focused tests: chunk-plan validation (20 passed), embeddings/retrieval (73 passed), investigations (9 passed), and index-document-version (23 passed).
- Full isolated unit suite after all current refactors: 576 passed, 1 third-party deprecation warning.
- Isolated PostgreSQL integration: semantic and hybrid retrieval suites passed (40 tests) in a disposable pgvector Testcontainer. No external-provider, production database, or Cloud Run tests were run. The local unit suite used loopback-only database settings.
- `git diff --check`: passed.

The upload `finally` block intentionally closes the upload even if filename validation or reading raises; WPS501/WPS229 remain at that site. FastAPI route closures, ordered awaits, test-focused findings, and meaningful exception paths require case-by-case review. Do not add architectural wrappers or duplicate named constants solely to reduce counts.

## Enforcement and next steps

Ruff lint and formatting can be gated in CI now. Continue WPS cleanup in `src/`, then scripts and tests, capturing before/after counts with a pinned policy before enabling a WPS gate. Prioritize complexity, overlong functions, nested exception flow, and high-impact magic values where an invariant can be named without changing domain meaning. Keep intentional exceptions narrow and documented.

Reproduction commands (PowerShell):

```powershell
uv run --locked ruff check .
uv run --locked ruff format --check .
$files = rg --files -g '*.py'
uv run --locked flake8 $files --statistics --count
uv run --locked pytest
```

## Modified files in this worktree

148 paths are modified or untracked; the full list follows.

- `.flake8`
- `deploy/cloud_run.py`
- `deploy/entrypoint.py`
- `docs/decisions/deterministic-chunking.md`
- `migrations/env.py`
- `migrations/versions/0001_document_registration.py`
- `migrations/versions/0002_embedding_storage.py`
- `pyproject.toml`
- `README.md`
- `reports/wps-lint-baseline.md`
- `scripts/benchmark_retrieval.py`
- `scripts/evaluate_persistent_answer_quality.py`
- `scripts/evaluate_persistent_retrieval.py`
- `scripts/experiment_gemini_embedding_batches.py`
- `scripts/index_evaluation_corpus.py`
- `scripts/migrate_evaluation_database.py`
- `scripts/rescore_answer_quality.py`
- `scripts/validate_corpus_scale.py`
- `scripts/validate_gemini_corpus_pilot.py`
- `scripts/verify_persistent_evaluation_search.py`
- `src/investigation_engine/application/answers.py`
- `src/investigation_engine/application/chunk_plan_validation.py`
- `src/investigation_engine/application/chunking_ranges.py`
- `src/investigation_engine/application/chunking.py`
- `src/investigation_engine/application/embeddings.py`
- `src/investigation_engine/application/investigation_decisions.py`
- `src/investigation_engine/application/investigation_records.py`
- `src/investigation_engine/application/investigation_values.py`
- `src/investigation_engine/application/investigations.py`
- `src/investigation_engine/application/ports/document_activation_repository.py`
- `src/investigation_engine/application/ports/document_repository.py`
- `src/investigation_engine/application/ports/embedding_provider.py`
- `src/investigation_engine/application/ports/embedding_repository.py`
- `src/investigation_engine/application/ports/evidence_repository.py`
- `src/investigation_engine/application/ports/generation_errors.py`
- `src/investigation_engine/application/ports/provider_diagnostics.py`
- `src/investigation_engine/application/ports/text_tokenizer.py`
- `src/investigation_engine/application/retrieval.py`
- `src/investigation_engine/application/rrf.py`
- `src/investigation_engine/application/use_cases/answer_question.py`
- `src/investigation_engine/application/use_cases/index_document_version.py`
- `src/investigation_engine/application/use_cases/list_documents.py`
- `src/investigation_engine/application/use_cases/prepare_document_indexing.py`
- `src/investigation_engine/application/use_cases/register_document.py`
- `src/investigation_engine/application/use_cases/search_evidence.py`
- `src/investigation_engine/application/use_cases/update_document.py`
- `src/investigation_engine/domain/document_version.py`
- `src/investigation_engine/domain/document.py`
- `src/investigation_engine/domain/provenance.py`
- `src/investigation_engine/domain/source_span.py`
- `src/investigation_engine/domain/structural_span.py`
- `src/investigation_engine/evaluation/answer_aliases.py`
- `src/investigation_engine/evaluation/answer_quality_report.py`
- `src/investigation_engine/evaluation/answer_quality.py`
- `src/investigation_engine/evaluation/build_suite.py`
- `src/investigation_engine/evaluation/checkpoints.py`
- `src/investigation_engine/evaluation/chunking_comparison.py`
- `src/investigation_engine/evaluation/grounding.py`
- `src/investigation_engine/evaluation/rescore.py`
- `src/investigation_engine/evaluation/retrieval.py`
- `src/investigation_engine/infrastructure/api/app.py`
- `src/investigation_engine/infrastructure/api/bootstrap.py`
- `src/investigation_engine/infrastructure/api/server.py`
- `src/investigation_engine/infrastructure/api/uploads.py`
- `src/investigation_engine/infrastructure/config.py`
- `src/investigation_engine/infrastructure/embeddings/gemini_provider.py`
- `src/investigation_engine/infrastructure/generation/gemini_answer_generator.py`
- `src/investigation_engine/infrastructure/generation/gemini_diagnostics.py`
- `src/investigation_engine/infrastructure/generation/gemini_investigation_model.py`
- `src/investigation_engine/infrastructure/ingestion/docx_adapter.py`
- `src/investigation_engine/infrastructure/ingestion/extraction_errors.py`
- `src/investigation_engine/infrastructure/ingestion/pdf_adapter.py`
- `src/investigation_engine/infrastructure/ingestion/twowiki_adapter.py`
- `src/investigation_engine/infrastructure/ingestion/txt_adapter.py`
- `src/investigation_engine/infrastructure/persistence/_document_mapping.py`
- `src/investigation_engine/infrastructure/persistence/_embedding_mapping.py`
- `src/investigation_engine/infrastructure/persistence/in_memory_document_repository.py`
- `src/investigation_engine/infrastructure/persistence/models.py`
- `src/investigation_engine/infrastructure/persistence/postgresql_chunk_plan_repository.py`
- `src/investigation_engine/infrastructure/persistence/postgresql_document_activation_repository.py`
- `src/investigation_engine/infrastructure/persistence/postgresql_document_repository.py`
- `src/investigation_engine/infrastructure/persistence/postgresql_embedding_repository.py`
- `src/investigation_engine/infrastructure/persistence/postgresql_evidence_repository.py`
- `src/investigation_engine/infrastructure/persistence/register_chunking_configuration.py`
- `src/investigation_engine/infrastructure/tokenization/fetch_runtime.py`
- `src/investigation_engine/infrastructure/tokenization/huggingface_tokenizer.py`
- `src/investigation_engine/ingestion/audit_contexts.py`
- `src/investigation_engine/ingestion/corpus_processing.py`
- `src/investigation_engine/ingestion/download_dataset.py`
- `src/investigation_engine/ingestion/inspect_dataset.py`
- `src/investigation_engine/ingestion/inspect_parquet.py`
- `tests/conftest.py`
- `tests/external/ingestion/test_twowiki_corpus.py`
- `tests/integration/conftest.py`
- `tests/integration/external/test_live_answer_quality.py`
- `tests/integration/infrastructure/persistence/conftest.py`
- `tests/integration/infrastructure/persistence/test_answer_quality.py`
- `tests/integration/infrastructure/persistence/test_corpus_processing.py`
- `tests/integration/infrastructure/persistence/test_document_lifecycle.py`
- `tests/integration/infrastructure/persistence/test_document_management_http.py`
- `tests/integration/infrastructure/persistence/test_embedding_indexing.py`
- `tests/integration/infrastructure/persistence/test_file_ingestion.py`
- `tests/integration/infrastructure/persistence/test_hybrid_retrieval.py`
- `tests/integration/infrastructure/persistence/test_live_investigations.py`
- `tests/integration/infrastructure/persistence/test_postgresql_chunk_plan_repository.py`
- `tests/integration/infrastructure/persistence/test_postgresql_document_repository.py`
- `tests/integration/infrastructure/persistence/test_rag_workflow.py`
- `tests/integration/infrastructure/persistence/test_retrieval_evaluation.py`
- `tests/integration/infrastructure/persistence/test_retrieval_optimization.py`
- `tests/integration/infrastructure/persistence/test_schema.py`
- `tests/integration/infrastructure/persistence/test_semantic_retrieval.py`
- `tests/integration/migrations/test_migrations.py`
- `tests/unit/application/conftest.py`
- `tests/unit/application/test_answer_question.py`
- `tests/unit/application/test_chunk_plan_validation.py`
- `tests/unit/application/test_chunking.py`
- `tests/unit/application/test_document_lifecycle.py`
- `tests/unit/application/test_embedding_storage_values.py`
- `tests/unit/application/test_index_document_version.py`
- `tests/unit/application/test_investigations.py`
- `tests/unit/application/test_provider_diagnostics.py`
- `tests/unit/application/test_rrf.py`
- `tests/unit/application/test_search_evidence.py`
- `tests/unit/domain/test_document_version.py`
- `tests/unit/evaluation/test_answer_quality.py`
- `tests/unit/evaluation/test_chunking_comparison.py`
- `tests/unit/evaluation/test_identity_and_answer_values.py`
- `tests/unit/evaluation/test_persistent_answer_runner.py`
- `tests/unit/evaluation/test_retrieval.py`
- `tests/unit/infrastructure/embeddings/test_gemini_provider.py`
- `tests/unit/infrastructure/ingestion/test_file_adapters.py`
- `tests/unit/infrastructure/ingestion/test_twowiki_adapter.py`
- `tests/unit/infrastructure/ingestion/test_txt_adapter.py`
- `tests/unit/infrastructure/persistence/test_in_memory_document_repository.py`
- `tests/unit/infrastructure/test_cloud_deploy.py`
- `tests/unit/infrastructure/test_config.py`
- `tests/unit/infrastructure/test_container_runtime.py`
- `tests/unit/infrastructure/test_document_management_api.py`
- `tests/unit/infrastructure/test_gemini_answer_generator.py`
- `tests/unit/infrastructure/test_gemini_investigation_model.py`
- `tests/unit/infrastructure/test_gemini_investigation_protocol.py`
- `tests/unit/infrastructure/test_investigation_api.py`
- `tests/unit/infrastructure/test_migration_entrypoint.py`
- `tests/unit/infrastructure/test_rag_api.py`
- `tests/unit/infrastructure/test_rag_bootstrap.py`
- `tests/unit/infrastructure/test_web_ui.py`
- `tests/unit/ingestion/test_corpus_processing.py`
- `uv.lock`
