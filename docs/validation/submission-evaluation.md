# Submission evaluation — 29 October 2026

This report summarizes the frozen paired run, not a claim of production readiness.

Saved outcomes: **74/80** (40 selected questions × two modes). Stopped early: True. Generation attempts: 137. The outcome tables retain provider failures and unrun questions; the paired comparison excludes provider failures. No failed request was retried. Source audits, when attached, are attributed to Codex and bound to exact saved responses; they are not independent human review or an assessment of all answers.

[Machine-readable results and source audits](submission-answer-quality.json).

## Dataset and method

The pinned 2Wiki development processing experiment registered 125,760 occurrences and 56,680 exact Contents. The attested local evaluation corpus is smaller: 218 active source versions and 197 real 768-dimensional Gemini embeddings. All 40 frozen questions are selected, with identical corpus, gold annotations, generation model and semantic retrieval configuration in both modes. Investigation is allowed additional searches; it also has a larger context/evidence budget, so this is a workflow comparison, not an isolated causal reasoning experiment.

The five categories are simple factual, two-hop, three-plus-hop/bridge comparison, unanswerable and contradictory controls. Previously inspected development questions are not an unseen benchmark. Gold labels never enter model input. No document embeddings were regenerated; results are checkpointed per question/mode and failed calls are not retried.

Generation uses gemini-3.5-flash-lite, at least 4.2 seconds between requests, one attempt, and bounded output/context. The operator reported 15 RPM, 250k TPM and 500 RPD; remaining project usage is unavailable. The run has a 300-generation-attempt cap and 240-query-embedding cap. Quotas are project-scoped, not independently verified here. A provider failure stops the run cleanly. No billing change or paid batch API is used.

## Retrieval baseline

Historical Task 15: Recall@5 0.875; Recall@10 0.9265; Recall@20 0.9412; MRR@20 0.9853; complete supporting-evidence coverage@20 0.8235. Semantic and hybrid had identical measured coverage; only 4/8 bridge comparisons had complete support at K=20. These are historical real-embedding retrieval metrics, not newly measured answer accuracy or evidence of hybrid superiority. [Original baseline](../../reports/evaluation/task15-2026-10-28/retrieval.json).

## Answer and citation results

Answer correctness uses the existing conservative normalized gold/alias matcher; unscorable prose is not silently correct. Strict citation precision/recall measure gold source-occurrence and supporting-fact matching. Structural citation validity only checks identifiers; none of these establishes semantic entailment. Unreviewed semantic support remains requires_review. Incomplete means operational limits, while insufficient evidence is a model conclusion.

| Mode | Evaluated | Outcomes | Strict citation P / R | Evidence recall | Mean latency ms |
| --- | ---: | --- | --- | ---: | ---: |
| single_step | 40 | {'answer_requires_human_review': 1, 'correct_abstention': 5, 'correct_answer': 16, 'correct_conflict': 1, 'incorrect_abstention': 11, 'missed_contradiction': 3, 'not_run': 3} | 1.0 / 0.640625 | 0.8671875 | 6809.434075675027 |
| multi_step | 40 | {'correct_abstention': 4, 'correct_answer': 23, 'correct_conflict': 4, 'incomplete': 4, 'incorrect_abstention': 1, 'not_run': 3, 'provider_failure': 1} | 1.0 / 0.78125 | 0.9921875 | 17663.742586111362 |

### Usage by mode

| Mode | Search calls | Generation attempts | Prompt / candidate / thinking tokens | Complete gold evidence cases |
| --- | ---: | ---: | --- | ---: |
| single_step | 37 | 37 | [38794, 1717, 0] | 23 |
| multi_step | 66 | 98 | [307956, 3759, 0] | 31 |

Tokens cover returned usage metadata; failed requests may not supply usage. Provider attempt accounting in execution below is authoritative. No semantic support is imputed to unreviewed answers.

### Paired descriptive comparison

