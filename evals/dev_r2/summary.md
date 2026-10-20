# Evaluation summary (devset)

code: `b3db7b1` · model: Ternary Bonsai 27B (PQ2_0) · runs/request: 1

| request | runs | gate pass | human-verified correct | median s | block rejections (mean) | most frequent rules |
|---|---|---|---|---|---|---|
| d0_led | 1 | 1/1 | 1/1 judged | 218 | 0.0 | E073x2 |

*gate pass = every automatic gate (linter, coverage, reviewer, merge). It is not correctness: judge each drawing and set `human_verdict` (true/false) in its result.json, then re-run the summary.*
