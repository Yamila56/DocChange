# 公开评测复现

2026-11-17新增仓库策略`taskgraph-v2`，实现见[任务图设计](design/23-TaskGraph与证据关系维护策略.md)及[阶段协议修复](design/24-TaskGraph阶段协议与运行稳定性.md)。历史评测默认仍为coverage-v1。同题对照需额外传`--repository-policy taskgraph-v2`并使用新输出目录；原试跑停止后，修复版以独立批次冻结，不合并试跑结果。

普通仓库入口默认 `--policy proposal-react-v1`，可显式选择 `ledger-react-v1`、`belief-react-v1`、`batched-react-v1`、`taskgraph-v2` 或 `coverage-v1`。维护策略运行后可导出 `maintenance-graph.json`，补丁经人工审查。

基准使用 `--repository-policy batched-react-v1`。冻结计划记录十次模型决策的实际硬上限、初稿前三轮调查及复核专用的一轮补查。`--batched-thinking` 用于独立模型设置对照。设计见[批量 ReAct 架构](design/25-Batched-ReAct仓库维护架构.md)。

所有命令在仓库根目录运行。缓存、数据、原仓库、调用日志和预算 SQLite 都在 ignored 路径；不提交 Key 或下载的完整仓库。不要删除账本以绕过限额。

## Proposal-First 开发回归及全量评测

按[冻结配置](design/27-Proposal-First仓库维护架构.md)先运行预选的 30 题，再决定是否进入 175 题。沿用下文的工作区、检索和评分参数，将策略改为 `--repository-policy proposal-react-v1` 并使用新输出目录。首轮传入 `--cohort-predictions evals/data/belief-regression-v1-regression.jsonl`，核对 30 条终态和 30 条评分。全量批次省略此参数，核对 175 条终态和 175 条评分。使用保守账本限制调用，费用以实际账单为准。

```powershell
.venv\Scripts\python.exe -m scripts.analyze_belief_regression --policy proposal-react-v1 `
    --batch "$batch" --judgments "$batch-judgments" --output "$batch/regression-gate.json"
```

## 历史 Full Belief 175 题评测配置

按同一冻结版本生成并评分全部 175 道公开题。题目曾参与历史评测，报告全量结果和历史可比子集时保留各自分母。候选生成用 Flash 的选择性思考，`ORIENT`、`BELIEF_UPDATE`、`VERIFY` 开启思考，`DRAFT` 和 `DRAFT_DELTA` 关闭思考。单题共用 12 次决策上限。复核可要求补查或撤销候选，实际分支以运行记录为准。

Windows CUDA 配置见下文。`$prepared` 为已核验的完整封存输入目录，`$cudaDllDir` 为 CUDA DLL 路径，`$batchCap` 为本批次人民币估算上限。使用新的 `$batch`，沿用同目录恢复时保持冻结源码、参数和账本一致。

```powershell
$batch = '.cache/evals/dogbench-belief-full175'
.venv\Scripts\python.exe -m scripts.evaluate_dogbench --prepared $prepared `
    --repository-policy belief-react-v1 --model deepseek-flash --model-limit 12 `
    --workers 8 --task-timeout-seconds 1800 --request-timeout-seconds 180 `
    --retrieval-mode hybrid --retrieval-provider CUDAExecutionProvider --cuda-dll-dir $cudaDllDir `
    --wsl-index-io --capture-error-usage --cache-aware --api-only-symlinks `
    --cap-cny $batchCap --output $batch
if ($LASTEXITCODE -ne 0) { throw 'Candidate generation exited early' }
$generation = Get-Content -Raw "$batch/summary.json" | ConvertFrom-Json
if (!$generation.complete -or $generation.finished -ne 175) { throw 'Incomplete generation' }
.venv\Scripts\python.exe -m scripts.score_dogbench --predictions "$batch/predictions.jsonl" `
    --output "$batch-judgments" --model gpt-5.6-terra --workers 4
