# M1 验证记录

日期：2026-10-27。环境：Windows / Python 3.12.13 / Docker Desktop Linux Engine 29.7.2 / MySQL 8.4.6。依赖锁见 requirements-dev.lock。

## 已运行

- 临时 SQLite 功能/API + Agent 控制契约测试：44 passed、3 skipped。
- 专用 MySQL shopresolve_test 上的全套测试：47 passed（无跳过）。
- MySQL 专用并发三项单独重跑：3 passed。
- Ruff lint、format check、pip check：通过。
- Alembic upgrade head、alembic check：通过，无待生成 schema 差异。
- 合成订单 seed：在项目专用 shopresolve 库成功执行。

测试范围包括：请求去重/参数冲突/大小写语义、授权隔离、审批/提案完整性、状态与政策版本改变、退货数量/时间/品类约束、事务错误回滚、操作回执重放、两个合法部分退货，以及 MySQL 并发请求/重复执行/不同提案冲突。

Agent 部分目前仅为类型契约与确定性守卫测试：多意图输入类型、来源验证、指代消歧、过早完成拒绝、操作凭据和证据校验、无进展计数可序列化等。**不代表已经接通模型、实现整个 Agent 或完成全部 L01–L18。**

## 发现并修复的问题

首次 MySQL 全套 32 passed、1 failed：两份独立提案争用同一订单时，缺失操作回执的间隙锁导致死锁。调整为先锁订单、后锁回执，并增加仅对 1205/1213 的有限本地事务重试。修正后的 MySQL 全套 47 项通过。

MySQL 8 caching_sha2_password 首次连接需要 cryptography，已作为显式依赖加入；没有为绕过依赖而降低数据库认证方式。

## 尚未证明

Redis 恢复、Worker fencing、检查点崩溃恢复、真正 kill/restart 后执行恢复、SSE、模型语义正确率、RAG 成功率、沙箱安全均未测试/尚未实现。事务内注入异常测试不能替代真实进程强杀测试。CI workflow 已编写，但尚未在 GitHub 执行。

实际 HTTP 进程 smoke 也已通过：独立子进程启动 Uvicorn，验证就绪/OpenAPI/演示登录/当前用户订单/跨用户拒绝后退出该子进程。没有启动常驻 API。后续新增测试与提交位置以 PROGRESS.md 为最新记录。
