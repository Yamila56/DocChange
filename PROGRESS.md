# ShopResolve 进度与续接记录

更新：2026-10-27。项目位置：`D:\证书及申请资料\ShopResolve`。

## 当前阶段

持续实施中：单 Agent demo 闭环、MySQL/Redis Worker、检查点/fencing/SSE、真实 ONNX 混合检索、独立 Docker 分析模块及 React 已接通。整个项目未完成：真实模型尚待服务商/模型名与本地 key，CSV 还未接 Agent 自动工具，物流工单/商品替代及部分质量门禁待补齐。

本地代码快照：`f80c69e`（M1 + M2 初始契约）；这是本地提交，不是 GitHub 推送。

本轮验证：MySQL 全套 47 passed；临时 SQLite 集 44 passed / 3 skipped；MySQL 并发三项单跑 3 passed；实际 HTTP 子进程 smoke 通过（readiness/OpenAPI/登录/查单/越权拒绝）。Alembic upgrade/check、Ruff、pip check 已通过。CI 尚未远程运行。

## 里程碑

- M1 业务内核：已实现并通过本地验证（订单、政策、提案、审批、幂等、账本、迁移、测试）。
- M2 Agent / memory / intent / loop：进行中；demo 完整闭环可运行，真实模型接口为显式付费 opt-in 的兼容 JSON adapter（尚未调用）。新增 15 项循环测试通过，1 项 MySQL 认领竞争在 MySQL 全套验证。
- M3 Redis 后台可靠执行与 SSE：进行中；真实 Redis 丢消息恢复、重复通知、MySQL 租约竞争等已有测试。事务提交前/后两个子进程硬退出测试通过，尚不能声称全套 R01–R18 已通过。
- M4 hybrid / agentic RAG 及评测：已实现 BM25 / 中文 BGE 向量 / RRF / BGE 量化重排 / 父子片段与权限版本过滤。实际检索消融结果已生成，见 docs/evaluation/retrieval-v1-results.md 和配套 JSON；数据仅为小型合成集，不是 Agent 成功率。
- M5 沙箱：独立 manager、owner-scoped CSV artifact/analysis API、固定镜像和安全收集已实现。5 项真实 Docker 测试通过，另有上传/权限/完整性及内部 manager 认证测试。实际 HTTP 上传→manager→Docker→44800 分结果核验通过。沙箱未接入 Agent 工具路由，当前是工作台独立模块。
- M6 React、CI、真实模型评测与发布：React TypeScript/Vite build 通过，实际 HTTP 检查验证静态入口可用；尚未做浏览器视觉/交互 QA。真实模型服务待用户提供服务商/模型并本地配 key，未产生付费调用。CI 文件已编写，未在 GitHub 执行；未创建远程仓库。

最新新增验证：15 项 Agent 生命周期单元测试、2 项真实 Redis 测试通过；2 项真实 MySQL 子进程硬退出测试通过（事务提交前/后），最终各仅一笔模拟退款。API/Agent/检索/队列组合测试 28 passed / 1 skipped。每组是不同范围的运行，不能简单相加成独立场景总数。

检索冻结草案测试集 16 条（外加 8 条 dev），BM25 Recall@3=93.75%、混合+重排=100%；Recall@1 从 68.75% 到 75%，仅多命中一个样本（+6.25 个百分点），不能夸大泛化提升。重排 CPU P50≈1343ms，明显慢于 BM25≈0.4ms。报告保留所有配置，不隐藏纯向量较弱结果；标注由助手构造，待独立人工审核。

## 续接入口

先读本文件，再读 docs/design 中 10、11、13、14。运行 `.venv\Scripts\python.exe -m pytest` 和 Ruff，排查实际结果后更新记录。M1 的 execute 是内部服务入口，尚未接入 run/fencing；不得直接开放 HTTP 写执行接口。下一阶段必须补运行上下文权限。

