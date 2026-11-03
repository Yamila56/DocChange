# Flash 延时配置与 Pro 固定49题对照

2026-11-04。根据用户要求，使用DeepSeek Flash重新运行原Pro已结束的同一49道公开任务；没有挑选高分题或替换Pro输出。**49/49项均已结束并按原规则评分；这仍不是175题全量成绩。** 新配置在该固定子集上提高了正常返回与决策正确率；仍以逐题内容评分和阻断项判断可用性。 无需修改任务的正确弃答率由33.33%降至8.33%，存在明确退步，不能说整体能力全面提升。

## 配置

| 设置 | 原Pro | 本轮Flash |
|---|---|---|
| 模型请求名 | deepseek-v4-pro | deepseek-flash |
| 思考模式 | enabled/high | disabled |
| 总决策次数，包括规划与复核 | 16 | 32 |
| 单次请求超时 | 120秒 | 180秒 |
| 单题执行窗口 | 900秒 | 1800秒 |
| 同时运行任务 | 4 | 8 |
| 检索与策略 | 章节BM25/Dense混合，coverage-v1 | 同左 |
| 输入与评分 | 原公开快照、官方rubric与数值规则 | 同左 |

Flash当前官方名称与版本映射见[官方模型文档](https://api-docs.deepseek.com/quick_start/pricing/)。这是模型、思考模式、调用次数和执行时间共同变化的系统配置比较，不是仅更换模型的因果消融，也不据此宣称Flash模型本身更强。原Pro失败包括21项轮次耗尽与13项超时，因此同时放宽时间与轮次。网页和仓库Agent实现、工具权限与检索策略均未因这次分数继续改动。

## 同题结果

| 指标 | Pro | Flash延时配置 |
|---|---:|---:|
| 已结束并评分 | 49 | 49 |
| 正常返回最终决定 | 11 | 25 |
| 执行失败 | 38 | 24 |
| 修改/不修改决定正确率 | 20.41% | 42.86% |
| 需改任务补丁召回率 | 16.22% | 54.05% |
| 需改任务平均交付质量，含失败零分 | 6.14/100 | 14.39/100 |
| 无需修改任务正确弃答率 | 33.33% | 8.33% |
| 需改任务无阻断P0交付率 | 5.41% | 10.81% |

同49题中，需要修改37题、无需修改12题。配对决定：两者都正确4题，仅Flash正确17题，仅Pro正确6题，两者都错误22题。这里的决定正确只判断修改/不修改方向，不代表补丁内容全部正确。官方报告的175题综合分仍为null，未用旧输出补齐缺题。

| 失败类型 | Pro | Flash延时配置 |
|---|---:|---:|
| JSONDecodeError | 3 | 1 |
| ValueError | 1 | 0 |
| 执行超时 | 13 | 0 |
| 模型轮次耗尽 | 21 | 12 |
| 覆盖复核未通过 | 0 | 11 |

## 补丁

本轮导出24份最终候选，原始字节与预测及评分哈希逐一核对。生成时的git apply --check说明可应用；语义完整性按以下评分及P0判断，仍需人工审核。所有低分与失败保留，未通过复核的草稿不计为交付。

| 任务及补丁 | 内容分 | 阻断P0 |
|---|---:|---|
| [Azure-azure-service-operator-pr4003](dogbench-flash-long-2026-11-04/patches/Azure-azure-service-operator-pr4003.patch) | 0.00 | C1 |
| [Azure-azure-service-operator-pr4202](dogbench-flash-long-2026-11-04/patches/Azure-azure-service-operator-pr4202.patch) | 15.00 | C1, C2, C4 |
| [MaterializeInc-materialize-pr36618](dogbench-flash-long-2026-11-04/patches/MaterializeInc-materialize-pr36618.patch) | 0.00 | C1, C6, C7, C8 |
| [MaterializeInc-materialize-pr37023](dogbench-flash-long-2026-11-04/patches/MaterializeInc-materialize-pr37023.patch) | 31.60 | C1, C2, C4 |
| [PostHog-posthog.com-pr16354](dogbench-flash-long-2026-11-04/patches/PostHog-posthog.com-pr16354.patch) | 22.20 | C2, C5 |
| [PostHog-posthog.com-pr16752](dogbench-flash-long-2026-11-04/patches/PostHog-posthog.com-pr16752.patch) | 53.80 | 无 |
| [PostHog-posthog.com-pr16822](dogbench-flash-long-2026-11-04/patches/PostHog-posthog.com-pr16822.patch) | 22.20 | C1 |
| [PostHog-posthog.com-pr16939](dogbench-flash-long-2026-11-04/patches/PostHog-posthog.com-pr16939.patch) | 46.70 | C1 |
| [PostHog-posthog.com-pr17231](dogbench-flash-long-2026-11-04/patches/PostHog-posthog.com-pr17231.patch) | 20.00 | 无 |
| [apache-flink-pr27195](dogbench-flash-long-2026-11-04/patches/apache-flink-pr27195.patch) | 7.10 | C2, C4, C6, C15 |
| [apache-flink-pr27345](dogbench-flash-long-2026-11-04/patches/apache-flink-pr27345.patch) | 0.00 | 应不修改；P0不适用 |
| [apache-iceberg-pr15667](dogbench-flash-long-2026-11-04/patches/apache-iceberg-pr15667.patch) | 0.00 | 应不修改；P0不适用 |
| [apache-iceberg-pr15814](dogbench-flash-long-2026-11-04/patches/apache-iceberg-pr15814.patch) | 0.00 | C1, C2, C4, C5, C6, C7, C8, C9 |
| [apache-iceberg-pr16241](dogbench-flash-long-2026-11-04/patches/apache-iceberg-pr16241.patch) | 0.00 | 应不修改；P0不适用 |
| [bentoml-bentoml-pr5465](dogbench-flash-long-2026-11-04/patches/bentoml-bentoml-pr5465.patch) | 23.50 | C9, C11 |
| [bentoml-bentoml-pr5518](dogbench-flash-long-2026-11-04/patches/bentoml-bentoml-pr5518.patch) | 33.30 | C1 |
| [celery-celery-pr10135](dogbench-flash-long-2026-11-04/patches/celery-celery-pr10135.patch) | 28.60 | C2, C5, C10, C11 |
| [celery-celery-pr10206](dogbench-flash-long-2026-11-04/patches/celery-celery-pr10206.patch) | 60.00 | C2, C3 |
| [celery-celery-pr9879](dogbench-flash-long-2026-11-04/patches/celery-celery-pr9879.patch) | 40.00 | 无 |
| [dagworks-inc-hamilton-pr1445](dogbench-flash-long-2026-11-04/patches/dagworks-inc-hamilton-pr1445.patch) | 30.00 | C1, C3, C5, C8 |
| [dlt-hub-dlt-pr2500](dogbench-flash-long-2026-11-04/patches/dlt-hub-dlt-pr2500.patch) | 20.00 | C4 |
| [doc-detective-common-pr65](dogbench-flash-long-2026-11-04/patches/doc-detective-common-pr65.patch) | 0.00 | 应不修改；P0不适用 |
| [doc-detective-common-pr78](dogbench-flash-long-2026-11-04/patches/doc-detective-common-pr78.patch) | 28.60 | 无 |
| [doc-detective-doc-detective-pr146](dogbench-flash-long-2026-11-04/patches/doc-detective-doc-detective-pr146.patch) | 50.00 | C1, C2 |

[全部逐题CSV](dogbench-flash-long-2026-11-04/tasks.csv) · [全部预测](dogbench-flash-long-2026-11-04/predictions.jsonl) · [评分与逐项准则](dogbench-flash-long-2026-11-04/scores.json) · [配对数据](dogbench-flash-long-2026-11-04/paired-comparison.json) · [原Pro报告](docchange-final-2026-11-03.md)

## 检索与工程指标保持原口径

本轮没有重新计算或调参检索：SciFact300 Dense Recall@10 **87.42%**；SCIDOCS1000 Dense Recall@10 **22.94%**、Hit@10 **62.60%**；仓库110项适用路径标签上Hybrid Recall@10 **69.10%**，文件BM25 **60.88%**。这些检索指标与文档维护评分分开。

所有候选仅接收原任务、授权原文与实现变更；之前的预测只用于取49个任务ID，不进入模型上下文。原始快照HEAD与树保持不变，输入重新校验记录一并导出。该49题是先前并发运行已结束的开发子集，非随机样本或独立留出集；评测为API工具适配协议，不是原生Docker/CLI排行榜成绩。没有新增训练、GraphRAG或多Agent框架。API费用账本中的峰值保守估算不是平台实际扣费；不上传Key、余额或数据库。

复现参数：`--model deepseek-flash --model-limit 32 --task-timeout-seconds 1800 --request-timeout-seconds 180 --cohort-predictions docs/evaluation/dogbench-pro-2026-11-03/predictions.jsonl --capture-error-usage`，其余CUDA/原始输入准备与评分命令见[运行手册](../PUBLIC_BENCHMARK_RUNBOOK.md)。
