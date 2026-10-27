# ShopResolve：可靠执行协议与故障验收

日期：2026-10-27。状态：待实现的设计契约，不是测试报告。

本文细化主规格 10 的执行语义，配套 11 的实施任务书。技术栈固定为 FastAPI + 自写类型化 Runtime/Harness + MySQL + Redis Streams。目标是能证明关键业务不变量的业务型 Agent，不是重新发明通用 LangGraph/Temporal。

## 1. 稳定性的承诺范围

模型输出可能不同，供应商可能重复计费；我们不承诺逐 token 确定性或整个系统 exactly-once。承诺以下可测试语义：

1. 已提交的任务不会只存在于进程内存；Redis 通知丢失后可从 MySQL 重建待执行工作。
2. 同一已批准 operation_id 的本地模拟业务副作用最多提交一次；响应丢失可回读。
3. 未确认、过期、越权或参数被改写的操作不能新执行；已经提交的历史结果仍可查询。
4. 旧 Worker 不能覆盖新 Worker 的执行进度或业务状态。
5. 暂停/终态有持久依据；恢复不能依赖前端还开着或某个 Python coroutine 还活着。
6. 客户端连接恢复不造成新任务、新批准或新业务操作。

以上默认 MySQL 已提交事务和必要 artifact 存储仍可用。单机磁盘损坏、数据库丢失、跨机灾备不由 checkpoint 自动解决；备份恢复需单独演练。不承诺高可用部署、零停机或未测试的 RPO/RTO。

安全性与活性分别验收：安全性检查不重复/不越权；活性是在数据库和依赖恢复、重试预算足够时可继续推进。持续故障时正确失败/等待人工，比无限重试更合理。

## 2. 自研边界及可替换接口

| 模块 | 自研职责 | 不承担的职责 |
|---|---|---|
| AgentCore | Decision 联合类型、循环、工具请求与观察配对 | 不直接操作 DB、Redis、容器 |
| ModelGateway | 单次模型请求、响应归一、usage、超时/取消 | 不嵌套隐藏 Agent loop |
| ContextBuilder | 消息选择、摘要、证据与预算装配 | 不创造订单事实或授权 |
| ToolRouter | schema/权限/风险分类、只读并发调度 | 不接受任意 shell 或 SQL |
| RunStore | 原子推进步骤、检查点、事件序号、租约 | 不把 Redis 锁作为最终权限 |
| OperationService | 提案审批绑定、业务事务、唯一结果回执 | 不让 LLM 计算退款最终金额 |
| RecoveryCoordinator | 根据持久状态决定回读、重跑、等待或失败 | 不无条件从对话第一条重新执行 |
| ExecutionEnvironment | 沙箱 submit/query/cancel 与结果引用 | 不暴露 Docker 管理权限给 Agent |

参考 Pi 的分层、mini-SWE-agent 的 Agent/Model/Environment 边界和公开的权限隔离思路。具体租约/事务协议是本项目设计，不归因成这些上游仓库原样实现。实施时建立借鉴记录：参考链接、实际阅读 commit、采用思想、未照搬部分、对应测试。

## 3. 三种“断点”不要混为一谈

| 类型 | 存储位置 | 恢复方法 |
|---|---|---|
| Agent 任务断点 | checkpoints + step/tool/operation records | 从最后提交步骤和待执行清单继续，不重做已完成操作 |
| SSE 断线续传 | events(run_id,seq) | 携带最后已处理 seq 拉取后续持久事件，客户端去重 |
| 文件上传续传 | 本版不实现分片状态 | CSV 上限 2MiB；失败整文件重传，上传 key/哈希防重复登记 |

模型流中断不等于模型可以原地接着生成。未完成的 tool call JSON 不执行；丢弃不完整候选，按预算重新请求。恢复时可以复用已持久的完整模型响应，但不伪造逐 token 连续输出。

## 4. 持久对象和原子推进

在主规格已有表上增加/明确以下字段：

- runs：runtime_version、prompt_version、tool_schema_version、index_snapshot_id、next_event_seq、resume_generation、last_committed_step。
- steps：unique(run_id,step_seq)，phase、input_hash、model_response_ref、expected_call_count、checkpoint_id；步骤输入不可变。
- tool_invocations：unique(run_id,step_seq,call_index)，logical_call_id、effect_class、request_hash、status、result_ref、observed_at、attempt_count。
- model_call_attempts：call_id、attempt、reserved_budget、status、provider_request_id、actual_usage/unknown；一次逻辑调用可对应多次物理请求。
- checkpoints：除状态外记录 pending logical_call_ids、固定版本、摘要版本和 artifact 哈希；不存 pickle、不序列化活的连接对象。

