# 设计—代码—证据审查（2026-10-27）

结论：项目是可运行的本地开发版，不是全部规格已经完成，也没有上线。
这份清单依据 docs/design/10、11、13、14 与当前代码/测试逐项对照。
“部分”不是失败，也不是完成；既有测试通过不等于对应设计矩阵全覆盖。

本轮补强更新：见 docs/evaluation/live-eval-readiness.md。新增授权候选/明确选单、能力清单、
只读资格工具、逐目标条款覆盖/检索预算、持久版本摘要、取消时权威回执合并。
下表和故障映射相应更新；未消除的生产/完整验收缺口仍保留。

## 模块状态

| 范围 | 当前证据 | 尚缺 / 边界 |
|---|---|---|
| 取消 / 退货 | service.py；审批、整数金额、业务事务、稳定 operation 回执；MySQL 并发和真实进程退出测试 | 全部为合成模拟账本，不是实际支付 |
| 单 Agent Harness | runtime.py / runstore.py / queue.py；先存决策再执行、lease/fence、checkpoint、outbox、等待释放 Worker、有限循环 | 并非通用工作流引擎；完整恢复矩阵未过 |
| Memory | Run.state 中 goals/slots/source、messages、observations、results；摘要版本/来源哈希随 checkpoint 持久化；补问恢复测试 | 不是独立 summary 表；无完整 TTL/删除策略；本期不做长期画像 |
| 意图 / 实体 | 类型化 IntentFrame、来源验证、多目标；授权词法候选、明确选单、只读资格核验、未实现能力拒绝 | 不声称通用语义消歧；多目标回复绑定与目标修改体验仍需完善；demo 是规则控制器 |
| Loop | CompletionGuard、ProgressMonitor、累计调用与活跃时长预算、服务端渲染业务事实 | 一次恢复策略与合法状态轮询未完整接线；部分 L 测试仍缺 |
| 异步 | API 202、独立 Worker、Redis Streams、只读工具并发、每次独立 session；HTTP 连接池；RAG 在线程中计算 | 无完整用户级限流 / 全局队列背压 / provider 熔断；to_thread 不等于可强制取消 CPU 计算 |
| 退出 | CLI 停止领取后有有限 grace，超时取消当前 coroutine；不提前 ACK | 新增协程测试，不冒称整套 Ctrl+C/强杀重启均已验证 |
| RAG | BM25+dense+RRF+rerank、父节展开、owner/version 过滤；每目标两次补检索预算、条款覆盖、动作前版本核验 | 无完整 region/category/生效区间过滤、原子索引发布、逐声明语义支持评测；覆盖检查不是通用蕴含判断 |
| CSV / 沙箱 | 独立受认证 manager；owner 隔离上传；真实无网络 Docker；输出/超时/符号链接测试；工作台模块 | 尚未成为 Agent 自动调用工具；OOM/PID/输出上限等验收不完整；无完整孤儿/TTL 清理 |
| 物流 / 商品 | 当前 get_shipment 仅返回订单/发货状态，不虚构 ETA | 没有真实事件链、物流人工工单、商品/库存和替代品业务闭环 |
| 前端 | React/TS 构建与 HTTP 静态入口验证；SSE seq 去重/重连代码 | 未做真实浏览器交互/视觉验收 |
| 部署 / CI | Compose 仅 MySQL/Redis；CI 文件存在；本地 smoke 通过 | 无 API/worker 发布镜像和一键完整编排；无公网部署、生产身份/TLS、远程 CI 执行与备份恢复演练 |
| DeepSeek | 官方 HTTPS 专用配置、JSON 协议、非思考模式、输出/输入限制、拒绝空/截断响应、连接复用；离线 MockTransport 测试 | 未配置实际 key，未调用真模型；JSON 决策协议不是原生 function-calling 排行榜协议 |
| 评测 | 20 条 deterministic 回归、16 条冻结草案检索问题及消融；故障/并发测试 | 无真实 LLM 成功率；标签待独立审核；无公开基准成绩、完整基线/多次重复试验 |

## R01–R18 故障验收映射