```json
[
  {
    "baseline": "quick-answer-real-gemini",
    "other": "investigation-real-gemini",
    "paired_cases": 36,
    "paired_question_ids": [
      "2wiki:8813f87c0bdd11eba7f7acde48001122",
      "simple:8813f87c0bdd11eba7f7acde48001122",
      "2wiki:e2a3bf2a0bdd11eba7f7acde48001122",
      "simple:e2a3bf2a0bdd11eba7f7acde48001122",
      "2wiki:0cd3bdea0bde11eba7f7acde48001122",
      "simple:0cd3bdea0bde11eba7f7acde48001122",
      "2wiki:f9dcb4a60bda11eba7f7acde48001122",
      "simple:f9dcb4a60bda11eba7f7acde48001122",
      "2wiki:019bed300bde11eba7f7acde48001122",
      "simple:019bed300bde11eba7f7acde48001122",
      "2wiki:298f23b8088a11ebbd6eac1f6bf848b6",
      "2wiki:05f8a691087b11ebbd67ac1f6bf848b6",
      "2wiki:645c9a5a08c311ebbd8dac1f6bf848b6",
      "2wiki:5bec3cd408a711ebbd7fac1f6bf848b6",
      "2wiki:464cfa460bd911eba7f7acde48001122",
      "simple:464cfa460bd911eba7f7acde48001122",
      "2wiki:c05e16a40bdd11eba7f7acde48001122",
      "simple:c05e16a40bdd11eba7f7acde48001122",
      "2wiki:2935f1640bda11eba7f7acde48001122",
      "simple:2935f1640bda11eba7f7acde48001122",
      "2wiki:3f857208086c11ebbd61ac1f6bf848b6",
      "2wiki:dfe93efe0bdd11eba7f7acde48001122",
      "2wiki:1d890623088711ebbd6dac1f6bf848b6",
      "2wiki:2207e9060bda11eba7f7acde48001122",
      "2wiki:7abc1e3008d511ebbd98ac1f6bf848b6",
      "2wiki:ae331827088b11ebbd6fac1f6bf848b6",
      "2wiki:a9cc5f240bdb11eba7f7acde48001122",
      "2wiki:6ebe05740bde11eba7f7acde48001122",
      "unanswerable:0",
      "contradictory:0",
      "unanswerable:1",
      "contradictory:1",
      "unanswerable:2",
      "contradictory:2",
      "unanswerable:3",
      "contradictory:3"
    ],
    "outcome_changes": [
      {
        "id": "2wiki:e2a3bf2a0bdd11eba7f7acde48001122",
        "baseline": "incorrect_abstention",
        "other": "correct_answer"
      },
      {
        "id": "2wiki:0cd3bdea0bde11eba7f7acde48001122",
        "baseline": "correct_answer",
        "other": "incomplete"
      },
      {
        "id": "2wiki:298f23b8088a11ebbd6eac1f6bf848b6",
        "baseline": "incorrect_abstention",
        "other": "correct_answer"
      },
      {
        "id": "2wiki:645c9a5a08c311ebbd8dac1f6bf848b6",
        "baseline": "incorrect_abstention",
        "other": "correct_answer"
      },
      {
        "id": "2wiki:5bec3cd408a711ebbd7fac1f6bf848b6",
        "baseline": "incorrect_abstention",
        "other": "correct_answer"
      },
      {
        "id": "2wiki:464cfa460bd911eba7f7acde48001122",
        "baseline": "incorrect_abstention",
        "other": "incomplete"
      },
      {
        "id": "2wiki:dfe93efe0bdd11eba7f7acde48001122",
        "baseline": "incorrect_abstention",
        "other": "correct_answer"
      },
      {
        "id": "2wiki:1d890623088711ebbd6dac1f6bf848b6",
        "baseline": "incorrect_abstention",
        "other": "correct_answer"
      },
      {
        "id": "2wiki:2207e9060bda11eba7f7acde48001122",
        "baseline": "incorrect_abstention",
        "other": "incomplete"
      },
      {
        "id": "2wiki:7abc1e3008d511ebbd98ac1f6bf848b6",
        "baseline": "incorrect_abstention",
        "other": "correct_answer"
      },
      {
        "id": "2wiki:ae331827088b11ebbd6fac1f6bf848b6",
        "baseline": "incorrect_abstention",
        "other": "correct_answer"
      },
      {
        "id": "2wiki:6ebe05740bde11eba7f7acde48001122",
        "baseline": "answer_requires_human_review",
        "other": "incomplete"
      },
      {
        "id": "contradictory:0",
        "baseline": "missed_contradiction",
        "other": "correct_conflict"
      },
      {
        "id": "contradictory:1",
        "baseline": "missed_contradiction",
        "other": "correct_conflict"
      },
      {
        "id": "contradictory:3",
        "baseline": "missed_contradiction",
        "other": "correct_conflict"
      }
    ],
    "answer_correctness_changes": [
      {
        "id": "2wiki:e2a3bf2a0bdd11eba7f7acde48001122",
        "baseline": "no_answer",
        "other": "correct"
      },
      {
        "id": "2wiki:0cd3bdea0bde11eba7f7acde48001122",
        "baseline": "correct",
        "other": "no_answer"
      },
      {
        "id": "2wiki:298f23b8088a11ebbd6eac1f6bf848b6",
        "baseline": "no_answer",
        "other": "correct"
      },
      {
        "id": "2wiki:645c9a5a08c311ebbd8dac1f6bf848b6",
        "baseline": "no_answer",
        "other": "correct"
      },
      {
        "id": "2wiki:5bec3cd408a711ebbd7fac1f6bf848b6",
        "baseline": "no_answer",
        "other": "correct"
      },
      {
        "id": "2wiki:dfe93efe0bdd11eba7f7acde48001122",
        "baseline": "no_answer",
        "other": "correct"
      },
      {
        "id": "2wiki:1d890623088711ebbd6dac1f6bf848b6",
        "baseline": "no_answer",
        "other": "correct"
      },
      {
        "id": "2wiki:7abc1e3008d511ebbd98ac1f6bf848b6",
        "baseline": "no_answer",
        "other": "correct"
      },
      {
        "id": "2wiki:ae331827088b11ebbd6fac1f6bf848b6",
        "baseline": "no_answer",
        "other": "correct"
      },
      {
        "id": "2wiki:6ebe05740bde11eba7f7acde48001122",
        "baseline": "human_review",
        "other": "no_answer"
      }
    ],
    "strict_evidence_recall_delta": 0.125,
    "strict_citation_recall_delta": 0.140625,
    "latency_delta_ms": 10897.51388333429,
    "generation_request_delta": 1.7222222222222223,
    "interpretation": "Paired descriptive differences only; no statistical significance or superiority is inferred."
  }
]
```