### 4.1 一个步骤的写入顺序

1. **准备调用**：短事务中锁定 run，验证 lease/fence/state_version；分配 step_seq，保存输入和预算预留；提交。
2. **请求模型**：事务外调用 provider。流式草稿仅用于临时展示，不能成为业务执行依据。
3. **保存决策**：完整响应校验后，短事务验证 fence/CAS，保存响应、工具调用意图、checkpoint、step 事件。该事务失败则不能执行任何工具。
4. **执行工具**：读取已落盘意图；只读工具可并发，写动作按审批协议串行。每个结果单独可靠落盘；不能等整批结束才保存已经完成的写操作。
5. **提交观察**：确认所有调用都有完整结果或明确错误后，短事务推进 last_committed_step、checkpoint、事件和 run state_version。缺失结果不能假装成功进入下一模型步。

模型结束、审批等待和任务终态也走相同的状态/事件原子提交。终态后不得被迟到的 token callback 改回 RUNNING。

### 4.2 Artifact 和数据库不是一个事务

小型关键状态/结果尽量直接存 DB JSON，避免依赖文件才能回查业务操作。大型响应先写临时文件、校验哈希、完成持久化并原子 rename，再由 DB 事务引用；rename 的原子性不能等同断电持久性，需实现平台适用的 flush/fsync 策略并记录限制。

崩溃可留下没有被 DB 引用的孤儿文件，由延迟 GC 按引用和 TTL 清理；禁止 DB 已引用但文件尚未写完。跨 Worker 必须共享可访问的 artifact store；本地路径方案仅支持同机部署。缺失/哈希不符的关键 artifact 阻止恢复，标记完整性错误，不自动重新生成后覆盖原引用。

### 4.3 事件序号

在锁定 run 的事务中分配递增 seq，并与对应状态一起插入 events。禁止多个 Worker 用无锁 MAX(seq)+1 分配。业务状态提交成功后才发布实时通知；通知不是权威日志。

## 5. 恢复算法：按状态决策，不靠猜

工具执行状态：PREPARED → IN_FLIGHT → SUCCEEDED / FAILED / UNKNOWN。FAILED 必须区分确定未执行与结果未知；不能把所有 timeout 都叫 FAILED 并重试。

| 持久状态/类型 | 恢复动作 |
|---|---|
| 完整模型响应已保存，工具还未执行 | 使用同一 logical_call_id，继续工具阶段，不重新问模型决定动作 |
| 只读工具 SUCCEEDED | 复用已保存观察；需要新鲜数据时显式发起新 read，不悄悄覆盖历史结果 |
| 只读工具 IN_FLIGHT 且无结果 | 可重读，记录新 attempt 和新 observed_at；评测固定 snapshot/clock |
| 本地业务写 IN_FLIGHT | 先按 operation_id 查回执；存在则回填；不存在才进入带同 ID 的受控事务 |
| 远程写 IN_FLIGHT | 先查询下游；仅在其幂等/状态查询协议保证下重试；否则 UNKNOWN 并转人工 |
| 沙箱 IN_FLIGHT | 按 job_id 查询 manager/运行实例；先确认原任务终止再决定重跑，不无限创建容器 |
| WAITING_APPROVAL | 等已保存的合法审批事件，不默认同意、不重新生成不同参数提案 |
| SUCCEEDED/FAILED/CANCELLED/EXPIRED | 不重新执行；返回持久结果或失败原因 |
| schema/runtime 不兼容或 checkpoint 损坏 | 隔离该 run，显式迁移或人工处理；禁止新代码盲读旧 checkpoint |

### 5.1 本地业务事务的关键顺序

先读已有 operation 回执，已提交结果可在审批过期后继续查询；这不是重新执行。若没有结果，则开始短事务：按固定顺序锁 run → proposal/approval → order/相关 item；验证当前 fence、run 未取消、批准主体/参数、有效期、订单/政策版本、业务约束；用唯一 operation_id 插入操作记录；更改订单/账本；保存结果与业务事件；提交。

任何一步失败全部回滚。同 operation_id 不同 request_hash 返回冲突。唯一约束作为最终防线，不依赖一次“先查不存在”。DB 死锁按同 operation_id 有限重试，重试前读取回执；不能生成新 ID 绕开冲突。

### 5.2 CAS 与 fencing 的作用不同

state_version 保护并发状态更新：update 必须匹配读到的版本。fencing_token 区分 Worker 所有权代次：新 Worker 接管后增加 token，旧 Worker 不得落库。二者不可替代。