```

## 批量 ReAct 的固定 49 题对照

| 比较 | 旧组 | 新组 | 要回答的问题 |
| --- | --- | --- | --- |
| `historical-system` | 已归档的 Flash `coverage-v1@32` | Flash `batched-react-v1@10` | 新系统在较低的决策上限下交付了什么，实际调用和时延是多少 |
| `matched-budget` | 本提交的 Flash `coverage-v1@10` | 同一批 `batched-react-v1@10` | 在同一决策上限下，两套执行策略的完成率、正确性、质量和实际调用量有何差异 |

以下 Windows 命令使用前文准备的封存工作区；`$prepared` 为本机 WSL 路径，`$cudaDllDir` 为本机 CUDA DLL 路径，`$batchCap` 为每批的费用上限。每组使用独立目录和账本。对照使用同一源码、Flash 非思考、混合检索、相同 CUDA provider、WSL 读取方式、8 任务并发、180 秒请求和 1,800 秒任务超时。不传 `--batched-thinking`。两组时延记录缓存状态和中断情况。

```powershell
$common = @('--prepared', $prepared, '--model', 'deepseek-flash', '--model-limit', '10',
    '--cohort-predictions', 'docs/evaluation/dogbench-flash-long-2026-11-04/predictions.jsonl',
    '--workers', '8', '--request-timeout-seconds', '180', '--task-timeout-seconds', '1800',
    '--retrieval-mode', 'hybrid', '--retrieval-provider', 'CUDAExecutionProvider',
    '--cuda-dll-dir', $cudaDllDir, '--wsl-index-io', '--capture-error-usage',
    '--api-only-symlinks', '--cache-aware', '--cap-cny', $batchCap)
foreach ($policy in @('coverage-v1', 'batched-react-v1')) {
    $batch = ".cache/evals/dogbench-$policy-10"
    .venv\Scripts\python.exe -m scripts.evaluate_dogbench @common --repository-policy $policy --output $batch
    if ($LASTEXITCODE -ne 0) { throw "Candidate generation failed for $policy" }
    .venv\Scripts\python.exe -m scripts.score_dogbench --predictions "$batch/predictions.jsonl" --output "$batch-judgments" --model gpt-5.6-terra --workers 4
    if ($LASTEXITCODE -ne 0) { throw "Scoring failed for $policy" }
}
.venv\Scripts\python.exe -m scripts.analyze_maintenance_ab --comparison matched-budget `
    --old-batch .cache/evals/dogbench-coverage-v1-10 --old-judgments .cache/evals/dogbench-coverage-v1-10-judgments `
    --new-batch .cache/evals/dogbench-batched-react-v1-10 --new-judgments .cache/evals/dogbench-batched-react-v1-10-judgments `
    --output .cache/evals/dogbench-matched-10-report
```

历史对照用 `--comparison historical-system`，将旧组与评分目录指向 `dogbench-flash-long-20261104` 和 `dogbench-flash-long-judgments-20261104`。两份报告使用不同输出目录。历史版本的实现、预算和索引读取方式不同，适用于整体对照。同预算对照比较整套编排策略，尚不能把变化归因于某个单独模块。两组都完整评分后再汇总 49 题，保留未解项和官方计分口径。

## 1. 数据与组件检索

Windows 项目虚拟环境：

```powershell
.venv\Scripts\python.exe -m pip install -c requirements-dev.lock -e ".[dev,benchmark]"
.venv\Scripts\python.exe -m scripts.prepare_public_benchmarks
.venv\Scripts\python.exe -m scripts.evaluate_public_ir
```

下载器固定 DoGBench Git commit、Hugging Face revisions、数据 SHA256；SciFact 使用官方 BEIR ZIP/MD5。SciFact 首次运行会下载；SCIDOCS 使用官方 HF 镜像，避免本机无法完成的旧 ZIP 传输。检索过程不调用语言模型。

结果在 `docs/evaluation/public-ir-2026-11-02/`：每题指标与整体 Recall、nDCG、MAP、Precision、MRR、Hit。完整 top1000 TREC run 在 `.cache/evals/public-ir/`。默认输出是本轮日期路径，另一次实验请指定新 `--output`，避免覆盖报告。

## 2. Linux 仓库准备

本机使用现有 WSL Ubuntu，未修改全局 Python。Linux 需 Git、Python3.12 与项目依赖；一次性用 `uv venv .venv-bench-linux --python 3.12`、`uv pip install --python .venv-bench-linux/bin/python -c requirements-dev.lock -e '.[dev]'` 建立隔离环境。若使用系统 venv，则需预先安装发行版的 python3-venv。