### Per-category outcomes

| Mode | Category | Evaluated | Outcomes |
| --- | --- | ---: | --- |
| single_step | contradictory | 6 | {'correct_conflict': 1, 'missed_contradiction': 3, 'not_run': 2} |
| single_step | simple_factual | 8 | {'correct_answer': 7, 'incorrect_abstention': 1} |
| single_step | three_plus_hop | 8 | {'correct_answer': 2, 'incorrect_abstention': 6} |
| single_step | two_hop | 12 | {'answer_requires_human_review': 1, 'correct_answer': 7, 'incorrect_abstention': 4} |
| single_step | unanswerable | 6 | {'correct_abstention': 5, 'not_run': 1} |
| multi_step | contradictory | 6 | {'correct_conflict': 4, 'not_run': 2} |
| multi_step | simple_factual | 8 | {'correct_answer': 7, 'incorrect_abstention': 1} |
| multi_step | three_plus_hop | 8 | {'correct_answer': 8} |
| multi_step | two_hop | 12 | {'correct_answer': 8, 'incomplete': 4} |
| multi_step | unanswerable | 6 | {'correct_abstention': 4, 'not_run': 1, 'provider_failure': 1} |

## Usage and configuration

Exact saved configuration, attempts, tokens and timing:

```json
{
  "kind": "persistent_local_frozen_sample",
  "generation_model_requested": "gemini-3.5-flash-lite",
  "investigation_guidance_version": "grounding-v4-scoped-conflicts",
  "generation_budget_model_scope": "gemini-3.5-flash-lite",
  "generation_model_configuration_source": "explicit evaluation CLI configuration",
  "max_output_tokens": 1024,
  "generation_request_cap": 300,
  "generation_requests_observed": 137,
  "generation_requests_by_mode": {
    "single_step": 37,
    "multi_step": 100
  },
  "assumed_daily_rpd_limit": 500,
  "remaining_rpd_independently_verified": false,
  "prior_generation_requests_this_daily_window": 0,
  "prior_single_step_requests_this_daily_window": 0,
  "prior_multi_step_requests_this_daily_window": 0,
  "prior_embedding_request_totals": "unknown: the first paired response was lost before its local ledger was written",
  "reserved_daily_requests": 200,
  "active_quota_not_independently_api_readable": true,
  "generation_request_interval_seconds": 4.2,
  "provider_retries": 0,
  "embedding_model": "gemini-embedding-2",
  "embedding_dimensions": 768,
  "embedding_configuration": {
    "provider": "gemini_developer_api",
    "model_id": "gemini-embedding-2",
    "dimensions": 768,
    "document_format": "gemini_no_title_v1",
    "normalization": "none"
  },
  "embedding_inputs_observed": 104,
  "embedding_text_cap": 240,
  "embedding_request_counts": {
    "count_tokens": 104,
    "embed_content": 104
  },
  "counted_embedding_tokens": 1841,
  "database": {
    "documents": 218,
    "document_versions": 218,
    "contents": 197,
    "version_chunk_plans": 218,
    "chunk_memberships": 218,
    "chunk_embeddings": 197,
    "active_versions": 218,
    "active_searchable_occurrences": 218,
    "incomplete_planned_versions": 0,
    "database_bytes": 10196671,
    "migration": "0002_embedding_storage",
    "postgres_version": "18.4 (Debian 18.4-1.pgdg13+1)",
    "vector_dimensions": 768
  },
  "verified_manifest_occurrences": 218,
  "test_database": "persistent local PostgreSQL 18.4/pgvector evaluation database",
  "corpus_fingerprint": "93c5ccb10f7fea8dcfbebdf52adc6e3c493f2df71eddb9452383982b4e78cc4e",
  "suite_fingerprint": "3506da0cccb341fbde307fad8a57b8ae0d97cb42495998681759a96fe132bda0",
  "selected_question_ids": [
    "2wiki:8813f87c0bdd11eba7f7acde48001122",
    "simple:8813f87c0bdd11eba7f7acde48001122",
    "2wiki:e2a3bf2a0bdd11eba7f7acde48001122",
    "simple:e2a3bf2a0bdd11eba7f7acde48001122",
    "2wiki:0cd3bdea0bde11eba7f7acde48001122",
    "simple:0cd3bdea0bde11eba7f7acde48001122",
    "2wiki:f9dcb4a60bda11eba7f7acde48001122",
    "simple:f9dcb4a60bda11eba7f7acde48001122",
    "2wiki:019bed300bde11eba7f7acde48001122",
    "simple:019bed300bde11eba7f7acde48001122",
    "2wiki:298f23b8088a11ebbd6eac1f6bf848b6",
    "2wiki:05f8a691087b11ebbd67ac1f6bf848b6",
    "2wiki:645c9a5a08c311ebbd8dac1f6bf848b6",
    "2wiki:5bec3cd408a711ebbd7fac1f6bf848b6",
    "2wiki:464cfa460bd911eba7f7acde48001122",
    "simple:464cfa460bd911eba7f7acde48001122",
    "2wiki:c05e16a40bdd11eba7f7acde48001122",
    "simple:c05e16a40bdd11eba7f7acde48001122",
    "2wiki:2935f1640bda11eba7f7acde48001122",
    "simple:2935f1640bda11eba7f7acde48001122",
    "2wiki:3f857208086c11ebbd61ac1f6bf848b6",
    "2wiki:dfe93efe0bdd11eba7f7acde48001122",
    "2wiki:1d890623088711ebbd6dac1f6bf848b6",
    "2wiki:2207e9060bda11eba7f7acde48001122",
    "2wiki:7abc1e3008d511ebbd98ac1f6bf848b6",
    "2wiki:ae331827088b11ebbd6fac1f6bf848b6",
    "2wiki:a9cc5f240bdb11eba7f7acde48001122",
    "2wiki:6ebe05740bde11eba7f7acde48001122",
    "unanswerable:0",
    "contradictory:0",
    "unanswerable:1",
    "contradictory:1",
    "unanswerable:2",
    "contradictory:2",
    "unanswerable:3",
    "contradictory:3",
    "unanswerable:4",
    "contradictory:4",
    "unanswerable:5",
    "contradictory:5"
  ],
  "task15_semantic_hybrid_metrics_equal": true,
  "cloud_sql_contacted": false,
  "budget_note": "Daily cap is the prior user-reported allowance minus a reserved request. Previously known same-window requests are charged before new calls. Investigation limits are independently configurable and must allow one final-decision turn after searches.",
  "incomplete_reason": "Stopped after a provider failure; sanitized diagnostic is saved in the mode checkpoint.",
  "generation_provider_requests": {
    "quick_answer": 37,
    "investigation": 100
  },
  "total_generation_attempts_including_prior": 137,
  "generation_failures": [
    {
      "operation": "generate_content",
      "model_id": "gemini-3.5-flash-lite",
      "http_status": 503,
      "category": "availability",
      "retryable": true,
      "rate_or_quota_limit": false,
      "request_count": 100,
      "provider_code": "UNAVAILABLE",
      "error_reason": "provider_rejection",
      "investigation_step": 2
    }
  ]
}
```