所有 Worker 写 run/checkpoint/event 的入口都统一经过 RunStore 的带 fence 方法。不能某个 tool 直接调用 repository 写业务绕过检查。心跳失败到租约不确定时停止新动作，旧的外部请求可能还在运行；真实下游若不识别 fence，需它自己的幂等协议，不能宣称本地 fence 已终止远程操作。

## 6. 多轮输入、审批和恢复消息的竞态

conversation 增加 active_run_id/version。创建任务时锁 conversation 行，仅当没有非终态任务时创建；等待态仍占 active_run。终态时以匹配 run_id 的条件清空，防止迟到 worker 清掉新任务指针。

WAITING_USER 的补充消息必须指向 run_id + wait_generation；WAITING_APPROVAL 的决策必须指向 proposal_id/version。同一等待点两个浏览器重复提交：同幂等 key 回原结果；不同且冲突的内容只有一个能推进，另一个 409 后刷新。不能把后一条消息悄悄拼进已经执行的步骤。

每次恢复事务增加 resume_generation，并产生唯一 outbox(run_id,generation,kind)。重复或旧 generation 通知只 ACK，不回退状态。运行中收到新指令返回明确的“正在处理，先取消或等待”，MVP 不实现含糊的实时 steering。

用户可一次批准多个相互独立的提案，但每个 proposal 单独绑定授权和结果，允许部分成功；不把跨订单操作伪称为全有或全无事务。界面分别呈现状态。

## 7. 超时、预算、背压和退出

超时分层：连接/读取超时、单工具截止时间、单次模型请求时限、run 活跃执行总预算、人工等待过期。重启不能重置累计预算；保存已用值与每次调用预算预留。

调用前预留 token/调用次数额度；实际 usage 到达后结算差额。网络失败且可能已被供应商执行的请求标 unknown，保留保守预留，不因重启恢复“免费额度”。货币费用无法精确获知时报告估算区间/未知，不声称绝对费用上限。

按 provider 设置独立并发和熔断状态；连续服务故障时停止扩大请求压力，退避有抖动且有上限。业务参数错误、越权、拒绝批准不可重试。检索失败可返回证据不足，不能悄悄用模型记忆决定退款。

队列有容量上限；DB outage 时禁止接受只存内存的新任务。Redis outage 时默认对新任务返回 503，已提交 DB 的任务保留并待恢复；不要回滚或删除已经接收的任务。取消有独立持久标记，恢复也检查。

优雅退出：先停止领取新消息，将 readiness 置 false；当前步骤在有限 grace period 内完成或退出；没完成时不伪造终态、不提前 ACK。租约过期由别的 Worker 接管。单机 Ctrl+C、进程强杀和完整重启都要测试。

## 8. SSE、草稿与文件传输

持久事件使用 (run_id,seq) 标识。客户端完成应用后保存 cursor，重连携带 cursor；服务器鉴权后查询 seq>cursor 的事件，并持续轮询/监听唤醒。客户端按 seq 去重；状态卡以 DB snapshot/版本为准。

先回放后订阅存在竞态，因此每次通知只触发重新查询 DB；即使订阅期间漏通知，周期性扫描仍能补齐。慢客户端有发送缓冲上限，超限断开并给出重连方式，不能无限积累内存。

事件保留期可配置。cursor 已过期时返回专用错误与 snapshot endpoint；snapshot 返回一致的状态版本和 last_event_seq，客户端从该 seq 恢复。UI 把 provisional 模型草稿与 durable 结果分开；重连可清空草稿，不能将草稿中的“成功”提升为业务事实。

CSV 不做断点分片上传：上传中断留下的临时文件按 TTL 回收；上传完成且校验后才登记 artifact。上传请求同 key 同内容返回同 artifact，不同内容 409。哈希去重限定 owner，不向用户泄漏其他人的文件是否存在。

## 9. 版本升级、备份与运维边界

每个 run 固定 prompt/tool schema/runtime/index 版本；在途 run 尽量由兼容版本 Worker 继续。新增字段优先向后兼容；破坏性迁移先停接新任务、drain/暂停在途任务、备份，再迁移；旧 checkpoint 没有迁移器则不得继续执行写动作。

不要按“全量事件重放”重新执行业务。本项目使用检查点与操作回执，并非通用 event-sourcing 系统。轨迹回放分两类：只播放已记录结果的审计模式、在独立测试库重跑的实验模式；两者都不得重新触碰原账本。

本地演示至少做一次 MySQL 备份与恢复到新测试库的演练，并核对 operation/checkpoint 引用与 artifact。不得用从未测试的备份作恢复保证。运营文档给出 dead-letter/UNKNOWN 查询与人工处理步骤；人工重试需审计、保持原 operation_id，不能手工删回执“让它再跑一遍”。

