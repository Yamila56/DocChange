# Retrieval v1 measured results

Synthetic corpus; independent label review pending.
Test: 28 cases. Percentages below are retrieval metrics, not Agent success.

| Configuration | Recall@1 | Recall@3 | Recall@6 | MRR@6 | P50 ms | P95 ms |
|---|---:|---:|---:|---:|---:|---:|
| bm25 | 58.93% | 85.71% | 91.07% | 0.7542 | 0.5 | 0.9 |
| dense | 75.00% | 94.64% | 98.21% | 0.8940 | 4.8 | 5.9 |
| hybrid | 73.21% | 87.50% | 91.07% | 0.8482 | 5.0 | 6.6 |
| hybrid_rerank | 73.21% | 98.21% | 98.21% | 0.9107 | 1627.6 | 1899.3 |

See the paired JSON for hashes, per-case outputs, confidence intervals and limitations.
