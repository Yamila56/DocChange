# DocChange · V0.2

版本化文档工作台：检索授权资料、生成有来源的修改、查看差异、人工审批，发布可回看的新版本。Python/FastAPI + React + 自写 Agent Runtime/Harness；保留旧 ShopResolve 电商域。

定位：**面向技术文档变更审核的后端 Agent 项目**。针对接口、配置与示例更新，定位依据并生成待审核修改，保留历史版本与发布回执。默认演示控制器；真实模型路径由模型决定工具调用。

V0.2 增加任务修改范围与后置条件、MDX/RST 新建删除、按需读取真实 Git 仓库和原生候选补丁导出。公开检索已覆盖 SciFact 全300题与 SCIDOCS 全1000题；文档维护采用 DoGBench 公开175题，原始失败与评分口径单独保留。

当前报告：[检索改进与完整配对评测](docs/evaluation/docchange-retrieval-improvement-2026-11-03.md) · [仓库维护基线](docs/evaluation/docchange-public-2026-11-02.md) · [公开数据复现命令](docs/PUBLIC_BENCHMARK_RUNBOOK.md) · [简历表述](docs/RESUME-DOCCHANGE.md)。

## 核心能力

- **变更可审查：** 任务绑定文档快照和修改范围，生成精确补丁、证据与影响清单，经 hash 绑定审批后发布新版本。
- **执行可恢复：** 自研单 Agent Harness 保存模型决策、工具回执和 checkpoint；结合幂等、Worker Lease/Fencing 约束重复执行与过期 Worker。
- **长任务异步化：** API 提交返回 HTTP 202；Outbox → Redis Streams → Worker 执行，模型与数据库采用异步 I/O，Worker 独立心跳；本地入口提供持久数据库轮询模式。
- **维护范围明确：** 工作台维护小型版本化文档集；Git 适配器按需读取大仓库并导出候选 patch，供人工审查。

## 公开评测摘要

| 公开范围 | BGE Dense 检索结果 | 相对原 BM25 的改进 |
|---|---|---|
| SciFact 完整 test 300 查询 | Recall@5 **81.54%**、Recall@10 **87.42%** | Recall@10 **+9.86个百分点** |
| SCIDOCS 完整 test 1,000 查询 | Hit@10 **62.60%**、Recall@10 **22.94%** | Recall@10 **相对提升48.70%** |

同题、同K、完整语料的四配置对照：BM25、Dense、RRF、RRF+Rerank。两套数据均由Dense取得最高Recall@10和nDCG@10，因此推荐将其用于英文语义资料；精确标识符覆盖仍使用scan。完整逐题结果、退步方案和bootstrap区间见[报告](docs/evaluation/docchange-retrieval-improvement-2026-11-03.md)。

DoGBench公开dev175的仓库维护基线、73份候选补丁及语义评分独立[归档](docs/evaluation/docchange-public-2026-11-02.md)。各任务分别计分；文档变更仍通过人工审核发布。

## 快速启动

需要 Python 3.12、Node.js 与 pnpm。获取私有仓库需要已授权的 GitHub 账号：

```powershell
git clone https://github.com/Yamila56/DocChange.git
cd DocChange
```

