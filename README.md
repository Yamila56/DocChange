# ShopResolve

电商售后与订单异常处理 Agent。当前为 **M1 开发版本，不是完整 Agent，也不是可上线系统**。

目标：查订单 → 查适用政策 → 提出方案 → 用户确认 → 受控执行 → 回读验证。

## 已实现与未实现

已编写：异步 FastAPI API、MySQL 兼容领域模型、Alembic 初始迁移、合成订单/政策、不可变提案、审批、请求幂等、事务型模拟取消/退货申请、操作结果回读、业务与 HTTP 测试。

M2 已开始：AgentDecision/IntentFrame 类型契约、来源校验、实体消歧、CompletionGuard、可序列化 ProgressMonitor，已有独立测试。

未实现：连接模型的 Agent loop、完整 memory/intent 服务、Redis Worker、checkpoint/fencing、SSE、hybrid RAG、Docker 代码沙箱、React 工作台、真实模型评测。完整计划及当前验证结果见 [PROGRESS.md](PROGRESS.md)。

**所有客户、订单、政策、退款均为合成模拟数据，不接真实支付或商家系统。**退货申请成功不代表已经退款；模拟取消订单才产生模拟退款账本。

## 架构

计划使用 FastAPI + Pydantic + 自写单 Agent Runtime/Harness + MySQL + Redis Streams + 受限 Docker 沙箱 + React。M1 只实现其中可独立测试的业务后端，不引入 LangGraph。

当前服务层不开放 HTTP 执行写操作入口；审批只保存决定。内部 `CommerceService.execute` 用于业务测试，M3 接入执行器前必须增加 run/fencing 约束。

## 本地开发（PowerShell，Python 3.12）

在本项目目录执行。已有 `.venv` 可跳过创建步骤；不要将父目录加入 Git。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -c requirements-dev.lock -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
```

未配置 MySQL 测试地址时，功能测试使用每个测试独立的临时 SQLite 数据库，**不能证明 MySQL 行锁/并发语义**。MySQL 专用测试会明确跳过。

复制 `.env.example` 为 `.env` 并设置随机的本地密码/演示码。当前工作副本已生成独立 `.env`，不要覆盖；它已被 Git 忽略。不要把它截图上传 GitHub。

```powershell
docker compose up -d mysql
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m shopresolve.cli seed
.\.venv\Scripts\python.exe -m uvicorn shopresolve.api:create_app --factory --host 127.0.0.1 --port 8000
```

也可以用安装后的 `shopresolve seed`。数据库仅用专用 `shopresolve`，seed 检测到已有订单会拒绝，不删除旧数据。上述数据库/服务命令的实际验证状态见 PROGRESS，不以此说明替代验证。

打开 `http://127.0.0.1:8000/docs`。用 `POST /v1/demo-sessions` 提交 `account=alice` 及 `.env` 中的演示码，得到短期 bearer token 后在 Authorize 输入。演示登录默认关闭，只在显式启用且提供演示码时可用；不得将开发服务器暴露公网。

示例取消提案：`POST /v1/proposals`，填写 `Idempotency-Key`，请求体：

```json
{"action": "CANCEL_ORDER", "order_id": "order-pending"}
```

审批：`POST /v1/proposals/{id}/decision`，请求体 `{"decision":"approve","expected_version":1}`，仍需幂等 key。相同 key 同请求返回原结果；不同请求返回冲突。

## MySQL 集成测试

需要独立、可丢弃的 **shopresolve_test** 数据库，以及仅针对该库的账号权限。设置 `SHOPRESOLVE_TEST_DATABASE_URL=mysql+asyncmy://.../shopresolve_test` 后运行 pytest。测试会重建该库内的业务表；fixture 明确拒绝其他库名。不要设置成用户已有 MySQL 数据库。

当前本地环境已创建该测试库。可用 `.\.venv\Scripts\python.exe scripts/test_mysql.py` 从项目 .env 安全加载配置、自动换成测试库名运行，无需在命令行粘贴密码。首次克隆项目时，需要先在本项目 Compose MySQL 内执行 `scripts/create_test_database.sql` 创建专用测试库并授权。

## 文档

- [进度、测试结果及续接记录](PROGRESS.md)
- [主架构](docs/design/10-ShopResolve电商Agent架构规范.md)
- `docs/design/10-*`：完整架构；`11-*`：实施任务书；`13-*`：可靠性协议；`14-*`：记忆、意图及循环控制。

## 安全和发布

仅本地演示；不收集真实客户资料。认证令牌只存 SHA-256 摘要；模型/API 密钥尚未接入。`.env`、虚拟环境、数据库和运行 artifact 不提交 Git。GitHub 将先建私有仓库；当前尚未推送。项目代码不得包含父目录中的证件、成绩单或申请文件。

## 评测与归因

现在只有业务/API 回归测试，无 Agent 成功率、RAG 准确率或性能提升数据。未来参考 τ-bench 的政策交互和最终状态检查，自建结果与官方基准分开报告。架构参考 Pi、mini-SWE-agent、SWE-agent 和公开 Harness 工程经验，详见设计文档；没有复制它们的私有源码。

本地验证记录见 [M1 验证报告](docs/evaluation/M1-verification.md)。

许可尚待项目发布时确定；未添加第三方数据集，不宣称自建模型或论文创新。