## Representative outcomes and failure analysis

The following representatives are selected by outcome after execution; all cases remain in the machine-readable report. Failure labels are diagnostic evidence, not proof of an unobserved model reasoning process.

- **single_step / correct_answer / 2wiki:8813f87c0bdd11eba7f7acde48001122**: Who is the mother of the director of film Polish-Russian War (Film)?
  Answer: 'The mother of Xawery Żuławski, the director of the film Polish-Russian War, is actress Małgorzata Braunek.'. Status: answered. Analysis: None. Independent support assessment: {'answer_correctness': 'correct', 'citation_identifier_validity': 'valid', 'gold_supporting_fact_matching': {'strict_fact_count': 2, 'strict_precision': 1.0, 'strict_recall': 1.0}, 'semantic_evidence_support': 'supported', 'cross_document_entity_identity': 'established', 'basis': 'explicit_source_review'}.
  Search trace: [{'sequence': 1, 'query': None, 'evidence_ids': ['E1', 'E2', 'E3', 'E4', 'E5'], 'result_count': 5, 'succeeded': True}]. Evidence sample: [{'id': 'E1', 'title': 'Polish-Russian War (film)', 'excerpt': 'Polish-Russian War (Wojna polsko-ruska) is a 2009 Polish film directed by Xawery Żuławski based on the novel Polish-Russian War under the white-red flag by Dorota Masłowska.'}, {'id': 'E2', 'title': 'Xawery Żuławski', 'excerpt': 'Xawery Żuławski (born 22 December 1971 in Warsaw) is a Polish film director. In 1995 he graduated National Film School in Łódź. He is the son of actress Małgorzata Braunek and director Andrzej Żuławski. His second feature "Wojna polsko-ruska" (2009), adapted from the controversial best-selling novel by Dorota Masłowska, won First Prize in the New P'}]