在仓库根目录：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -c requirements-dev.lock -e ".[dev]"
pnpm --dir frontend install --frozen-lockfile
pnpm --dir frontend build
.\.venv\Scripts\python.exe -m scripts.run_docchange
```

打开 http://127.0.0.1:8011，账号 `alice`，本地演示码 `docs-local-2026`。无需模型Key、MySQL或Redis即可运行本地演示；数据持久保存在ignored `.cache/docchange/`。本地演示码用于本机，不适合公网认证。

创建示例工作区 → 提交默认迁移任务 → 查看两篇文档diff → 批准并发布 → 切换R1/R2查看正文。

真实模型需在本地 `.env` 保存 `SHOPRESOLVE_MODEL_API_KEY`，再显式执行 `python -m scripts.run_docchange --remote`（使用项目虚拟环境）。交互模式有持久20请求/预估0.5元预算；不会自动充值或重置。详细配置见[使用说明](docs/DOCCHANGE.md)。

## 已实现与证据

- 文档导入、owner隔离、只读来源/历史、不可变工作区快照、BM25与全量精确扫描。
- 可选英文BGE语义召回、RRF与top20交叉编码重排；按owner/revision/内容隔离的有界缓存，模型revision与权重哈希固定。
- 文档Agent工具循环、读取/批读取、补问、精确补丁、引用与链接检查。
- ChangeSet差异审核、hash绑定批准、拒绝、过期版本拦截、幂等发布与旧版回看。
- 复用checkpoint、租约/fencing、工具结果日志、事件与恢复。Redis Worker支持文档域；一键演示入口用SQLite持久轮询。
- 导出修改记录与可选固定沙箱检查。

### 异步执行与一致性

提交任务只负责持久化 Run/Outbox，不在 HTTP 请求内等待模型完成。Dispatcher 将通知送入 Redis Streams，Worker 领取租约后执行工具循环，独立心跳维持租约；通知允许重复，执行资格和状态以 SQL 为准。单个 Worker 进程逐项处理任务，可通过多个进程消费；没有宣称单进程高并发吞吐或压测成绩。

工作台 BM25/精确扫描在读取快照、关闭 SQL 会话后经 `asyncio.to_thread` 执行；仓库工具的文件/Git 操作同样离开事件循环。单任务中有依赖的读取、提案和发布按顺序执行。文档正文与索引仍随同一 Revision 原子发布，未拆成异步索引服务。前端当前轮询快照和事件；通用 SSE 接口可续传。

验收：9个确定性工程fixture通过；真实模型首轮4题2题通过，发现协议问题后两个失败场景复测通过。不是独立benchmark的100%准确率。完整结果、失败和费用见[交付报告](docs/evaluation/docchange-delivery-2026-10-29.md)。

本轮修改后定向回归：**35 passed / 2 skipped**，包含文档发布、仓库补丁、模型协议、预算与队列测试；跳过的依赖环境用例不算实机通过。[本轮 JUnit](docs/evaluation/docchange-v02-final-2026-11-03.xml)。

首版2026-10-29本地全套：157 passed / 16 skipped（依赖服务用例等），不是本轮全套成绩。首版源码commit `4d6c36d` 的远程CI中 unit、frontend、mysql 三个job均通过，见 [历史 GitHub Actions](https://github.com/Yamila56/DocChange/actions/runs/40873084174)；后续提交的验证以各自Actions为准。

V0.2提交 `e517753` 的[远程CI](https://github.com/Yamila56/DocChange/actions/runs/41576363937)已全部通过：unit **165 passed / 16 skipped**，MySQL配置任务 **174 passed / 7 skipped**，前端构建成功。两套测试不相加，跳过项不算服务实机通过。

语义检索提交`6a82b00`的[最新CI](https://github.com/Yamila56/DocChange/actions/runs/41579224498)全部通过：unit **168 passed / 16 skipped**，MySQL配置任务 **177 passed / 7 skipped**，前端构建成功。真实本地ONNX模型已通过HTTP检索及后台任务功能冒烟。

正文与基础索引随Revision同步发布；可选语义索引由快照派生并缓存。保持单Agent。文档用例已纳入本轮MySQL配置CI；Redis文档端到端联调、真实Docker文档检查与生产认证尚待补充。

英文语义模式：安装`.[dev,retrieval]`依赖，运行`python -m scripts.prepare_document_models`准备固定权重，再运行`python -m scripts.run_docchange --retrieval-mode dense`。默认启动仍无需权重；详见[配置与复现](docs/PUBLIC_BENCHMARK_RUNBOOK.md#6-文档语义检索与四组公开复测)。

## 开发验证

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts/evaluate_documents.py
```

工程fixture成绩不代表LLM能力；新增付费评测必须显式执行并受预算限制。`.env`、模型、数据库与本地日志均不提交。仓库先保持Private。

## 原 ShopResolve 历史说明

2026-10-29：项目主线已迁移为 **DocChange 版本化文档工作台**，在现有后端与持久 Harness 上新增文档、检索、补丁、审批与版本发布。启动与真实模型验收见 [DocChange 使用说明](docs/DOCCHANGE.md)。一条命令：`.venv\Scripts\python.exe -m scripts.run_docchange`，访问 `http://127.0.0.1:8011`。下文保留原电商项目历史，不代表新文档域的全部功能或测试成绩。