```powershell
.venv\Scripts\python.exe scripts/run_benchmark_linux.py scripts/prepare_dogbench.py --workers 4 --retry-failed --released-input-compatibility --api-only-symlinks
```

仅此准备命令从已登录 gh 获取 token，通过环境传给可信 GitHub attestation 控制器。候选运行不会继承 GH token。缓存位于 Linux 的 `~/.cache/docchange/`，减少 Windows 文件系统的大仓库开销。已有成功项跳过，失败项保留前次记录后重试；不改用户应用数据。

`--released-input-compatibility` 对确切发布条目的 prose-overlap 准入兼容，保留上游 finding；`--api-only-symlinks` 保留代码测试 fixture 的原始符号链接，由 API 工具层禁止沿链接访问。后一选项严禁用于带 Shell 的 CLI Agent。旧仓库名通过 GitHub repository ID 绑定，不修改 item 或 frozen prompt。

这是 **DocChange API-only 适配协议**。没有真实 Docker Engine，因此不能冒充上游原生 Docker/CLI 隔离，也不直接与其历史榜单数值作优劣比较。

## 3. 真实模型候选

Key 只配置在本地 `.env` 的 `SHOPRESOLVE_MODEL_API_KEY`，不要放入命令、聊天或仓库。

```powershell
.venv\Scripts\python.exe scripts/run_benchmark_linux.py -m scripts.evaluate_dogbench --wait-preparation --output .cache/evals/new-dogbench-batch
```

当前命令默认真实付费的`deepseek-v4-pro`、thinking/high、Repository章节混合检索：每题16轮，每响应8192 token含思考，每题900秒。总预算15元是预估防护上限，不保证覆盖175个Pro任务；最终批次按用户要求以约50项收尾：49/175项有结果，126项缺失；见最终报告，不得当作全量完成。需要先准备本地向量模型。首次冻结源哈希与配置，后续同目录必须匹配；已完成结果不重复调用。候选不读取outcome/rubric。Pro缓存价为0.30元/百万，高峰未命中输入9元/百万、输出27元/百万。

下面仅记录2026-11-02旧Flash基线的恢复流程，不是当前Pro运行建议，不应重复已完成批次。旧恢复完成的语义决定不重做，跨批次每题合计限16；结果未知的调用也占次数：

```powershell
.venv\Scripts\python.exe scripts/run_benchmark_linux.py -m scripts.continue_dogbench --parent .cache/evals/dogbench-20261102 --output .cache/evals/dogbench-recovery-20261102 --cap-cny 5 --api-only-symlinks
```

`--cap-cny` 是这个新增批次的限额，不能随意提高已有账本上限。恢复配置与原始尝试分别保存，不能用恢复后的结果伪造第一次成功率。实际已发生的批次、请求数与费用在最终报告列明。

## 4. 官方冻结规则评分

需要本机 Codex CLI 已本人登录并可访问 `gpt-5.6-terra`。本轮已实际验证该模型可用。评分调用使用现有 Codex 登录，不使用 DeepSeek Key；订阅额度与 DeepSeek API 费用分开。

```powershell
.venv\Scripts\python.exe -m scripts.score_dogbench --predictions .cache/evals/最终批次/predictions.jsonl --output .cache/judge-dogbench --workers 4
```

同一个 prediction hash 使用已有评分，避免重复付费/占用订阅。裁判失败保留 attempt 与验证错误，完整报告缺项时 `complete=false`、composite为空；不能人工补一个数字。已成功的裁判输出须通过原始引用证据验证，再进入原始数值 scorer。

完整175条、同时含两类任务时报告：需改任务平均交付质量 Q、应弃答任务 recall A，以及 `2QA/(Q+A)`。执行错误和错误决策按官方规则为0。它不是“175条生成率”或逐任务二元正确率；报告另外列决策准确率、P0 clean delivery 与完成数量。

## 5. 普通仓库使用

不需要 benchmark 数据，也不需要 Linux；Windows 长路径仓库建议在 Linux 使用。准备一个任务文本和选定的 Git 根目录：

```powershell
.venv\Scripts\python.exe -m scripts.run_repository --docs-root D:\work\project-docs --code-root D:\work\project-code --task-file D:\work\task.txt
```

