# Phase 19.3B Dialogue A/B/C controlled comparison — segment 0001

A uses the frozen historical E2E result. B and C each used the formal `CompatibleLLMProvider._chat` exactly once on 2026-09-27, with the frozen fixture and independent receipts in ignored `output/llm-reasoning-ab/dialogue/controlled-20260927/`. Each physical telemetry row reports HTTP 200, attempt index 0, no retry. The prior B/C HTTP 400 receipts remain untouched. No Baseline A, TTS, audio, E2E, or Consistency call was made this round.

| Metric | A historical | B thinking off | C focused prompt |
| --- | ---: | ---: | ---: |
| Input tokens | 1666 | 1641 | 1590 |
| Output tokens | 1614 | 646 | 1077 |
| Reasoning tokens | 1281 | unknown (not reported) | 707 |
| Reasoning ratio | 79.37% | unknown | 65.65% |
| Reasoning reduction vs A | — | unknown | 44.81% |
| Latency, seconds | 7.495 journal interval | 3.051 request interval | 4.722 request interval |
| Apparent latency reduction | — | 59.29% | 37.00% |
| Estimated CNY cost | 0.008122 | 0.004225 | 0.004894 |
| Estimated cost reduction | — | 47.98% | 39.74% |
| Generated characters | 133 | 365 | 173 |
| Turns | 5 | 6 | 4 |
| Schema valid | yes | yes | yes |
| Claim coverage | 75% | 100% | 75% |
| Unsupported claim IDs / numeric turns | none / none | none / none | none / none |
| Repetition | 0 | 0 | 0 |
| Deterministic quality gate | pending semantic/listening review | **failed: length** | **failed: length** |

B turned thinking off and used fewer output tokens and about 48% less estimated cost. The service omitted reasoning-token usage, so its reasoning-token reduction and ratio remain **unknown**, not zero. B was 365 characters against a 120-character target (3.04×). On manual text review, its six turns form a coherent exchange, distinguish the original claim from comparative proof, and contain no obvious unsupported source attribution. It adds discussion about practice and reflection as interpretation rather than an author claim. Its length is a clear quality regression for this fixed segment.

C cut input tokens by 4.56% and reported 707 reasoning tokens, 44.81% fewer than A. This is a measured reasoning reduction, not solely a shorter prompt. Estimated cost fell 39.74%. Its four turns preserve the cited claims without obvious unsupported facts, but 173 characters are 1.44× target and the segment ends with an unanswered challenge. Its length gate fails; conversational closure is weaker than A. Neither candidate passes the full deterministic gate, and no human listening review occurred. No winner or production setting is selected.

The A latency is a historical Attempt journal interval, whereas B/C timings measure the local request interval; the apparent latency reductions are directional, not strictly matched wire latencies. Costs are model-price estimates using the saved pricing snapshot, not provider invoices. C reported 1024 cache-hit input tokens while A/B reported zero, so its cost reduction also reflects caching and is not solely caused by prompt or reasoning changes. The frozen fixture and machine-readable numbers are in [phase19-3b-dialogue-controlled.json](phase19-3b-dialogue-controlled.json). Next decision requires reviewing the failed length gate before any separately authorized experiments; do not automatically rerun B/C or enter Consistency.
