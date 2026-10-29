# Retrieval v1 measured results

Synthetic corpus; independent label review pending.
Test: 16 cases. Percentages below are retrieval metrics, not Agent success.

| Configuration | Recall@1 | Recall@3 | Recall@6 | MRR@6 | P50 ms | P95 ms |
|---|---:|---:|---:|---:|---:|---:|
| bm25 | 68.75% | 93.75% | 100.00% | 0.8125 | 0.4 | 0.5 |
| dense | 56.25% | 81.25% | 87.50% | 0.6875 | 5.1 | 6.0 |
| hybrid | 62.50% | 93.75% | 100.00% | 0.7760 | 5.0 | 5.6 |
| hybrid_rerank | 75.00% | 100.00% | 100.00% | 0.8750 | 1653.6 | 5624.3 |

See the paired JSON for hashes, per-case outputs, confidence intervals and limitations.