| 编号 | 实际证据 | 判定 / 缺口 |
|---|---|---|
| R01 | test_duplicate_creation_has_one_outbox；业务幂等并发测试 | 部分；run 创建的真实并发故障组合仍需补 |
| R02 | outbox 与 dispatcher/worker 集成 | 部分；API 在提交后真实退出场景未完整复现 |
| R03 | test_outbox_worker_and_duplicate_notification | 部分；未真实强杀 dispatcher 于 publish 后 |
| R04 | after_model_response_commit hook；新增真实 MySQL 查单子进程硬退出，恢复复用原工具决策且不再调模型 | 已有该具体场景证据；不泛化为所有工具/决策组合 |
| R05 | 决策半段 JSON 和 provider length/空内容拒绝测试 | 部分；当前非流式，未实现流式中断协议 |
| R06 | test_crash_process 的 before_tool_commit 子进程 os._exit | 已有真实 MySQL 进程证据；具体环境见 JUnit |
| R07 | 同上 after_tool_commit；恢复时原 operation 回读 | 已有真实 MySQL 进程证据 |
| R08 | test_fenced_worker_cannot_commit_after_takeover；认领竞争测试 | 部分；更完整旧 Worker 迟到业务写组合待补 |
| R09 | 审批持久化、旧浏览器版本拒绝、审批后恢复；新增 MySQL 修改/批准竞态 | 部分；相反审批决策与完整重启组合尚未测全 |
| R10 | order_change / policy_change_invalidates_approval | 已覆盖本地业务约束；不是远程业务系统保证 |
| R11 | 取消先行、提交后取消保留回执、MySQL cancel/execute 同时竞争 | 已覆盖关键两种序与并发案例；不证明所有交错序列 |
| R12 | test_redis_notification_loss_recovers_from_database | 已有真实 Redis 删除专用测试 stream 后恢复证据，不曾清空用户 Redis |
| R13 | SSE cursor 回放测试、HTTP smoke | 部分；慢客户端/回放边界、过期 cursor 完整协议尚缺 |
| R14 | corrupt_checkpoint 拒绝、runtime/hash 守卫 | 部分；关键 artifact 丢失/旧 schema 的完整组合尚缺 |
| R15 | 调用预算、超时、有限死锁重试、provider 配置守卫 | 部分；限流/背压/持续故障矩阵尚缺 |
| R16 | MySQL 并发准备/执行、跨 owner、部分退货、死锁重试测试 | 部分；全属性序列与随机交错测试尚缺 |
| R17 | manager durable job identity、相同 job 查询/回读测试 | 部分；真实 manager 进程重启/孤儿容器启动清理待补 |
| R18 | Docker 无网络/host/key、符号链接、CSV owner/hash 测试 | 部分；上传中断 TTL/全部资源攻击验收尚缺 |

## L01–L18 控制验收映射

| 编号 | 证据 / 状态 |
|---|---|
| L01 | 咨询不建提案与模式契约测试；demo/脚本证据，不是真模型意图准确率 |
| L02 | 两目标独立提案测试；真实模型多意图覆盖待测 |
| L03 | 已有授权候选→补问→明确序号选择的端到端测试；复杂指代/多目标仍需扩展 |
| L04 | owner 注入、他人订单/运行/附件拒绝测试 |
| L05 | 缺数量补问与恢复测试；conflicting 槽位全生命周期尚需完善 |
| L06 | 执行只能读取结构化 Approval；“好的”不构成 API 审批；双提案专测待补 |
| L07 | revisions/version/generation 约束已实现；审批竞争真实并发待补 |
| L08 | RUNNING 不接受 revisions 的等待态约束；专门回归待补 |
| L09 | operation 不删除、取消结果回执；反悔完整对话案例待补 |
| L10 | 摘要来源/版本持久化；长历史与错误金额摘要专测已补；更多真实长会话仍待评测 |
| L11 | 消息/run/artifact owner 隔离；相似历史摘要跨 owner 组合待补 |
| L12 | 提前 FinalAnswer/缺回执守卫测试；金额由服务端渲染 |
| L13 | 两目标等待状态与完成覆盖守卫；部分结果 UI 专测待补 |
| L14 | repeat snapshot 恢复契约单测；完整运行/重启/表面查询改写组合待补 |
| L15 | 缺少独立受预算状态轮询调度，未完成 |
| L16 | 暂停、取消、累计预算、回执恢复已有实现；完整交错矩阵未完成 |
| L17 | DECLINED 与 FULFILLED 区分测试；人工工单未实现，不冒称 ESCALATED 已建单 |
| L18 | 已批准动作落账后不用再次调用模型即可核验/模板收尾；专门 unavailable provider 测试已补 |

## 发布前顺序

优先补限流/背压、R04/R09/R11/R15 和摘要/实体解析的端到端证据，再接 CSV Agent 工具与物流工单。
随后完整本地容器编排、浏览器 QA、备份恢复和私有 GitHub CI。
真实模型先用小批次 contract smoke 排错，再冻结独立任务集，不能边看测试答案边改到满分。
不以随意的“完成 90%”替代上述缺口清单。