更新：run-bound 提案现在已在同一业务事务检查有效 lease/fencing 和 owner；不能继续沿用上段旧 M1 注记当作当前未实现。没有开放任意 execute HTTP 入口。当前 `.cache` 下有模型/上传/沙箱运行数据，都是 ignored，不应推送。

## 环境与安全

Python 3.12.13 的项目专用 `.venv`，未改原 Conda。Docker Desktop 已启动；独立 shopresolve-mysql-1（MySQL 8.4.6）绑定 127.0.0.1:13316，独立卷 shopresolve_shopresolve_mysql。shopresolve 库含合成演示订单；shopresolve_test 供测试重建。其他数据库/容器未修改。所有数据为合成，无真实交易。未连接模型供应商，不需要 API key。HTTP smoke 的临时进程已自行终止。

MySQL 首次并发测试发现间隙锁死锁，调整订单/回执锁顺序并增加只针对 1205/1213 的有限重试后全套通过，详见 docs/adr/0001-business-kernel-first.md。真实 kill/restart 恢复仍未实现，不将事务异常回滚测试冒充进程恢复。

用户已确认 GitHub 先私有，检查后再公开。GitHub 账号已能读取；远程仓库尚未创建/推送。不可把上层申请资料目录加入 Git；只推送 ShopResolve 内已审查文件。不得提交 .env、.venv、个人证件或成绩单。

额度/会话突然停止时，以本文件及实际 Git diff/测试状态为准，不能把计划当完成。

## 下一步

最新优先级（替代下方旧计划）：

1. 本轮 MySQL 全套已完成：87 passed，报告 docs/evaluation/mysql-regression-latest.xml；本地是 81 passed / 6 skipped。历史 mysql-regression.xml 有一次 405/404 失败，已修复 API fallback，不能用历史失败文件当最终结果。之后又补了 CSV 表头公式拒绝和 provider 配置变更守卫，需看后续复测记录。
2. CSV 作为 Agent 的受控工具整合；物流人工工单及替代商品业务按规格补齐；完善 admission/rate limit、provider 配置锁定、优雅退出和剩余故障用例。
3. 真实模型服务信息与本地 key 到位后，先有明确调用预算的小样本 contract test，再做冻结任务集/多次重复评测。当前用户仅确认会提供，尚无服务商或模型名。
4. 浏览器交互 QA、CI 运行及安全检查、GitHub 私有推送（完成验收后）。

本轮已生成 docs/evaluation/workflow-regression-v1-results.json（20/20 deterministic 回归、0 额外账本），不算真实 LLM 能力。真实 API+dispatcher+worker+Redis+SSE 多进程 smoke 和独立 sandbox HTTP smoke 均通过；测试进程已终止，数据库/Redis 保留。

1. 完成 M2 的 ModelGateway/scripted provider、工具注册与单 Agent 循环；先只读和受控提案，不直接接执行写工具。
2. 实现会话/TaskGoal/slot/memory 的持久化与上下文装配，对齐 L01–L18。
3. M3 再接 outbox/worker/run lease/fencing/审批恢复，最后开放经保护的执行路径。

运行命令：`.venv\Scripts\python.exe -m pytest -q`、`.venv\Scripts\python.exe -m ruff check .`。MySQL 全套需要 SHOPRESOLVE_TEST_DATABASE_URL（用 .env 的项目数据库凭据，但库名改成 shopresolve_test；不要打印/提交密码）。实际 HTTP 检查用 `.venv\Scripts\python.exe scripts/smoke_http.py`。

本地 MySQL 测试也可直接运行 `.venv\Scripts\python.exe scripts/test_mysql.py`，它自动读取本项目 .env 并仅切换到 shopresolve_test，避免复制密码。Docker MySQL 当前保留运行供下阶段开发；没有常驻 API/Worker。请不要对该 Compose 执行带卷删除的清理命令，以免丢失演示数据。