- **single_step / incorrect_abstention / 2wiki:e2a3bf2a0bdd11eba7f7acde48001122**: When did John V, Prince Of Anhalt-Zerbst's father die?
  Answer: None. Status: insufficient_evidence. Analysis: {'classification': 'search_planning_or_retrieval_failure', 'observation': 'Strictly retrieved 1 of 2 required facts.', 'hypothesis': 'Search logs identify query order and returned evidence, but do not establish whether planning or ranking caused the gap.'}. Independent support assessment: {'answer_correctness': 'no_answer', 'citation_identifier_validity': 'not_applicable', 'gold_supporting_fact_matching': {'strict_fact_count': 0, 'strict_precision': None, 'strict_recall': 0.0}, 'semantic_evidence_support': 'not_applicable', 'cross_document_entity_identity': 'not_applicable', 'basis': 'semantic_support_not_automatically_scored'}.
  Search trace: [{'sequence': 1, 'query': None, 'evidence_ids': ['E1', 'E2', 'E3', 'E4', 'E5'], 'result_count': 5, 'succeeded': True}]. Evidence sample: [{'id': 'E1', 'title': 'John V, Prince of Anhalt-Zerbst', 'excerpt': 'John V of Anhalt-Zerbst (Dessau, 4 September 1504 – Zerbst, 4 February 1551), was a German prince of the House of Ascania and ruler of the principality of Anhalt-Dessau. From 1544, he assumed rule of the re-created principality of Anhalt-Zerbst. John was the second (but eldest surviving) son of Ernest I, Prince of Anhalt-Dessau, by his wife Margare'}, {'id': 'E2', 'title': 'Karl I, Prince of Anhalt-Zerbst', 'excerpt': 'Karl I of Anhalt- Zerbst( 17 November 1534 in Dessau – 4 May 1561 in Zerbst), was a German prince of the House of Ascania and ruler of the principality of Anhalt- Zerbst. He was the eldest son of John V, Prince of Anhalt- Zerbst, by his wife Margaret, daughter of Joachim I Nestor, Elector of Brandenburg.'}]
- **single_step / answer_requires_human_review / 2wiki:6ebe05740bde11eba7f7acde48001122**: Where was the place of death of Anastasia Of Serbia's husband?
  Answer: "Anastasia of Serbia's husband, Stefan Nemanja, died at Mount Athos.". Status: answered. Analysis: {'classification': 'human_review_required', 'observation': 'Observed outcome: answer_requires_human_review.', 'hypothesis': 'The available deterministic annotations cannot settle this response.'}. Independent support assessment: {'answer_correctness': 'human_review', 'citation_identifier_validity': 'valid', 'gold_supporting_fact_matching': {'strict_fact_count': 2, 'strict_precision': 1.0, 'strict_recall': 1.0}, 'semantic_evidence_support': 'requires_review', 'cross_document_entity_identity': 'requires_review', 'basis': 'semantic_support_not_automatically_scored'}.
  Search trace: [{'sequence': 1, 'query': None, 'evidence_ids': ['E1', 'E2', 'E3', 'E4', 'E5'], 'result_count': 5, 'succeeded': True}]. Evidence sample: [{'id': 'E2', 'title': 'Anastasia of Serbia', 'excerpt': 'Ana ; 1196–d. June 22, 1200) was the princess consort of the Serbian Principality as the wife of Stefan Nemanja (r. 1166–1196). She was of noble descent. Ana took monastic vows in 1196 and was baptized "Anastasia", after Anastasia of Sirmium. She is venerated by the Serbian Orthodox Church as Saint Anastasija (Света Анастасија) with her feast day o'}, {'id': 'E3', 'title': 'Stefan Nemanja', 'excerpt': 'Stefan Nemanja (Serbian Cyrillic: Стефан Немања, ; 1113 – 13 February 1199) was the Grand Prince (Veliki Župan) of the Serbian Grand Principality (also known as Raška, lat. " Rascia") from 1166 to 1196. A member of the Vukanović dynasty, Nemanja founded the Nemanjić dynasty, and is remembered for his contributions to Serbian culture and history, fo'}]
- **single_step / correct_abstention / unanswerable:0**: On what date was fictional station Unlisted-0 constructed?
  Answer: None. Status: insufficient_evidence. Analysis: None. Independent support assessment: {'answer_correctness': 'not_applicable', 'citation_identifier_validity': 'not_applicable', 'gold_supporting_fact_matching': {'strict_fact_count': 0, 'strict_precision': None, 'strict_recall': None}, 'semantic_evidence_support': 'not_applicable', 'cross_document_entity_identity': 'not_applicable', 'basis': 'semantic_support_not_automatically_scored'}.
  Search trace: [{'sequence': 1, 'query': None, 'evidence_ids': ['E1', 'E2', 'E3', 'E4', 'E5'], 'result_count': 5, 'succeeded': True}]. Evidence sample: [{'id': 'E1', 'title': 'unanswerable-0', 'excerpt': 'Fictional station Unlisted-0 records wind speed. Its construction date is not recorded.'}, {'id': 'E2', 'title': 'unanswerable-1', 'excerpt': 'Fictional station Unlisted-1 records wind speed. Its construction date is not recorded.'}]
