# 公开评测复现

所有命令在仓库根目录运行。缓存、数据、原仓库、调用日志和预算 SQLite 都在 ignored 路径；不提交 Key 或下载的完整仓库。不要删除账本以绕过限额。

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

该命令会真实付费：Flash 非思考、温度0、每题16轮、每响应4096 token、每题900秒，总预算15元保守估算/最多2800请求。首次读取源文件哈希与配置，后续同目录必须匹配；已完成结果不重复调用。它只加载 task 与匿名快照，评分的 outcome/rubric 由另一进程持有。

本轮最初网络中断后的恢复采用独立批次，完成的语义决定不重做，未完成项从前批 SQL 检查点导入已读内容与证据。跨批次每题模型调用合计仍限16；最后一次传输结果未知的调用也占次数，不免费重发：

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

有真实 API 费用，使用独立持久交互账本（累计100请求/2元保守估算）；输出候选 patch、run_id 与日志。候选不自动应用、提交或推送。小型网页工作台仍用 `python -m scripts.run_docchange`；两种入口的存储/审核范围见设计18。

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

缓存记录冻结源哈希、模型文件哈希、完整输入文本哈希和逐题结果；中断后同配置可恢复。不要编辑候选实现后继续复用旧缓存，旧CPU诊断批次也不能充当正式新模型结果。查询向量在此离线评测中批量生成，排序耗时不包含查询编码；不把它与旧CPU调用延迟直接相比。
