# Task 16D: entity identity, answer values, and grounding

## Result

The recorded answer **“American”** matches the benchmark's **“United States”** for this nationality question under a narrow evaluation-only alias. The cited evidence **does not establish the identity bridge needed to support that answer**. The new separate report therefore records answer correctness `correct`, citation IDs `valid`, annotated citation precision/recall 1.0, semantic evidence support `unsupported`, and cross-document identity `unresolved`.

“Unsupported” here means the cited passages do not establish the claim. It does not mean the nationality value has been proved false. Benchmark agreement is established; the director's real-world nationality/citizenship is not independently established by the cited corpus. Deployment preparation should remain on hold for authoritative factual-answer use.

The original Task 16C report, generated answer, citations, benchmark answers, corpus, and gold annotations were preserved. The new result is in `rescored-flash-lite/answer-quality.json`; its source hashes and model settings are recorded in `provenance.json`.

## Reconstructed investigation

Question: **What nationality is the director of film World And Time Enough?** Model: `gemini-3.5-flash-lite`. The saved response used four generation calls, three successful searches, and no retries.

| Step | Actual search | Evidence returned | Relevant observation |
| --- | --- | --- | --- |
| 1 | `World And Time Enough film director nationality` | E1 film; E2 Ken Burns; E3 Jason Moore; E4 James Cruze; E5 Robert Bresson | E1 identifies Eric Mueller as the director, but gives no nationality or biographical identity link. |
| 2 | `"Eric Mueller" director nationality` | E6 Eric C. Mueller rower; E1 film; E3 Jason Moore; E7/E8 Brian Kennedy occurrences | The query uses the name found in E1 and retains the director role. E6 is the new annotated nationality fact, but its occupation and biography do not link it to the film. |
| 3 | `"Eric Mueller" director "World and Time Enough" american` | E1, E6, E3, E7, E8 | This query retains both role and film title. It returns no new evidence identities or corroborating identity link. |
| Final | Answer `American`; citations E1/E6 | Both IDs resolve to retrieved occurrences | The answer transfers a nationality-related fact from the rower biography to the film director without establishing that they are the same person. |

The specific unsupported association is the final cross-source join: **E1's film → director name** combined with **E6's same-name biography → United States**. Internal model reasoning was not exposed, so its exact decision process cannot be observed. The output and citations require an identity assumption that the supplied sources do not establish.

This is not simply a loss of identifying words in queries: the second query already included the role, and the third included the work. The additional prompt guidance may help, but query context by itself did not prevent this observed failure.

## E1, E6, and identity

E1 names the director and gives the film's year and filming locations. E6 describes **Eric C. Mueller**, born November 6, 1970, an Olympic rower representing the United States, with a Wisconsin mechanical-engineering and rowing history. It does not mention the film, filmmaking, or an identifying relationship to E1.

The two excerpts cannot establish that their subjects are the same person. Different occupations alone also do not prove that they are different people: one person may hold multiple roles. The correct evidentiary assessment is **unresolved identity**, so the nationality cannot safely be transferred.

