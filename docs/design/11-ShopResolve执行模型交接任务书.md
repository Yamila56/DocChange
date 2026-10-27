# ShopResolve 执行模型交接任务书

日期：2026-10-27。当前只完成设计，尚未创建实现仓库或安装依赖。

## 1. 先读什么

完整阅读同目录 `10-ShopResolve电商Agent架构规范.md`，它是主规格。历史 00–09 是不同主题的设计，不能把 FailureLens、DeepTrace、RepoUpgrade 的功能全部混入本项目。

必须完整阅读 `13-ShopResolve可靠执行协议与故障验收.md`；它定义恢复协议、原子边界、测试编号和面试解释依据。用户已确认继续自写 Runtime/Harness，不采用讨论中曾提出的 LangGraph/PostgreSQL 替换建议。

同时完整阅读 `14-ShopResolve记忆意图与Loop控制规范.md`。本期单 Agent，不实现 Multi-Agent；意图/实体/工作记忆、完成与无进展守卫是必做项。以 10 为总图，13 管可靠执行，14 管决策控制；本文是实施入口。

当前选定业务：电商售后与订单异常处理；不是金融，不做真实支付。技术栈：FastAPI + Pydantic + 自写类型化 Runtime + MySQL + Redis Streams + React。不得擅自改成 LangGraph/Pydantic AI 双循环；要换栈先提出 ADR 与迁移取舍。

所有提到的库版本要在开始实施时核验并锁定，主规格没有授权自动购买 API、租 GPU、外发消息、公开部署或覆盖已有数据库。可用 mock provider 完成第一轮开发。

## 2. 开始前检查

1. 读取目标目录适用的 AGENTS.md，检查工作树、现有 Docker/WSL/MySQL/Redis 配置。
2. 拟新建独立 `shopresolve` 仓库/目录；用户有既存实现则先检查再复用。不得清空 `failurelens` 或共享 Docker 卷。
3. 说明主规格中的业务闭环、单循环、持久化与安全边界；列出阻塞项，不为非关键偏好停工。
4. 创建实现计划和状态清单，每项标未开始/实现中/测试通过，禁止只生成目录即报完成。
5. 先实现 mock 模型与固定 clock，不要求用户先提供付费密钥。

## 3. 实施里程碑和验收

### M1：业务内核先行

交付：schemas、SQLAlchemy models、Alembic migration、合成 seed、订单/规则/提案/审批服务、HTTP 契约。

必须通过：金额整数；他人订单不可读；过期退货正确拒绝；部分退货不超数量；相同操作重复请求不重复建账；同 key 不同 body 409；订单版本变化使旧提案失效；拒绝后无动作。先用服务层/集成测试，不等待前端。

### M2：Runtime 与最小 RAG

交付：ModelGateway、mock script provider、Decision union、ToolRegistry、ContextBuilder、有限循环、policy/evidence 数据、一次检索。

增加交付：IntentFrame/EntityResolver、带来源的 TaskGoal/SlotValue、版本化会话摘要、CompletionGuard、ProgressMonitor、run.outcome/stop_reason。完成 14 中 L01–L18 可用脚本模型验证的契约；M3 补 L07/L08/L14/L16 的真实并发/恢复测试。禁止将模型自报 confidence 当执行授权。

必须通过：模型不可伪造 owner/审批；未知工具拒绝；tool call/result 配对；预算耗尽可控退出；用户补问后恢复；引用能定位正确版本；订单事实不从向量库生成。

只读演示跑通后，再接 prepare_action 和已审批执行入口。执行入口参数来自持久 proposal，不接受模型重写。

### M3：后台可靠执行

交付：run/state/checkpoint/events/outbox、Redis consumer group、dispatcher/reconciler、lease/fencing、SSE、cancel。

必须实际测试：

| 注入 | 预期 |
|---|---|
| 同一消息重复投递 | 一份业务结果 |
| DB 提交后、通知前退出 API | outbox 最终送达 |
| 操作事务提交后 kill worker | 恢复回读，不重复退款 |
| 旧 worker 延迟返回 | fencing 拒绝落库 |
| 清空测试 Redis（非用户 Redis） | DB reconciler 重建待处理通知 |
| 等待批准时重启 | 批准状态仍存在，无自动默认批准 |
| SSE 断开再连接 | 按 seq 补发，前端去重 |
| 取消与写提交竞态 | 线性化状态明确，不谎报回滚 |