默认策略使用 Flash 非思考，有真实 API 费用。仓库 CLI 使用已有账本 `.cache/docchange/repository-pro-budget.sqlite`，累计上限为 100 请求及高峰价预估 2 元。默认章节混合检索，`--retrieval-mode section_bm25|dense|hybrid` 可选。大仓库首次向量索引建议使用已配置 CUDA 的环境，CLI 提供 `--retrieval-provider CUDAExecutionProvider --cuda-dll-dir 本机DLL目录`。输出候选补丁、运行 ID 与日志，补丁需人工审批。

仓库搜索返回路径、标题、行号、匹配词、原文片段和evidence_id；`queries`可包含最多4个查询；`read_many.reads`允许每文件分别指定query/offset/limit。已展示片段可支持编辑，但不能授权未展示范围；删除要求完整读取。

## 6. 文档语义检索与四组公开复测

此部分使用本地检索模型，不调用 DeepSeek 或生成模型，也不租卡。模型为英文 BGE-base-en-v1.5 和 MiniLM-L6 重排器，仓库revision固定在`evals/document-retrieval-models.lock.json`；首次准备后应用仅从本地读取。

```powershell
.venv\Scripts\python.exe -m pip install -c requirements-dev.lock -e ".[dev,retrieval,benchmark]"
.venv\Scripts\python.exe -m scripts.prepare_document_models
.venv\Scripts\python.exe -m scripts.smoke_document_semantic
.venv\Scripts\python.exe -m scripts.run_docchange --retrieval-mode hybrid
```

默认工作台仍是BM25；新模式为`dense`、`hybrid`和`hybrid_rerank`。可在本地`.env`配置`SHOPRESOLVE_DOCUMENT_RETRIEVAL_MODE`供正式API读取，一键本地入口用上述命令行参数。任务绑定模式，精确scan仍走全量字面匹配。CPU冷启动需要生成当前快照向量，之后复用按owner/revision隔离的有界缓存；这里没有在线吞吐保证。英文模型成绩不代替中文资料评测。

完整CPU复现（BGE-base全语料编码较慢，使用新的输出和缓存路径）：

```powershell
.venv\Scripts\python.exe -m scripts.evaluate_document_hybrid --cache .cache/evals/new-ir-cpu-batch --output docs/evaluation/new-ir-cpu-batch
.venv\Scripts\python.exe -m scripts.summarize_document_hybrid --cache .cache/evals/new-ir-cpu-batch --results docs/evaluation/new-ir-cpu-batch
```

本机正式批次使用RTX4050 6GB加速，固定四组BM25/Dense/RRF/RRF+top20重排、SCIDOCS1000和SciFact300；CUDA ONNX Runtime 1.23.2安装到ignored`.cache/ir-cuda-runtime`，通过该进程的`PYTHONPATH`加载，CUDA/cuDNN DLL只读使用已有PyTorch环境。原`.venv`和全局CUDA没有修改。已验证命令：

```powershell
# 安装时将TEMP/TMP和pip缓存放到本仓有空间的D盘目录。
.venv\Scripts\python.exe -m pip install --target .cache/ir-cuda-runtime --no-deps onnxruntime-gpu==1.23.2
$env:PYTHONPATH = (Resolve-Path .cache/ir-cuda-runtime).Path
.venv\Scripts\python.exe -m scripts.evaluate_document_hybrid --provider CUDAExecutionProvider --cuda-dll-dir D:\conda_envs\torch-ts-py312\Lib\site-packages\torch\lib --cache .cache/evals/public-ir-v03-cuda-20261103
```

最后的DLL目录是本机已有带CUDA的PyTorch路径；其他机器须替换为自己的路径并满足CUDA依赖。请求的CUDA Provider不可用时直接报错，不把CPU回退伪装成GPU。若只需要CPU，请省略这段GPU配置。

## 7. Pro协议检查与仓库检索诊断

```powershell
# 默认仅查询模型和账户可用性，不打印Key或余额。
.venv\Scripts\python.exe -m scripts.verify_document_pro
# --execute执行固定日期账本内最多1次Pro协议调用，最多0.75元预估；本机该批已完成。
```