2026-10-28联调更新：已用官方DeepSeek Flash完成受控真实模型开发试跑，并实测浏览器审批/拒绝、CSV隔离计算及刷新恢复。开发中发现的协议与持久化问题、失败及修复均见[联调报告](docs/evaluation/integration-and-live-eval-2026-10-28.md)。这不是正式冻结成绩；默认仍为无密钥demo，生产功能缺口继续保留。

电商售后与订单异常处理 Agent：查事实、查政策、提出方案、明确批准、受控执行、回读核验。

**本地开发版，尚未完成全部验收。全部客户、订单、政策和退款为合成模拟数据，不接真实支付。**目前默认使用 deterministic demo 控制器，不将其回归成绩冒充真实 LLM 能力。最新状态见 [PROGRESS.md](PROGRESS.md)。

## 已实现

- 自写单 Agent 循环、结构化多意图、来源校验、任务工作记忆、上下文选择、完成与无进展守卫。
- 授权候选消歧、明确选择、未实现能力拒绝、只读资格咨询；动作前政策核验与逐目标条款覆盖检查。
- FastAPI、MySQL 事务、不可变提案、审批绑定、操作幂等与结果回执。
- 数据库检查点、Worker 租约/fencing、outbox、Redis Streams、消息回收、持久 SSE cursor 续传。
- BM25、中文 BGE 向量、RRF 混合、量化 BGE 重排、父子片段、政策版本与 owner 过滤。
- 独立内部 Sandbox Manager、CSV 上传、无网络非 root Docker 分析、资源限制、日志限额与安全输出收集。
- React 工作台：任务、订单、政策依据、确认卡、执行时间线及独立 CSV 分析模块。

注意：CSV 分析尚未接为 Agent 自动委派工具；物流人工工单、商品库存/替代品业务、限流背压与部分质量门禁仍待补齐。官方 DeepSeek Flash 已做真实开发联调，正式冻结、多trial与模型对比仍待完成。不宣称生产就绪、所有故障已覆盖或已部署到 GitHub。

逐项状态及 R01–R18/L01–L18 缺口见 [实现审查](docs/implementation-audit-2026-10-27.md)。Memory 有工作状态和恢复，不等于已完成全部摘要版本/生命周期设计；Compose 当前只编排基础设施，不等于整套应用上线。

本轮补强及小规模真实模型联调的范围见 [联调准入](docs/evaluation/live-eval-readiness.md)。
词法消歧和条款覆盖检查不是通用语义理解，默认 demo 控制器仍不是 LLM。

## 架构

React → FastAPI → MySQL/outbox → Redis Streams → Worker → 自写 Runtime/Harness → 受权限控制的业务工具/检索。

Docker 管理权限只在独立沙箱进程使用，普通 Agent 不直接运行宿主 shell 或 SQL。已批准的订单操作由持有有效 run lease 的 Worker 执行；没有公开的任意 execute HTTP 入口。

## 开发启动（PowerShell / Python 3.12）