- **single_step / missed_contradiction / contradictory:0**: What is the current access code of fictional vault Conflict-0?
  Answer: 'The sources make incompatible claims about the current access code of fictional vault Conflict-0, stating it is either amber or violet.'. Status: answered. Analysis: {'classification': 'reasoning_or_synthesis_failure', 'observation': 'Observed outcome: missed_contradiction.', 'hypothesis': 'Gold matching and output status expose the failure class but do not prove its semantic cause.'}. Independent support assessment: {'answer_correctness': 'not_applicable', 'citation_identifier_validity': 'valid', 'gold_supporting_fact_matching': {'strict_fact_count': 2, 'strict_precision': 1.0, 'strict_recall': 1.0}, 'semantic_evidence_support': 'requires_review', 'cross_document_entity_identity': 'requires_review', 'basis': 'semantic_support_not_automatically_scored'}.
  Search trace: [{'sequence': 1, 'query': None, 'evidence_ids': ['E1', 'E2', 'E3', 'E4', 'E5'], 'result_count': 5, 'succeeded': True}]. Evidence sample: [{'id': 'E1', 'title': 'contradiction-0-a', 'excerpt': 'The current access code of fictional vault Conflict-0 is amber.'}, {'id': 'E2', 'title': 'contradiction-0-b', 'excerpt': 'The current access code of fictional vault Conflict-0 is violet.'}]
- **single_step / correct_conflict / contradictory:2**: What is the current access code of fictional vault Conflict-2?
  Answer: 'The sources make incompatible claims about the current access code of fictional vault Conflict-2: one source states it is violet [E1], while another states it is amber [E2].'. Status: answered. Analysis: None. Independent support assessment: {'answer_correctness': 'not_applicable', 'citation_identifier_validity': 'valid', 'gold_supporting_fact_matching': {'strict_fact_count': 2, 'strict_precision': 1.0, 'strict_recall': 1.0}, 'semantic_evidence_support': 'requires_review', 'cross_document_entity_identity': 'requires_review', 'basis': 'semantic_support_not_automatically_scored'}.
  Search trace: [{'sequence': 1, 'query': None, 'evidence_ids': ['E1', 'E2', 'E3', 'E4', 'E5'], 'result_count': 5, 'succeeded': True}]. Evidence sample: [{'id': 'E1', 'title': 'contradiction-2-b', 'excerpt': 'The current access code of fictional vault Conflict-2 is violet.'}, {'id': 'E2', 'title': 'contradiction-2-a', 'excerpt': 'The current access code of fictional vault Conflict-2 is amber.'}]
- **multi_step / correct_answer / 2wiki:8813f87c0bdd11eba7f7acde48001122**: Who is the mother of the director of film Polish-Russian War (Film)?
  Answer: 'The mother of Xawery Żuławski, the director of the film Polish-Russian War, is actress Małgorzata Braunek.'. Status: answered. Analysis: None. Independent support assessment: {'answer_correctness': 'correct', 'citation_identifier_validity': 'valid', 'gold_supporting_fact_matching': {'strict_fact_count': 2, 'strict_precision': 1.0, 'strict_recall': 1.0}, 'semantic_evidence_support': 'requires_review', 'cross_document_entity_identity': 'requires_review', 'basis': 'semantic_support_not_automatically_scored'}.
  Search trace: [{'sequence': 1, 'query': 'Polish-Russian War Film director', 'evidence_ids': ['E1', 'E2', 'E3', 'E4', 'E5'], 'result_count': 5, 'duration_ms': 1568.6038999992888, 'succeeded': True, 'disposition': 'succeeded'}]. Evidence sample: [{'id': 'E1', 'title': 'Polish-Russian War (film)', 'excerpt': 'Polish-Russian War (Wojna polsko-ruska) is a 2009 Polish film directed by Xawery Żuławski based on the novel Polish-Russian War under the white-red flag by Dorota Masłowska.'}, {'id': 'E2', 'title': 'Xawery Żuławski', 'excerpt': 'Xawery Żuławski (born 22 December 1971 in Warsaw) is a Polish film director. In 1995 he graduated National Film School in Łódź. He is the son of actress Małgorzata Braunek and director Andrzej Żuławski. His second feature "Wojna polsko-ruska" (2009), adapted from the controversial best-selling novel by Dorota Masłowska, won First Prize in the New P'}]
