# Evaluation summary (devset)

code: `d6027c5` · model: Ternary Bonsai 27B (PQ2_0) · runs/request: 1

| request | runs | gate pass | human-verified correct | median s | block rejections (mean) | most frequent rules |
|---|---|---|---|---|---|---|
| d0_led | 1 | 1/1 | 0/1 judged | 287 | 0.0 | E073x2 |
| d1_ldo33 | 1 | 1/1 | 1/1 judged | 620 | 1.0 | E073x2, E205x1 |
| d2_ldo18 | 1 | 1/1 | 0/1 judged | 1290 | 5.0 | E053x3, E073x2, E010x2, R001x2 |

*gate pass = every automatic gate (linter, coverage, reviewer, merge). It is not correctness: judge each drawing and set `human_verdict` (true/false) in its result.json, then re-run the summary.*
