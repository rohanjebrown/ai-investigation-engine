# Multi-step investigation verification — 28 October 2026

## Scope

This checkpoint records the first bounded multi-step investigation vertical slice. The application can issue a sequence of evidence searches, pass their results to Gemini for another action, and return an answer whose citations resolve to evidence returned by those searches. It is not a general autonomous agent, a quality evaluation, or a claim that citations semantically prove every answer claim.

The live checks used the pinned 2WikiMultihopQA development dataset revision and selected context passages only. Questions, answers, and gold supporting facts were not indexed. Indexing and retrieval used disposable local PostgreSQL with pgvector. No Cloud SQL database was contacted. Provider retries were disabled; the live generation calls were paced to the user-reported Flash free-tier limit.

## Live model check

The live generation and embedding configuration was `gemini-3.6-flash` and `gemini-embedding-2`, with 768-dimensional embeddings. The generation model is a configurable integration choice for this verification, not a permanent production model decision. The project’s Free tier and quota limits were reported by the user and could not be independently inspected in AI Studio during this run.

### Two-hop compositional case

The branching case used example `298f23b8088a11ebbd6eac1f6bf848b6` from `xanhho/2WikiMultihopQA`, `dev`, at revision `612bc5039a457880d9e7d84c3b0a4cf154b70e4f` (pinned file SHA-256 `c0d8b60b9026b728fb07ad74c5252a0f188f6942e8ba5c02df4dfa369502ea8d`). It indexed seven context passages, including four useful passages and three distractors.

Question: “Which film has the director who was born later, El Extraño Viaje or Love In Pawn?”

The actual model returned the supported conclusion that **El Extraño Viaje** has the later-born director, comparing Fernando Fernán Gómez (born 28 August 1921) with Charles Saunders (born 8 April 1904). Its four search calls found the two film/director links and the two biographies; its final answer cited all four retrieved evidence identifiers, which the application resolved to the registered passage versions and source locators.

| Observed usage | Count |
|---|---:|
| Search calls | 4 |
| Generation requests | 5 |
| Query embedding requests | 4 |
| Passage indexing embedding requests | 7 |
| Total embedding `embed_content` requests | 11 |
| Token-count requests | 11 |

One intermediate query combined the Love In Pawn branch with the already-discovered Fernando Fernán Gómez entity. The model then searched the Love In Pawn branch and recovered the correct comparison. This illustrates that orchestration is dynamic but is not proof of robust planning across questions.

### Polish-Russian War case and report limitation

The other live case asked for the mother of the director of *Polish-Russian War (Film)*. During this run, the HTTP endpoint returned an answer containing Xawery Żuławski and Małgorzata Braunek, and the test assertions for the live response completed. The process then failed while printing a Unicode report to the Windows `cp1252` console. The report did not preserve exact request counts or full source details. This case is therefore recorded as a successful live response with a reporting/harness failure, not as a cleanly completed pytest pass. It was not rerun because repeating it could exceed the aggregate live-request budget.

Across both model cases the configured per-case caps imply at most 11 generation requests and at most 21 embedding and token-count requests. The exact aggregate for the first case was not recovered. No retries or model substitutions were made.

## Browser replay

A separate headless Edge check replayed the same pinned dataset passages through a local FastAPI application using a deterministic result fixture. It verified the investigation mode, the `/investigations` endpoint, two rendered search steps, the answer, and expansion of citation source details. This check made no Gemini calls and does not independently verify model generation.

## Automated verification

| Command | Result |
|---|---|
| `uv --cache-dir .cache/uv run --no-sync pytest tests/unit -q` | 425 passed; one Google SDK deprecation warning |
| `uv --cache-dir .cache/uv run --no-sync pytest tests/integration -q` | 158 passed, 3 skipped; disposable local PostgreSQL; live checks opt-in |
| `node --test tests/frontend/test_web.cjs` | 26 passed |
| Opt-in headless Edge replay | 1 passed, 2 deselected |
| Opt-in live branching case | 1 passed, 2 deselected; real Gemini and local PostgreSQL |
| Opt-in first live case | Response assertions completed; pytest then failed on Windows console encoding while printing Unicode report |

The skipped integration cases include opt-in external/live checks. The full integration run did not call Gemini. A Google Gen AI SDK deprecation warning about `_UnionGenericAlias` appeared in Python test runs; it did not fail tests.

## Limits and follow-up

- Structural citation resolution is checked against actual retrieved evidence IDs and trusted source records. Semantic citation support, answer correctness at scale, contradiction handling, and abstention quality still require evaluation.
- Only two live questions were exercised. This is a smoke verification, not a benchmark or evidence of free-tier quota reliability.
- The selected generation model and production policy remain configurable; no permanent production-model approval is inferred here.
- The browser replay uses deterministic orchestration data; only the live endpoint tests exercised actual Gemini.
- The 40-question automated evaluation must be extended to measure multi-step answer, citation, abstention, contradiction, cost, and latency behavior.
