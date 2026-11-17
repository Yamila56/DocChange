# DocChange 批量 ReAct 固定49题评测

2026-11-18。新版与同预算旧版各完成49题生成和官方评分，共98次任务运行。代码固定于 `03c1115`。任务来自此前使用的公开开发子集，37题需要修改、12题无需修改。官方175题总体综合分继续为 null。本轮在 API 工具适配协议下评测，生成模型不接收标准答案、rubric 或历史输出。

新版相对历史旧版的方向正确率增加 8.16 个百分点，模型决策总量减少 72.74%。历史对照同时涉及编排、协议及运行时变化。相对同预算旧版的方向正确率增加 28.57 个百分点。这些结果描述固定开发子集上的整体系统表现。

## 结果

| 指标 | 历史 coverage-v1，上限32 | 当前 coverage-v1，上限10 | batched-react-v1，上限10 |
| --- | ---: | ---: | ---: |
| 最终交付率 | 51.02% | 22.45% | 59.18% |
| 修改/不修改方向正确率 | 42.86% | 22.45% | 51.02% |
| 需修改任务补丁召回率 | 54.05% | 24.32% | 59.46% |
| 无需修改任务正确弃答率 | 8.33% | 16.67% | 25.00% |
| 需修改任务平均交付质量，含失败零分 | 14.39/100 | 5.91/100 | 20.89/100 |
| 需修改任务 P0-clean 交付率 | 10.81% | 8.11% | 13.51% |
| 模型决策总数 | 895 | 462 | 244 |
| 每题平均模型决策 | 18.27 | 9.43 | 4.98 |
| 每题模型决策中位数 | 16 | 10 | 5 |
| 每题平均工具调用 | 9.04 | 5.92 | 9.71 |
| 每100次模型决策中的正确方向数 | 2.35 | 2.38 | 10.25 |
| 平均任务耗时 | 128.18秒 | 107.90秒 | 149.70秒 |

补丁召回率只判断修改方向。P0-clean要求方向正确且官方rubric无阻断项。最终交付率只统计最终补丁与无需修改决定。新版运行时另有6题正常结束为未解，在官方评分适配中计为未解零分；14题为系统、协议或编辑校验失败。新版三个无需修改决定均正确，但只覆盖12个无需修改任务中的3题。

新版26份最终补丁均通过只读 `git apply --check`，源工作树干净，修改路径均为文档。26份补丁中，22份修改方向正确、4份属于本应无需修改的任务。需修改任务的平均交付质量为20.89/100；只看22份方向正确且已交付的补丁，条件平均内容分为35.13/100。该条件均值不包含未交付任务。

## 对照与不确定性

两份新批次使用同一源码、49个任务ID、Flash非思考模式、混合检索、CUDA provider、WSL索引读取、8并发、180秒请求超时及1800秒任务窗口，官方评分配置相同。冻结文件和调用量、token及逐题耗时随数据导出。新旧策略的运行机制与约束共同构成比较对象。

历史配对中，两者都正确14题，仅历史版正确7题，仅新版正确11题，两者都错误17题。方向正确率差的配对bootstrap 95%区间为[-8.16, 24.49]个百分点，包含零。同预算配对区间为[10.20, 46.94]个百分点。样本为已多轮开发使用的49题，模型服务端版本与API延迟也未独立固定。

新版平均任务耗时相对历史增加16.79%。模型调用下降尚未带来历史对照中的端到端提速。新批次先运行新版，后运行旧版，缓存及运行顺序会影响时延。各组原始耗时随CSV保留。

## 剩余失败

新版14项失败包括9项模型输出Schema校验失败、3项精确编辑匹配校验失败、2项模型请求读取异常。Schema失败主要涉及查询列表长度、目标描述长度和目标ID格式。6项未解包括2项模型主动未解、2项修补越界、1项缺少目标路径观察和1项目标缺少编辑。精确次数匹配错误提示需要重读文件，该错误本轮仍以系统错误结束，应列为后续可恢复协议问题。

本轮记录了0次复核补搜和0次REVISE模型调用，因此当前结果尚未检验新增复核补搜及草稿增量保留机制的真实收益。该路径现有工程测试通过，仍需要后续有代表性的实际任务验证。

执行层减少了模型决策，但补丁内容完整性仍受限，需修改任务中仅5/37达到P0-clean交付。继续改进应优先依据逐题rubric检查遗漏和错误描述，现有结果不足以支持进一步归因于检索器。

## 全部最终补丁

