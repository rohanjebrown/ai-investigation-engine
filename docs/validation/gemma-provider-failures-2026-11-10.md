# Gemma provider failure investigation — 2026-11-10

The interrupted ten-question run has five persisted attempts: one answered, three HTTP 500 failures, and one HTTP 429. Five selected questions have no persisted outcome. The hard interruption did not capture whether another request was in flight. The completed answer and its citations require factual review; provider failures are not reasoning failures.

The sequential SDK diagnostics used `gemma-4-26b-a4b-it`, google-genai 2.33.0, a 30-second request timeout, SDK attempts=1, and at least 5.1 seconds between calls. The two JSONL files in `reports/analysis/gemma-provider-diagnostics-2026-11-10/` contain each attempted request and sanitized outcome. Character counts describe the question or contents representation, not the complete wire request including tools and instructions. No provider request ID was available in these records.

| Experiment | Requests | Success | HTTP 500 | HTTP 429 |
| --- | ---: | ---: | ---: | ---: |
| A: basic default thinking, no tools | 1 | 0 | 1 | 0 |
| B: same basic request, minimal thinking | 1 | 1 | 0 | 0 |
| C: initial native tool call | 2 | 1 | 1 | 0 |
| D: genuine function-response continuation, sufficient / insufficient fixture | 2 | 2 | 0 | 0 |
| E: application adapter initial request | 1 | 1 | 0 | 0 |

The two D calls both requested another search. Their simplified diagnostic configuration is not the application prompt and does not establish reasoning correctness. The A failure establishes that native tools and function-response history are not necessary for a 500. Failures and successes across C and E, and the small A/B sample, do not establish a configuration or thinking-level cause. A transient provider issue is plausible but unproven. No provider-side logs or controlled request-size sweep were available.

The adapter makes one SDK request per model turn and marks 500/502/503/504 retryable; the SDK itself is configured for one attempt. The evaluator counts provider attempts. A retry implementation would need to reserve and report each provider RPC inside the run-wide budget, preserve the same signed history and turn, honor cancellation and the whole-investigation deadline, and distinguish 429 rate limits from daily quota exhaustion. A failed request may already have consumed provider resources. Automatic retries are not enabled until these accounting and quota conditions can be enforced and verified. No retrieval should be replayed as part of a retry of a generation request.

The evaluator now checkpoints summary and report files before an attempt and after every persisted attempt. It records a pending attempt separately from unattempted work and stops after recording a rate-limit or quota error. A hard kill leaves the most recent checkpoint marked running; the in-flight outcome and provider request count may be unknown. Checkpoints cannot make a multi-file write transaction atomic, and an interrupted partial JSONL line may need manual repair in a copy. Existing historical artifacts were not overwritten.

## Recovery limitation

The evaluator does not support validated in-place resume. The command below starts a **new child run** for the four recorded failed questions and all five without recorded outcomes, excluding the one successful question. It does not merge histories or automatically verify that corpus, provider configuration, source code, and cache remain compatible. The child selection has a new hash and retains the saved selection fingerprint as its parent. Before live execution, compare the new plan's corpus, configuration, selection, embedding profile and code hashes to the original manifest. The source code changed to add checkpointing, and the corpus snapshot was not frozen, so do not label the resulting observations as an exact continuation or silently combine them. A previously in-flight question could incur a duplicate provider request. The user must account for this and provider quotas before using the command.

```powershell
$source='reports/evaluation/structured/gemma4-expanded-default'
$selection=Get-Content -Raw "$source/selection.json" | ConvertFrom-Json
$rows=Get-Content "$source/answer_results.jsonl" | ForEach-Object { $_ | ConvertFrom-Json }
$completed=@($rows | Where-Object { $_.status -notin @('failed','incomplete') } | ForEach-Object { $_.question_id })
$idArgs=@()
foreach ($id in $selection.question_ids) { if ($id -notin $completed) { $idArgs += '--question-id'; $idArgs += $id } }
if ((Get-FileHash '.cache/evaluation/query-embeddings.json' -Algorithm SHA256).Hash -ne '439F89AD35E3E4CF5BF1EB8B31C81D5ECA164CBCF47DAF41BB9F4E90739B10C2') { throw 'Starting cache differs from original manifest' }
New-Item -ItemType Directory -Force '.cache/gemma4-expanded-recovery' | Out-Null
Copy-Item '.cache/evaluation/query-embeddings.json' '.cache/gemma4-expanded-recovery/query-embeddings.json'
$env:UV_CACHE_DIR='.cache/uv'
$env:INVESTIGATION_MODEL_PROVIDER='gemma4'
uv run --frozen python scripts/evaluate.py run --selection "$source/selection.json" @idArgs --cache .cache/gemma4-expanded-recovery/query-embeddings.json --expected-database investigation_stage --modes investigation_hybrid --allow-generation --max-generation-requests 30 --allow-live-embeddings --max-embedding-requests 40 --generation-rpm 12 --generation-tpm 200000 --embedding-rpm 60 --embedding-tpm 24000 --repetitions 1 --warmups 0 --concurrency 1 --timeout 900 --local-diagnostics --plan-only --output reports/evaluation/structured/gemma4-expanded-recovery-plan
```

After inspecting the plan against the original manifest, run the same final command with `--plan-only` removed and `--output reports/evaluation/structured/gemma4-expanded-recovery`. This explicitly retries recorded failures; use a different ID list to run only cases without persisted outcomes.

Recommendation: postpone broad evaluation and production selection until availability improves. A small controlled repetition of the basic and minimal configurations, with quota headroom, is needed to test the apparent difference. Do not infer model reasoning quality or production reliability from the single completed benchmark case or these transport probes.