- **multi_step / incomplete / 2wiki:0cd3bdea0bde11eba7f7acde48001122**: What is the award that the director of film Wearing Velvet Slippers Under A Golden Umbrella won?
  Answer: None. Status: incomplete. Analysis: {'classification': 'budget_exhaustion', 'observation': 'The workflow returned incomplete or recorded a configured resource limit.', 'hypothesis': 'The current per-run budgets ended the investigation before a final decision.'}. Independent support assessment: {'answer_correctness': 'no_answer', 'citation_identifier_validity': 'not_applicable', 'gold_supporting_fact_matching': {'strict_fact_count': 0, 'strict_precision': None, 'strict_recall': 0.0}, 'semantic_evidence_support': 'not_applicable', 'cross_document_entity_identity': 'not_applicable', 'basis': 'semantic_support_not_automatically_scored'}.
  Search trace: [{'sequence': 1, 'query': 'Wearing Velvet Slippers Under A Golden Umbrella director', 'evidence_ids': ['E1', 'E2', 'E3', 'E4', 'E5'], 'result_count': 5, 'duration_ms': 3228.7674999970477, 'succeeded': True, 'disposition': 'succeeded'}, {'sequence': 2, 'query': '"Maung Wunna" awards OR academy OR "Wearing Velvet"', 'evidence_ids': ['E1', 'E3', 'E4', 'E6', 'E7'], 'result_count': 5, 'duration_ms': 2312.5235000043176, 'succeeded': True, 'disposition': 'succeeded'}, {'sequence': 3, 'query': '"Maung Wunna" "Myanmar Motion Picture Academy"', 'evidence_ids': ['E3', 'E1', 'E7', 'E8', 'E6'], 'result_count': 5, 'duration_ms': 2328.085499990266, 'succeeded': True, 'disposition': 'succeeded'}]. Evidence sample: [{'id': 'E1', 'title': 'Wearing Velvet Slippers under a Golden Umbrella', 'excerpt': "Wearing Velvet Slippers under a Golden Umbrella (Pronounced as Katipa phanat see shwe htee hsaung) is a 1970 Burmese film directed by Maung Wunna starring Myat Mon, Myat Lay and Thet Naung. The film was Maung Wunna's first feature as director and he also wrote the screenplay, based on the novel of the same name by Maung Thara."}, {'id': 'E2', 'title': 'Kim Sung-su (director)', 'excerpt': 'Kim Sung- su( born November 15, 1961) is a South Korean film director, known mainly for the teen film" Beat", period epic" Musa" and the comedy" Please Teach Me English".'}]
- **multi_step / incorrect_abstention / simple:464cfa460bd911eba7f7acde48001122**: What is Ken Burns's place of birth?
  Answer: None. Status: insufficient_evidence. Analysis: {'classification': 'citation_failure', 'observation': 'Citations strictly cover 0 of 1 required facts.', 'hypothesis': 'Required evidence was available in the model-facing retrieved set but not fully cited.'}. Independent support assessment: {'answer_correctness': 'no_answer', 'citation_identifier_validity': 'not_applicable', 'gold_supporting_fact_matching': {'strict_fact_count': 0, 'strict_precision': None, 'strict_recall': 0.0}, 'semantic_evidence_support': 'not_applicable', 'cross_document_entity_identity': 'not_applicable', 'basis': 'semantic_support_not_automatically_scored'}.
  Search trace: [{'sequence': 1, 'query': 'Ken Burns place of birth', 'evidence_ids': ['E1', 'E2', 'E3', 'E4', 'E5'], 'result_count': 5, 'duration_ms': 1225.8079000021098, 'succeeded': True, 'disposition': 'succeeded'}, {'sequence': 2, 'query': '"Kenneth Lauren Burns" born place OR "New York" OR "Brooklyn" OR "Delaware" OR "Ann Arbor"', 'evidence_ids': ['E1', 'E2', 'E3', 'E4', 'E6'], 'result_count': 5, 'duration_ms': 3416.333700006362, 'succeeded': True, 'disposition': 'succeeded'}]. Evidence sample: [{'id': 'E1', 'title': 'Ken Burns', 'excerpt': 'Kenneth Lauren Burns (born 1953) is an American filmmaker, known for his style of using archival footage and photographs in documentary films. His widely known documentary series include "The Civil War" (1990), "Baseball" (1994), "Jazz" (2001), "The War" (2007),  (2009), "Prohibition" (2011), "The Roosevelts" (2014), "The Vietnam War" (2017), and "'}, {'id': 'E2', 'title': 'Thomas Jefferson (film)', 'excerpt': 'Thomas Jefferson is a 1997 two-part American documentary film directed and produced by Ken Burns. It covers the life and times of Thomas Jefferson, the 3rd President of the United States. In the film Jefferson is portrayed as renaissance man. Not only was he a dedicated public servant, but was also a writer, an inventor, and a noted architect. Burn'}]