本轮新增独立的公开仓库定位诊断，使用123个patch-needed任务，比较5个预设方案；不调用生成模型。标签是原数据中的人类修改路径，仅在候选排名生成后进入评分。它不是DoGBench官方综合分，也不以源码变更文件代替文档gold路径。

```powershell
$env:PYTHONPATH = (Resolve-Path .cache/ir-cuda-runtime).Path
.venv\Scripts\python.exe -m scripts.evaluate_repository_retrieval --provider CUDAExecutionProvider --embedding-batch-size 32 --cuda-dll-dir D:\conda_envs\torch-ts-py312\Lib\site-packages\torch\lib --cache .cache/evals/repository-retrieval-b32-20261103
```

本机直接只读访问已准备的WSL匿名Git快照，模型只接收原文与task/diff派生查询。Git blob读取跳过符号链接、非文本和超限文件；原源树不修改。断点落盘，每条排名后独立打分，分别报告全部人类修改路径和当前快照既有路径；新建路径不可被旧快照检索，因此保留两种分母。缓存绑定模型、runtime与provider，并可跨快照复用相同原文章节的向量。

缓存记录冻结源哈希、模型文件哈希、完整输入文本哈希和逐题结果；中断后同配置可恢复。不要编辑候选实现后继续复用旧缓存，旧CPU诊断批次也不能充当正式新模型结果。查询向量在此离线评测中批量生成，排序耗时不包含查询编码；不把它与旧CPU调用延迟直接相比。


## 8. 最终覆盖策略与历史续跑命令

仓库入口现启用coverage-v1，所有规划、工具决策与独立复核共用16轮；最多一轮定向修补。实现和未实现边界见[设计22](design/22-覆盖驱动仓库维护最终方案.md)。源码冻结后不按低分重做输出。

本机Windows候选可直接访问已准备的WSL匿名快照，由Linux只读控制器逐题验证输入；CUDA只用于本地检索。启动原生Python须设置`$env:PYTHONUTF8='1'`，并使用第6节的CUDA overlay与DLL配置。预热结果清空，不能授予编辑权限。

本轮已按用户指定范围结束，以下仅保留历史续跑命令，不建议重新启动剩余任务。新的付费批次需另行授权，原进程退出后才可使用续跑控制器。它不充值，不清空旧请求账本，不替换已完成输出，不增加每题轮次；记录初始预算、授权和冻结计划哈希：

```powershell
$env:PYTHONUTF8='1'
$env:PYTHONPATH=(Resolve-Path .cache/ir-cuda-runtime).Path
.venv\Scripts\python.exe -m scripts.resume_authorized_pro_budget --batch .cache/evals/dogbench-coverage-pro-frozen-20261103 --prepared \\wsl.localhost\Ubuntu\home\duyumeng\.cache\docchange\dogbench-prepared --authorized-total-cny 60 --cuda-dll-dir D:\conda_envs\torch-ts-py312\Lib\site-packages\torch\lib
```

模型预算不足时，未完成项暂不生成prediction；不是把未调用任务算作模型失败。已实际执行的轮次耗尽、协议错误和内容失败全部保留。新完整综合分只能在175项全部执行并取得有效评分后发布。

最终批次已停止，既有账本/结果保留。导出命令：`python -m scripts.export_docchange_final --batch .cache/evals/dogbench-coverage-pro-frozen-20261103 --judgments .cache/evals/dogbench-coverage-pro-judgments-20261103 --output docs/evaluation/dogbench-pro-2026-11-03`。这只读取已保存结果，不发模型请求。


## 9. Flash同49题延时配置（2026-11-04）

原Pro结果保留。本轮固定同49个任务ID，使用`--model deepseek-flash --model-limit 32 --task-timeout-seconds 1800 --request-timeout-seconds 180 --cohort-predictions docs/evaluation/dogbench-pro-2026-11-03/predictions.jsonl --capture-error-usage`，独立输出目录，8任务并发。沿用混合检索和官方评分；这是配置对照，不是模型单因素消融。新参数写入冻结plan，中断后须保持一致。使用新的输出和账本，禁止覆盖旧Pro/Flash批次。详见[结果与全部补丁](evaluation/docchange-flash-comparison-2026-11-04.md)。
