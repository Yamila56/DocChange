# ShopResolve

电商售后与订单异常处理 Agent：查事实、查政策、提出方案、明确批准、受控执行、回读核验。

**本地开发版，尚未完成全部验收。全部客户、订单、政策和退款为合成模拟数据，不接真实支付。**目前默认使用 deterministic demo 控制器，不将其回归成绩冒充真实 LLM 能力。最新状态见 [PROGRESS.md](PROGRESS.md)。

## 已实现

- 自写单 Agent 循环、结构化多意图、来源校验、任务工作记忆、上下文选择、完成与无进展守卫。
- FastAPI、MySQL 事务、不可变提案、审批绑定、操作幂等与结果回执。
- 数据库检查点、Worker 租约/fencing、outbox、Redis Streams、消息回收、持久 SSE cursor 续传。
- BM25、中文 BGE 向量、RRF 混合、量化 BGE 重排、父子片段、政策版本与 owner 过滤。
- 独立内部 Sandbox Manager、CSV 上传、无网络非 root Docker 分析、资源限制、日志限额与安全输出收集。
- React 工作台：任务、订单、政策依据、确认卡、执行时间线及独立 CSV 分析模块。

注意：CSV 分析尚未接为 Agent 自动委派工具；物流人工工单、商品库存/替代品业务、部分质量门禁仍待补齐。真实模型适配器已编写，但服务商配置和真实模型评测尚未完成。不宣称生产就绪、所有故障已覆盖或已部署到 GitHub。

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