- **multi_step / correct_abstention / unanswerable:0**: On what date was fictional station Unlisted-0 constructed?
  Answer: None. Status: insufficient_evidence. Analysis: None. Independent support assessment: {'answer_correctness': 'not_applicable', 'citation_identifier_validity': 'not_applicable', 'gold_supporting_fact_matching': {'strict_fact_count': 0, 'strict_precision': None, 'strict_recall': None}, 'semantic_evidence_support': 'not_applicable', 'cross_document_entity_identity': 'not_applicable', 'basis': 'semantic_support_not_automatically_scored'}.
  Search trace: [{'sequence': 1, 'query': 'Unlisted-0 fictional station constructed date', 'evidence_ids': ['E1', 'E2', 'E3', 'E4', 'E5'], 'result_count': 5, 'duration_ms': 2511.2716000003275, 'succeeded': True, 'disposition': 'succeeded'}, {'sequence': 2, 'query': 'Unlisted-0 construction date', 'evidence_ids': ['E1', 'E2', 'E3', 'E4', 'E5'], 'result_count': 5, 'duration_ms': 1390.4801000026055, 'succeeded': True, 'disposition': 'succeeded'}]. Evidence sample: [{'id': 'E1', 'title': 'unanswerable-0', 'excerpt': 'Fictional station Unlisted-0 records wind speed. Its construction date is not recorded.'}, {'id': 'E2', 'title': 'unanswerable-1', 'excerpt': 'Fictional station Unlisted-1 records wind speed. Its construction date is not recorded.'}]
- **multi_step / correct_conflict / contradictory:0**: What is the current access code of fictional vault Conflict-0?
  Answer: None. Status: conflicting_evidence. Analysis: None. Independent support assessment: {'answer_correctness': 'not_applicable', 'citation_identifier_validity': 'valid', 'gold_supporting_fact_matching': {'strict_fact_count': 2, 'strict_precision': 1.0, 'strict_recall': 1.0}, 'semantic_evidence_support': 'not_applicable', 'cross_document_entity_identity': 'not_applicable', 'basis': 'semantic_support_not_automatically_scored'}.
  Search trace: [{'sequence': 1, 'query': 'Conflict-0 vault access code', 'evidence_ids': ['E1', 'E2', 'E3', 'E4', 'E5'], 'result_count': 5, 'duration_ms': 1392.6284999906784, 'succeeded': True, 'disposition': 'succeeded'}]. Evidence sample: [{'id': 'E1', 'title': 'contradiction-0-b', 'excerpt': 'The current access code of fictional vault Conflict-0 is violet.'}, {'id': 'E2', 'title': 'contradiction-0-a', 'excerpt': 'The current access code of fictional vault Conflict-0 is amber.'}]
- **multi_step / provider_failure / unanswerable:4**: On what date was fictional station Unlisted-4 constructed?
  Answer: None. Status: provider_error. Analysis: {'classification': 'provider_failure', 'observation': 'Execution raised GenerationProviderError; no completed result exposed its evidence or citations.', 'hypothesis': 'The sanitized exception type does not identify the failing workflow stage or underlying provider/database cause.'}. Independent support assessment: {'answer_correctness': 'unavailable', 'citation_identifier_validity': 'unavailable', 'gold_supporting_fact_matching': {'strict_fact_count': None, 'strict_precision': None, 'strict_recall': None}, 'semantic_evidence_support': 'unavailable', 'cross_document_entity_identity': 'unavailable', 'basis': 'semantic_support_not_automatically_scored'}.
  Search trace: None. Evidence sample: []

## Reliability findings and limits

Earlier two-hop and bridge abstentions were accompanied by only 1/2 and 2/4 required facts in context: observed evidence gaps, not proof of a generator defect. A prior contradiction control supplied both gold facts but Quick Answer missed the disagreement. Earlier investigations exhausted one-search budgets; a provider HTTP 503 was recorded separately. Task 16D obtained a benchmark-correct nationality while the cited athlete passage did not establish the intended director identity. Its source review marked support unsupported and identity unresolved. [Preserved investigation](../../reports/evaluation/task16d-2026-10-28/analysis.md).

This milestone adds identity/context/conflict guidance, rejects duplicate identical claims masquerading as a conflict, and adds controlled identity workflow regressions. Fake providers verify evidence transport, citations, ownership and outcomes; they do not demonstrate improved Gemini reasoning. No expensive second LLM judge was added. Quick Answer retains its narrower outcome contract: disagreement may appear in text rather than a typed conflict. No claim of semantic grounding improvement or statistical superiority follows from benchmark answer matches alone.

## Verification and deployment

See [completion verification](completion-verification-2026-10-29.md) for exact unit/integration/JavaScript/lint/container results, earlier failed attempts, skipped opt-in cases, trigger changes and production blockers. Offline deterministic tests, disposable PostgreSQL tests, real-embedding retrieval and real Gemini generation are separate evidence classes. No production data was modified and no new application revision was deployed by this milestone.

## Reproduction and remaining work

The source artifact is reports/evaluation/completion-2026-10-29/answer-quality.json; per-mode question checkpoints, full report tables and the frozen selection are beside it. Run the existing persistent answer-quality script with that selection/output and matching configuration to load completed checkpoints. Do not rerun completed failures to improve scores. The source database remains pinned to migration 0002 for this historical comparison, distinct from the current production schema and BM25 experiments. No migration was applied to the evaluation database.

Further work needs independently reviewed semantic/entity support, a held-out question set, aggregate public quota protection, secret references/scoped IAM, and a real authorized push verifying Cloud Build deployment. Missing retrieval facts, search planning, ambiguous identities and provider availability remain substantive limitations.