| 任务及补丁 | 官方内容分 | 阻断项或方向问题 |
| --- | ---: | --- |
| [Azure-azure-service-operator-pr4196](dogbench-batched-review-2026-11-18/patches/Azure-azure-service-operator-pr4196.patch) | 0.00 | 应不修改，修改方向错误 |
| [Azure-azure-service-operator-pr5490](dogbench-batched-review-2026-11-18/patches/Azure-azure-service-operator-pr5490.patch) | 33.30 | C1, C6 |
| [MaterializeInc-materialize-pr36328](dogbench-batched-review-2026-11-18/patches/MaterializeInc-materialize-pr36328.patch) | 55.60 | C8, C9 |
| [MaterializeInc-materialize-pr36618](dogbench-batched-review-2026-11-18/patches/MaterializeInc-materialize-pr36618.patch) | 25.00 | C1, C6 |
| [MaterializeInc-materialize-pr37023](dogbench-batched-review-2026-11-18/patches/MaterializeInc-materialize-pr37023.patch) | 15.80 | C1, C2, C3, C4 |
| [PostHog-posthog-pr63871](dogbench-batched-review-2026-11-18/patches/PostHog-posthog-pr63871.patch) | 55.60 | 无 |
| [PostHog-posthog-pr63886](dogbench-batched-review-2026-11-18/patches/PostHog-posthog-pr63886.patch) | 0.00 | 应不修改，修改方向错误 |
| [PostHog-posthog.com-pr16354](dogbench-batched-review-2026-11-18/patches/PostHog-posthog.com-pr16354.patch) | 22.20 | C2, C5 |
| [PostHog-posthog.com-pr16714](dogbench-batched-review-2026-11-18/patches/PostHog-posthog.com-pr16714.patch) | 19.00 | C1, C6, C7 |
| [PostHog-posthog.com-pr16752](dogbench-batched-review-2026-11-18/patches/PostHog-posthog.com-pr16752.patch) | 61.50 | 无 |
| [PostHog-posthog.com-pr16939](dogbench-batched-review-2026-11-18/patches/PostHog-posthog.com-pr16939.patch) | 50.00 | C1 |
| [PostHog-posthog.com-pr17172](dogbench-batched-review-2026-11-18/patches/PostHog-posthog.com-pr17172.patch) | 50.00 | C3, C7 |
| [PostHog-posthog.com-pr17231](dogbench-batched-review-2026-11-18/patches/PostHog-posthog.com-pr17231.patch) | 72.70 | 无 |
| [apache-flink-pr27195](dogbench-batched-review-2026-11-18/patches/apache-flink-pr27195.patch) | 14.30 | C2, C4, C6, C10 |
| [apache-flink-pr28212](dogbench-batched-review-2026-11-18/patches/apache-flink-pr28212.patch) | 44.40 | 无 |
| [apache-iceberg-pr15634](dogbench-batched-review-2026-11-18/patches/apache-iceberg-pr15634.patch) | 0.00 | 应不修改，修改方向错误 |
| [apache-iceberg-pr15814](dogbench-batched-review-2026-11-18/patches/apache-iceberg-pr15814.patch) | 16.70 | C1, C2, C4, C5, C6, C7, C8 |
| [astral-sh-ruff-pr21369](dogbench-batched-review-2026-11-18/patches/astral-sh-ruff-pr21369.patch) | 0.00 | C1, C5, C9 |
| [bentoml-bentoml-pr5518](dogbench-batched-review-2026-11-18/patches/bentoml-bentoml-pr5518.patch) | 25.00 | C1 |
| [celery-celery-pr10238](dogbench-batched-review-2026-11-18/patches/celery-celery-pr10238.patch) | 0.00 | C1, C4, C5, C7 |
| [celery-celery-pr9879](dogbench-batched-review-2026-11-18/patches/celery-celery-pr9879.patch) | 60.00 | 无 |
| [dagworks-inc-hamilton-pr1445](dogbench-batched-review-2026-11-18/patches/dagworks-inc-hamilton-pr1445.patch) | 60.00 | C3, C5 |
| [dlt-hub-dlt-pr2500](dogbench-batched-review-2026-11-18/patches/dlt-hub-dlt-pr2500.patch) | 35.00 | C4 |
| [doc-detective-common-pr65](dogbench-batched-review-2026-11-18/patches/doc-detective-common-pr65.patch) | 0.00 | 应不修改，修改方向错误 |
| [doc-detective-common-pr78](dogbench-batched-review-2026-11-18/patches/doc-detective-common-pr78.patch) | 6.70 | C1 |
| [doc-detective-doc-detective-pr146](dogbench-batched-review-2026-11-18/patches/doc-detective-doc-detective-pr146.patch) | 50.00 | C1, C2 |

## 数据与复现

- [新版全部预测](dogbench-batched-review-2026-11-18/predictions.jsonl)、[全部评分](dogbench-batched-review-2026-11-18/scores.json)、[逐题CSV](dogbench-batched-review-2026-11-18/tasks.csv)、[补丁可应用性](dogbench-batched-review-2026-11-18/patch-audit.json)。
- [同预算旧版数据](dogbench-coverage10-2026-11-18/summary.json)、[逐题CSV](dogbench-coverage10-2026-11-18/tasks.csv)、[全部评分](dogbench-coverage10-2026-11-18/scores.json)。
- [历史配对统计](batched-historical-ab-2026-11-18/paired-analysis.json)、[逐题差异](batched-historical-ab-2026-11-18/paired-tasks.csv)。
- [同预算配对统计](batched-matched-ab-2026-11-18/paired-analysis.json)、[逐题差异](batched-matched-ab-2026-11-18/paired-tasks.csv)。
- [失败明细](dogbench-batched-review-2026-11-18/runtime-failure-analysis.json)、[准备与正式批次说明](dogbench-batched-review-2026-11-18/run-protocol.json)、[运行手册](../PUBLIC_BENCHMARK_RUNBOOK.md)。

启动前两次准备尝试因残留Git对象和Windows Git所有权校验失败退出，两次均无模型API回执，未作为正式候选或质量分混入。本轮正式结果使用新目录，保留所有题目、未解和失败。检索Recall@K沿用既有公开报告，本轮仅运行仓库维护任务与官方评分。