在本项目目录执行。已有 .venv 可跳过创建；当前本机 .env 已生成，不要覆盖或提交。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -c requirements-dev.lock -e ".[dev]"
docker compose --profile queue up -d mysql redis
.\.venv\Scripts\python.exe -m alembic upgrade head
# 仅首次空库；已有演示数据会拒绝重复 seed，不清空数据
.\.venv\Scripts\python.exe -m shopresolve.cli seed
```

新克隆环境先复制 .env.example 为 .env，设置本地随机数据库密码、演示码和内部沙箱 token。专用数据库为 shopresolve；不要改成已有业务数据库。

前端构建：

```powershell
cd frontend
pnpm install --frozen-lockfile
pnpm build
cd ..
```

分别在三个终端启动：

```powershell
.\.venv\Scripts\python.exe -m shopresolve.cli dispatcher
.\.venv\Scripts\python.exe -m shopresolve.cli worker
.\.venv\Scripts\python.exe -m uvicorn shopresolve.api:create_app --factory --host 127.0.0.1 --port 8000
```

打开 http://127.0.0.1:8000 。账号 alice 或 bob，演示码在本地 .env。默认 demo 控制器对明确订单编号的任务可复现；不是通用自然语言模型。示例：

- 查询 order-pending
- 帮我取消 order-pending
- 申请退货 order-delivered item-delivered 数量1 原因：单侧无声音
- 取消订单的条件是什么？

实际取消会改变演示订单，之后不能再把它当待发货订单。不会自动重置演示数据。

## 可选神经检索

```powershell
.\.venv\Scripts\python.exe -m pip install -c requirements-dev.lock -e ".[dev,retrieval]"
.\.venv\Scripts\python.exe scripts/prepare_models.py
```

之后在 .env 设置 SHOPRESOLVE_RETRIEVAL_MODE=hybrid、SHOPRESOLVE_ENABLE_RERANKER=true，再重启 Worker。重排提高部分命中但显著增加 CPU 时延，默认保持 BM25。下载缓存不提交 Git。真实远程模型须明确设置 provider/model/base URL/key 及 ALLOW_PAID_MODEL，当前没有付费调用。

### DeepSeek 首轮试跑

先运行免费预览：

```powershell
.\.venv\Scripts\python.exe scripts/pilot_deepseek.py
```

密钥只放本地 `.env`，不发聊天、不提交 Git。`scripts/check_deepseek.py` 可只读查询官方余额和模型。
付费执行必须显式 `--execute` 并配置 opt-in；建议先最多12次请求/2元预估预算。
费用预留在本地持久保存，未知响应不退预留；这不是官方账户扣费硬上限。
三场景 smoke 不是最终准确率，报告默认只存 ignored 本地目录。
配置、费用算式、正式评测与公开数据集边界见 [评测计划](docs/evaluation/deepseek-pilot-plan.md)。

## 可选隔离计算

```powershell
docker build -f Dockerfile.sandbox -t shopresolve-sandbox:v1 .
.\.venv\Scripts\python.exe -m uvicorn shopresolve.sandbox.manager:create_manager --factory --host 127.0.0.1 --port 18081
```

主 API 和 manager 使用相同的本地内部 token。工作台 CSV 分析输入列 price_cents、quantity，可选 name；只允许合成数据。容器无网络、无密钥、无 Docker socket，输出从临时文件系统在容器结束前安全收集。Docker 共享内核，不是对恶意公网多租户代码的完整隔离保证。

## 验证与可复现评测

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe scripts/test_mysql.py
.\.venv\Scripts\python.exe scripts/smoke_agent_http.py
.\.venv\Scripts\python.exe scripts/smoke_sandbox_http.py
```

MySQL 测试只允许重建专用 shopresolve_test。新环境需先在本项目 Compose MySQL 执行 scripts/create_test_database.sql；本机已建好。SQLite 功能测试不等于 MySQL 并发验证；依赖缺失的集成测试会明确跳过。

- [实测检索消融](docs/evaluation/retrieval-v1-results.md)：测试 16 条，BM25 Recall@3 93.75%，混合重排 100%；重排 P50 约 1343ms。小型合成集，标签待独立审核，不是 Agent 准确率。
- [逐题检索原始结果](docs/evaluation/retrieval-v1-results.json)：包含配置、模型文件哈希、数据哈希和失败项。
- [20 条工程闭环回归](docs/evaluation/workflow-regression-v1-results.json)：20/20 通过，0 条额外账本案例；使用 demo 控制器，**不是 LLM 成功率**。
- [量化表述边界](docs/evaluation/quantification-notes.md)：避免将小样本和测试通过率包装成线上收益。
- M1 历史报告及最新本地/MySQL JUnit 均在 docs/evaluation；以 PROGRESS 指定的最新报告为准，历史失败不隐藏。

评测脚本 scripts/evaluate_retrieval.py、scripts/evaluate_workflows.py 保留已有结果，不静默覆盖。重复实验请指定新输出路径（workflow 脚本当前固定回归版本，需要新版本时显式修改）。

## 文档与安全

docs/design 的 10/11/13/14 为架构与实施规范；docs/adr 为取舍，PROGRESS 为续接入口，SECURITY 为边界。参考 Pi、mini-SWE-agent、SWE-agent 和公开 Harness 经验；没有复制私有 Claude Code 源码，也没有官方 τ-bench 分数。

.env、.venv、模型缓存、上传和沙箱 workspace 不提交 Git。只管理本项目，父目录中的证件、成绩单和申请文件不属于仓库。GitHub 先私有，完成审核后才考虑公开；目前尚未推送。