## 10. 故障测试矩阵和质量门禁

实现 fault hook：after_run_commit、after_stream_publish、after_model_response_commit、before_tool_commit、after_tool_commit、before_checkpoint_commit、after_approval_commit。hook 仅测试环境可用，不向公网 API 暴露。

| 编号 | 注入/场景 | 验证断言 |
|---|---|---|
| R01 | 相同创建请求并发两次 | 同 run_id；一份 outbox 意图；不同 body 冲突 |
| R02 | 创建 DB 提交后退出、尚未通知 Redis | 服务恢复后任务能被消费 |
| R03 | 通知发布后 dispatcher 崩溃并重发 | 仅当前 lease 可推进；无重复副作用 |
| R04 | 模型完整响应保存后 kill worker | 使用原决策与工具 ID，不再决定另一笔动作 |
| R05 | 不完整模型流包含半段 tool JSON | 无工具执行；预算记录未知/重试 |
| R06 | 业务事务提交前强杀 | 原子回滚；重试仅一次有效结果 |
| R07 | 业务提交后、checkpoint 前强杀 | 回读原 operation；账本只有一条 |
| R08 | A worker 暂停，B 接管，A 恢复 | A 的状态/业务写均被 fence 拒绝 |
| R09 | 审批提交后重启；两浏览器相反决策 | 仅一份合法决定推进，冲突可解释 |
| R10 | 批准后订单/政策版本改变 | 新动作不执行；旧提案 STALE；须重批 |
| R11 | cancel 与业务提交并发 | 合法线性化：取消先成功则无新写，写先提交则结果可见 |
| R12 | 测试 Redis 数据丢失 | DB reconciler 恢复非终态任务；等待态不误执行 |
| R13 | SSE 在回放/订阅间断开；慢客户端 | 事件最终补齐、去重；不创建新 run；内存有界 |
| R14 | checkpoint 缺文件/哈希错误/旧 schema | 隔离失败，不猜测状态或重做写操作 |
| R15 | provider 持续超时、DB 暂断、队列满 | 有限重试/背压；不重置预算；不丢已接收任务 |
| R16 | 并发部分退款/死锁/跨用户访问 | 累计不超额、同操作唯一、权限无绕过 |
| R17 | 沙箱完成后回执丢失或 manager 重启 | job 查询/孤儿处理有界，无无限容器创建 |
| R18 | 上传中断、输出符号链接、宿主/网络访问 | 临时文件可清理；未授权访问拒绝；无敏感文件泄漏 |

单元/状态机测试用固定 clock 和 fake provider；集成测试用真实 MySQL/Redis、多个进程与受控 kill/restart，不能全部用 SQLite 代替锁语义。针对状态迁移增加属性测试：随机重复、乱序、取消、重试序列，持续断言金额/授权/终态不可回退等不变量。

每次 CI 跑确定性的协议测试；耗时进程故障矩阵单独 job/手动运行，说明覆盖差异。发布候选必须保存一次完整集成报告：commit、依赖版本、场景 seed、故障点、进程日志、最终状态 SQL、assertion 结果和用时。沙箱测试只在无敏感凭证 runner 执行。

## 11. 面试时如何解释而非背术语

| 问题 | 应能用代码和测试说明的答案 |
|---|---|
| 为什么自研？ | 为了清楚掌控特定业务的循环与可靠性边界；并非比成熟框架普遍更好，范围受到约束 |
| 为什么 Redis 锁不够？ | 锁过期后旧进程可能仍运行；数据库 fence/CAS 和业务唯一约束负责拒绝陈旧写入 |
| 为什么有 checkpoint 还会重复退款？ | 工具可能已成功但 checkpoint 尚未写入，所以需要持久 operation 回执和稳定 ID |
| 超时能直接重试吗？ | 只读通常可；写操作先查结果，未知外部副作用不盲重试 |
| 是否 exactly-once？ | 通知至少一次；本地同 operation 的账本效果用事务/唯一约束控制，不承诺模型调用仅一次 |
| SSE 断了会重跑 Agent 吗？ | 不会，重新拉取持久事件；任务在 Worker 独立运行 |
| 恢复是否还用原来的政策？ | 历史证据固定版本便于审计，执行前按业务规则核验适用版本；变化则提案过期 |
| 如何证明稳定？ | 指向故障注入点、真实 kill/restart 记录、最终账本及不变量测试；不靠架构图和口头“生产级” |

所有“已做到”必须有实际实现和测试证据。当前只完成协议设计，不得将这些设计条目直接写成已实现的简历成绩。
