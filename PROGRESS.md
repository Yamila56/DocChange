# ShopResolve 进度与续接记录

更新：2026-10-27。项目位置：`D:\证书及申请资料\ShopResolve`。

## 当前阶段

M1 业务内核已实现并验证；M2 已开始类型契约和确定性控制组件。完整 Agent 尚未实现，不能作为完成项目展示。

本地代码快照：`f80c69e`（M1 + M2 初始契约）；这是本地提交，不是 GitHub 推送。

本轮验证：MySQL 全套 47 passed；临时 SQLite 集 44 passed / 3 skipped；MySQL 并发三项单跑 3 passed；实际 HTTP 子进程 smoke 通过（readiness/OpenAPI/登录/查单/越权拒绝）。Alembic upgrade/check、Ruff、pip check 已通过。CI 尚未远程运行。

## 里程碑

- M1 业务内核：已实现并通过本地验证（订单、政策、提案、审批、幂等、账本、迁移、测试）。
- M2 Agent / memory / intent / loop：进行中；已完成 decision/intent 类型、来源校验、实体消歧、完成守卫及可序列化无进展监测；未接模型/完整循环/memory 存储。
- M3 Redis 后台可靠执行与 SSE：未开始。
- M4 hybrid / agentic RAG 及评测：未开始。
- M5 沙箱：未开始。
- M6 React、CI、真实模型评测与发布：未开始。

## 续接入口

先读本文件，再读 docs/design 中 10、11、13、14。运行 `.venv\Scripts\python.exe -m pytest` 和 Ruff，排查实际结果后更新记录。M1 的 execute 是内部服务入口，尚未接入 run/fencing；不得直接开放 HTTP 写执行接口。下一阶段必须补运行上下文权限。

## 环境与安全

Python 3.12.13 的项目专用 `.venv`，未改原 Conda。Docker Desktop 已启动；独立 shopresolve-mysql-1（MySQL 8.4.6）绑定 127.0.0.1:13316，独立卷 shopresolve_shopresolve_mysql。shopresolve 库含合成演示订单；shopresolve_test 供测试重建。其他数据库/容器未修改。所有数据为合成，无真实交易。未连接模型供应商，不需要 API key。HTTP smoke 的临时进程已自行终止。

MySQL 首次并发测试发现间隙锁死锁，调整订单/回执锁顺序并增加只针对 1205/1213 的有限重试后全套通过，详见 docs/adr/0001-business-kernel-first.md。真实 kill/restart 恢复仍未实现，不将事务异常回滚测试冒充进程恢复。

用户已确认 GitHub 先私有，检查后再公开。GitHub 账号已能读取；远程仓库尚未创建/推送。不可把上层申请资料目录加入 Git；只推送 ShopResolve 内已审查文件。不得提交 .env、.venv、个人证件或成绩单。

额度/会话突然停止时，以本文件及实际 Git diff/测试状态为准，不能把计划当完成。

## 下一步

1. 完成 M2 的 ModelGateway/scripted provider、工具注册与单 Agent 循环；先只读和受控提案，不直接接执行写工具。
2. 实现会话/TaskGoal/slot/memory 的持久化与上下文装配，对齐 L01–L18。
3. M3 再接 outbox/worker/run lease/fencing/审批恢复，最后开放经保护的执行路径。

运行命令：`.venv\Scripts\python.exe -m pytest -q`、`.venv\Scripts\python.exe -m ruff check .`。MySQL 全套需要 SHOPRESOLVE_TEST_DATABASE_URL（用 .env 的项目数据库凭据，但库名改成 shopresolve_test；不要打印/提交密码）。实际 HTTP 检查用 `.venv\Scripts\python.exe scripts/smoke_http.py`。

本地 MySQL 测试也可直接运行 `.venv\Scripts\python.exe scripts/test_mysql.py`，它自动读取本项目 .env 并仅切换到 shopresolve_test，避免复制密码。Docker MySQL 当前保留运行供下阶段开发；没有常驻 API/Worker。请不要对该 Compose 执行带卷删除的清理命令，以免丢失演示数据。
