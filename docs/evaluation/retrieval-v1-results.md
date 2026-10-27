# Retrieval v1 measured results

Synthetic corpus; independent label review pending.
Test: 16 cases. Percentages below are retrieval metrics, not Agent success.

| Configuration | Recall@1 | Recall@3 | Recall@6 | MRR@6 | P50 ms | P95 ms |
|---|---:|---:|---:|---:|---:|---:|
| bm25 | 68.75% | 93.75% | 100.00% | 0.8125 | 0.4 | 0.6 |
| dense | 56.25% | 81.25% | 87.50% | 0.6875 | 4.6 | 5.3 |
| hybrid | 62.50% | 93.75% | 100.00% | 0.7760 | 3.9 | 4.7 |
| hybrid_rerank | 75.00% | 100.00% | 100.00% | 0.8750 | 1342.9 | 1467.8 |

See the paired JSON for hashes, per-case outputs, confidence intervals and limitations.
