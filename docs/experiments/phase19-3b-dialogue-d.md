# Phase 19.3B Dialogue Candidate D — one-shot result

D changed only C's `instruction` by adding a compact five-turn, alternating-speaker and 120–150-character budget. The frozen claims, evidence, schema, model (`deepseek-flash`), Provider and default thinking behavior remained the same. The original fixture hash was rechecked before dispatch. D made **one** formal-Adapter physical request on 2026-09-27: HTTP 200, attempt index 0, no retry. Its raw output and telemetry remain in ignored `output/llm-reasoning-ab/dialogue/candidate-d-20260927/`; the safe machine-readable comparison is [phase19-3b-dialogue-d.json](phase19-3b-dialogue-d.json). A was not rerun. B and C were not rerun. No Consistency, TTS, audio or E2E call occurred.

| Metric | A historical | C focused | D length control |
| --- | ---: | ---: | ---: |
| Input tokens | 1666 | 1590 | 1641 |
| Output tokens | 1614 | 1077 | 4996 |
| Reasoning tokens | 1281 | 707 | 4630 |
| Reasoning ratio | 79.37% | 65.65% | 92.67% |
| Reasoning reduction vs A | — | 44.81% | **−261.44%** (increase) |
| Latency, seconds | 7.495 journal interval | 4.722 request interval | 18.324 request interval |
| Estimated cost, CNY | 0.008122 | 0.004894 | 0.020496 |
| Cost reduction vs A | — | 39.74% | **−152.35%** (increase) |
| Generated dialogue characters | 133 | 173 | 122 |
| Turns | 5 | 4 | 5 |
| Claim coverage | 75% | 75% | 100% |
| Unsupported claim IDs / numeric turns | none / none | none / none | none / none |
| Repetition | 0 | 0 | 0 |
| Schema valid | yes | yes | yes |
| Deterministic output-shape gate | historical baseline | failed length and turns | **passed**: 5 alternating turns, 122 chars |
| Overall D success gate | baseline | not applicable | **failed**: reasoning and cost above A |

D's turn lengths were 24, 24, 24, 24 and 26 Unicode characters, including punctuation. The deterministic validator checked exact turn count, 120–150 total characters, alternating speakers and schema validity. Claim coverage, unsupported IDs/numbers and repetition also met the requested thresholds. The local content gate therefore remains pending semantic/listening review. Manual text review found no obvious unsupported source claim, but “仅只” is awkward and the final answer labels Munger's observation as empirical evidence without clearly resolving the guest's causality objection. No human listening review was performed.

**D failed the predeclared success criteria** because 4630 reasoning tokens exceed A's 1281 and ¥0.020496 exceeds A's ¥0.008122. There was no automatic retry or prompt adjustment. B remains the prior overlength failed experiment and receives no further work. The A latency comes from an Attempt journal interval, while C/D use request timing; latency percentages are not strictly comparable. C had 1024 cache-hit input tokens and D had 1152, so cost changes also reflect caching. No production configuration or Winner was selected. Stop here; any further Dialogue iteration requires a separate decision and authorization.