Independent primary-source research reinforces the need for disambiguation. The filmmaker's [own biography](https://ericmuellerphotography.com/about/) connects him explicitly to the film and describes a Minnesota background, English studies at Carleton, and film-production studies at Iowa. The rower's [World Rowing profile](https://worldrowing.com/athlete/7652) gives the 1970 birth date and United States sporting affiliation; [Wisconsin Athletics](https://uwbadgers.com/news/2006/10/19/From_Student_Athlete_to_Coach) documents his engineering degree and rowing career. These profiles provide distinct contextual identities, not an explicit cross-link equating them. The filmmaker's biography supports a US association but is not an explicit citizenship certification. These external sources were used for this audit only; none entered retrieval, generation, or the benchmark corpus.

## Retrieval, reasoning, and annotation limits

Retrieval returned a same-name biography with prominent nationality-related information. The evidence registry correctly preserved separate source occurrences; it does not merge entities by name. The model received intact excerpts, source titles, locations, and provenance, including the rower's occupation. The model then returned an answer whose grounding depended on the unestablished identity link. A generic identity-warning omission in the old prompt is a plausible contributor, not proof of the internal cause.

The existing benchmark annotation also has a semantic weakness. Its triples use the bare subject name `Eric Mueller`, and its gold supporting sentence for citizenship is the rower passage. Gold-source matching therefore rewards citation E6 instead of detecting the problem. The annotations contain no unambiguous person identifier or reviewed negative identity constraint. They cannot support a reliable automatic semantic identity score. No hardcoded name/occupation detector or LLM judge was added, and no gold answer or supporting fact was changed.

The report keeps four distinct observations: identifier validity, exact annotated fact matching, semantic evidence support, and cross-document identity. Full source matching never automatically becomes `supported`; unanswered/failed cases are separately not applicable or unavailable for grounding assessment.

## Answer normalization

The old scorer searched only the frozen acceptable answer `United States`. The concise value `American` did not contain that phrase, so it was classified as incorrect. In an explicit nationality question with this gold country, the country and demonym express the same expected answer value.

The new scorer `answer-values-v2-nationality-aliases` implements one reviewed rule, `nationality-united-states-american-v1`:

- Applies only to the question form `What nationality is/was …?` and the exact gold value `United States`.
- Accepts the whole direct answer value `American`, ignoring case, surrounding punctuation, and citation markers.
- Does not accept `South American`, `North American`, `Native American`, `American Samoa`, negations, or alternatives such as `American or Canadian`.
- Does not infer citizenship, residence, origin, British/UK equivalence, or other country/demonym mappings. It uses no fuzzy similarity.
- Records aliases, matched rule, and scorer version outside the unchanged gold answer/acceptable-answer list.

Only the reviewed nationality case qualifies in the current 40-case suite. This policy changes benchmark answer matching, not production generation or evidence-support judgments.

## Grounding guidance and reporting changes

Investigation guidance version `grounding-v2-entity-context` now tells the model to retain identifying context; compare roles, works, organizations, dates, places, and relationships; seek corroboration when identity matters; avoid name-only joins; and return the existing `insufficient_evidence` outcome when a necessary identity link remains unestablished. It does not hardcode people, occupations, films, or this question. Search ranking, RRF, evidence registry, resource limits, citation checks, authentication, schema, embeddings, and single-step generation were not changed.

This is guidance, not a deterministic identity resolver. It has not been tested with a new live response, and no effectiveness claim is made.

Reports now expose independent assessment axes and counts. Explicit source reviews can record supported, unsupported, or unresolved support; each review is attributed, justified, and bound to a fingerprint of the exact saved answer and evidence. A changed answer or source invalidates the review. These are explicit review judgments rather than automatic semantic scores. Correct-but-unsupported answers remain visible in the report's review section.

The offline rescoring tool verifies unchanged suite/corpus fingerprints, refuses to overwrite the source or an existing destination report, preserves prior score labels, and records the source report SHA-256. It has no provider or database execution path. Task 16D's review is labeled as a Codex source audit, not a human or automated-judge verdict; further independent review remains possible.

## Regression tests and verification

Twenty-four new deterministic cases cover conservative nationality aliases, prohibited mappings and geographic modifiers, two distinct same-name profiles, explicitly linked same-person context, unresolved abstention, relevant/irrelevant real citation IDs, correct answers with unsupported evidence, incorrect answers citing real evidence, stale review rejection, source-preserving rescoring, and report rendering. Scripted model fixtures exercise application context, source attribution, citation validation, and unresolved outcomes; they do not purport to demonstrate prompt compliance by a real model.

- Full Python: **669 passed, 4 opt-in live tests skipped** (508 unit and 161 PostgreSQL integration tests), in 190.07 seconds.
- Final focused verification after the report-rendering refinement: **68 passed**.
- JavaScript: **26 passed**.
- Compilation: passed.
- Warning: one upstream Google GenAI `_UnionGenericAlias` deprecation warning.
- New Gemini generation, model metadata, query embedding, and document embedding requests: **zero**.

All PostgreSQL tests used disposable databases. Historical report, suite, and corpus SHA-256 values still match the recorded inputs. No corpus regeneration, Cloud SQL, paid infrastructure, deployment, staging, commit, or push occurred.

## Model separation and readiness

The saved successful protocol trace belongs to **Gemini 3.5 Flash-Lite**. The earlier **Gemini 3.6 Flash** HTTP 400 detailed reason remains unavailable, and that model was not rerun. Flash-Lite success does not establish model-specific 3.6 behavior. The local configured `gemini_generation_model` remains `None`; API startup requires explicit generation configuration. The evaluation runner's fallback remains `gemini-3.6-flash`, with Flash-Lite selected only by the previous evaluation CLI override. No default was silently changed.

The scoring defect is corrected conservatively, and semantic uncertainty is now explicit. The identity-sensitive reasoning weakness and benchmark semantic annotation issue remain. **Do not treat the recorded answer as a verified grounded factual answer or proceed with public factual-answer deployment preparation on this evidence.** Further review should assess identity-sensitive examples and actual compliance with the revised guidance before changing that readiness judgment.