禁止在外部网络调用期间持有 MySQL 长事务。不要把 asyncio 能跑等同于幂等恢复已完成。

M3 还必须覆盖 13 文档 R01–R16 的确定性测试；R17–R18 在 M5 覆盖。对 PREPARED/IN_FLIGHT/SUCCEEDED/UNKNOWN 工具记录分别验证恢复分支。先用假 Provider + 故障点 hook 测协议，再做真实进程 kill/restart 集成演练。不能用仅抛一个 Python 异常代替全部崩溃测试。

### M4：Agentic RAG 与评测基线

交付：离线索引快照、BM25/dense/RRF/rerank、父节展开、版本过滤、有限补检索、固定数据切分、评测器。

先比较 A 固定检索、B 单 Agent 单次 hybrid、C 完整检索。输出真实结果；无提升也如实保留。语义支持和业务正确分开测。测试集不得反复调参。

### M5：沙箱

交付：CSV 上传校验、受限 manager、可信固定镜像、生命周期回收、输出验证、参考计算 grader。

必须测试：超时、内存超限、网络请求被阻断、宿主文件不可读、密钥不可见、符号链接输出拒绝、超大输出截断、跨 owner artifact 拒绝、孤儿容器仅按项目标签清理。

明确 Docker 容器不是面向任意恶意租户的完全隔离。未经更强隔离审计不公开服务。manager 不接收客户端任意容器参数。

### M6：前端、CI、README

交付：单页工作台，订单/引用/确认卡/时间线；无密钥 CI；真实模型手动评测；README、ADR、威胁模型、数据卡、结果表、演示步骤。

README 中每条命令必须实际跑过或标未验证。前端构建通过；刷新和断线能恢复任务；展示的是工具事件而非伪造模型思维链。

## 4. 每次交付格式

说明本次改了什么、运行了哪些测试及结果、尚未完成什么、下一步是什么。给出实际路径。测试未跑必须直说。不要写未经验证的并发数、耗时改善百分比或任务成功率。

依赖异常先做小规模诊断，不随意升级全局 Python/Conda，不修改已有 MySQL 用户密码，不停掉无关服务。

## 5. 必须留下的 ADR

- 为什么单 Agent + 自写 Runtime，而非框架叠加。
- 为什么 MySQL 为事实源、Redis 是可丢通知与缓存层。
- 为什么操作幂等不等于请求幂等，为什么需要 fencing。
- 为什么业务状态不走 RAG，为什么规则与文档共享版本。
- 为什么只有 CSV 扩展需要代码沙箱，为什么普通业务工具不用 shell。
- 为什么不以 LLM judge 单独衡量任务成功。
- 为什么本期单 Agent，为什么 memory 不是事实源、意图不是授权，以及为何结束状态与业务 outcome 分开。

## 6. 用户切换模型后可直接发送

> 请完整阅读 `D:\新建文件夹\agent-project-design\10-ShopResolve电商Agent架构规范.md`、`D:\新建文件夹\agent-project-design\11-ShopResolve执行模型交接任务书.md` 和 `D:\新建文件夹\agent-project-design\13-ShopResolve可靠执行协议与故障验收.md`，按 M1 起步实现 ShopResolve。保留 FastAPI + 自写 Runtime/Harness + MySQL + Redis 原方案，不切换 LangGraph。先检查环境和已有文件，创建独立实现目录，不改动其他项目或现有 Docker 数据。从可测试的模拟业务内核和 mock 模型开始，逐阶段实现并验收。不要一次生成未验证的全量脚手架，不接真实支付，不购买服务，不擅自换架构。每阶段报告真实测试结果和待办。

上述启动请求还须包含：完整阅读 `D:\新建文件夹\agent-project-design\14-ShopResolve记忆意图与Loop控制规范.md`，采用单 Agent，按 L01–L18 实现并测试意图、记忆和循环控制，不增加 Multi-Agent。
