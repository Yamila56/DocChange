# ShopResolve：记忆、意图与 Loop 控制规范

日期：2026-10-27。状态：待实现契约，无实测成绩。作为主规格 10 的必读补充，与可靠性协议 13 配套；执行顺序由 11 管理。

## 1. 决策：当前版本单 Agent，不实现 Multi-Agent

采用一个有状态业务决策 Agent，搭配确定性工具、政策检索子流程和执行 Harness。一个循环可调用多次模型；可在多个 Worker 上并行处理不同会话，但同一会话只有一个非终态 run。

理由：售后任务共享订单事实、政策版本和授权链，业务操作需要统一决策。初版没有足够独立的长任务需要多个自主 Agent；增加协调者/反思者/审批 Agent 会增加通信成本和冲突面，不能直接提升成熟度。检索器、规则检查器、CompletionGuard 不是独立 Agent。

本期不实现：Agent 消息总线、角色群聊、递归委派、独立子 Agent memory。保留工具/检索接口即可，不为未来猜测实现复杂抽象。

后续只有出现清晰的独立任务（例如多来源商品研究）、上下文隔离需求，且同模型/预算下实验表明收益时，再提出 Multi-Agent ADR。届时子任务契约应含 parent_run_id、task_id、输入引用、权限、预算、deadline、输出 schema；子 Agent 默认只读，副作用仍由主业务层批准。此处不是当前交付要求。

## 2. Memory：四层职责和事实优先级

| 层 | 存储/字段 | 写入与读取原则 |
|---|---|---|
| 会话记忆 | messages、conversation_summaries | 原文追加；摘要带覆盖 message_seq、来源引用、summary_version |
| 任务工作记忆 | task_goals、slot_values、entity_candidates、pending_question | 每次更新带来源、版本；工作状态随 checkpoint 持久化 |
| 执行记录 | steps、tool_invocations、operations、approvals | 由程序记录，不能让模型修改；用于恢复和事实核验 |
| 跨会话长期记忆 | 本期不实现 | 不建立用户画像向量库，不自动保存推断出的偏好 |

政策知识库属于外部知识，不是用户记忆。订单快照是某个时点的观察，不是永久事实。指令优先级与事实可信度分开：系统权限不能被文本覆盖；业务事实以授权服务的当前记录为准；用户陈述可以触发调查，但不能直接改写已查证状态。

### 2.1 工作记忆 schema

- TaskGoal：goal_id、intent、mode、revision、target_refs、status、required_slots、proposal_refs、operation_refs、result_ref。
- SlotValue：name、value、source_type(user/tool)、source_ref、observed_at、verification_state(asserted/verified/conflicting)。模型推断的值只能是候选，不能标 verified。
- EntityCandidate：entity_type、opaque_id、safe_label、source_tool_call_id；必须来自授权查询结果。用户手写的 ID 也需要归属校验。
- PendingQuestion：question_id、wait_generation、goal_ids、missing_slots、候选选项引用；用户回复必须绑定等待代次。
- TaskGoal status：UNRESOLVED、READY、WAITING_INPUT、WAITING_APPROVAL、EXECUTING、COMPLETED、DECLINED、WITHDRAWN、ESCALATED、FAILED。由程序校验迁移，模型仅提议更新。

目标 revision 变化时，旧提案必须失效；过去成功的 operation 不删除也不改写。目标数量有可配置上限，超过上限向用户说明并分批处理，不静默丢掉后半段请求。

### 2.2 ContextBuilder 预算与压缩

组装顺序：固定规范/权限 → 当前目标与待处理动作 → 必要最近消息 → 有来源的历史摘要 → 相关订单观察 → 检索证据/工具摘要。必须保留工具调用和结果配对，不随意截断协议消息。

输入预算按实际模型上下文上限扣除输出预留与安全余量计算；实现 tokenizer/估算器并记录估算误差。先移除无关历史和重复证据，再压缩长观察，最后才概括较旧会话。必需状态装不下时补问/分任务，不删除审批参数来凑长度。

不可仅靠摘要保存：order_id/item_id、goal revision、金额币种、quantity、policy/order version、proposal/operation_id、审批状态、未解决问题。以上从结构化状态单独注入；摘要中相冲突的说法不能覆盖它们。

摘要可以由同一 Gateway 的辅助调用生成，但没有工具权限、不形成第二个自主 Agent；计入调用/费用预算，带版本保存。摘要失败时退化为有界最近历史＋结构化状态，不能阻断已提交业务结果回查。压缩是否有效通过“压缩前后目标/槽位/审批信息一致”回归测试验证。

本地演示原文和摘要保留期限配置化；过期前清理不能删除恢复所需引用。在途 run 引用的记录要保留；终态清理需同步处理消息、摘要、artifact 和派生索引。日志默认脱敏，不为了 memory 保存完整地址/支付信息。

## 3. 意图识别不是关键词路由，也不是授权判定

采用：结构化模型解析 → 服务端校验 → 授权实体解析 → 槽位补齐 → 规则核验。初版将解析作为主 Agent 的首个决策阶段，不增加独立分类模型/Agent；每次用户补充或修改时重新评估相关目标。

Intent 类型：ORDER_QUERY、SHIPMENT_QUERY、POLICY_QUERY、RETURN_REQUEST、CANCEL_ORDER、REPLACEMENT_SEARCH、COMPARE_PRODUCTS、HUMAN_ESCALATION、OUT_OF_SCOPE、AMBIGUOUS。模式 mode=INQUIRE/REQUEST_ACTION，避免把“取消有什么影响”理解成“立即取消”。换货执行若尚未实现，应明确不支持，不能由 REPLACEMENT_SEARCH 偷渡实现。

取消 run、approve/reject proposal 是明确 API 控制事件，不由普通分类器直接执行。聊天中的“好的”只可产生待解析答复；批准仍要求绑定具体 proposal 的结构化确认。多提案不明确批准范围时须补问。

### 3.1 IntentFrame 合约

字段：schema_version、source_message_ids、items[]、unresolved_references[]。每个 item 包含 intent、mode、target_mentions、slot_candidates、ambiguity_reason、missing_slots、relation_to_goal(new/refine/withdraw/replace)、existing_goal_id（可空）。

所有 enum/大小/字段由 Pydantic 校验。source_message_ids 必须来自当前授权对话，existing_goal_id 必须属于当前 run。拒绝模型制造的 owner_id、审批结果、verified 状态和任意数据库 ID；格式错误有限修复，失败则 AskUser/明确报错，不直接执行。

不以 LLM 自报的“0.95 confidence”作为安全阈值。是否补问由可判定条件决定：多候选、缺必填字段、前后矛盾、目标版本变化、涉及写动作。未来若加入概率分类器，阈值需开发集校准并保留未知类别。

### 3.2 例子

输入：“耳机昨天收到，一边没声音，帮我退了；另一个没发货的订单取消。”

解析出 RETURN_REQUEST 和 CANCEL_ORDER 两个目标；“昨天收到”为 asserted 槽位，“耳机/另一个”为待解析指代。调用授权订单查询而不是生成订单 ID。多个耳机订单则 AskUser；唯一候选仍核验归属、数量、状态和签收时间。缺失数量不能默认全部退货。

查到第二笔已发货时，保留用户原始陈述与实际观察的差异，说明不能按未发货取消；不通过“模型认为可以”覆盖业务规则。每个目标独立形成提案、等待批准和结果。

### 3.3 槽位和实体解析

required_slots 按工具/业务 schema 声明，而非由模型任意定义。退货至少明确 order、item、quantity、必要原因；取消明确 order；政策咨询不强制索取无关订单或个人信息。

EntityResolver 只在服务端授权候选集内解析指代。匹配唯一不等于批准动作。找不到候选则解释/补问；多个候选显示最小必要标签。不在日志中暴露其他用户对象存在与否。

模型 slot_candidates 经应用层映射成为 asserted 值；工具返回值由代码标为 verified。冲突进入 conflicting 并要求规则判定或补问。每个槽位带 provenance，便于回看“为什么选了这个订单”。

### 3.4 意图变更和并发语义

- WAITING_USER/WAITING_APPROVAL：允许用户显式修改目标；事务中递增 goal revision、使受影响旧提案失效、保存新输入和恢复 generation。
- RUNNING：本版不做实时 steering。修改请求返回忙碌提示，用户可以取消当前 run 并等待确认后创建新 run；禁止 silently 拼接到执行中的上下文。
- 已有 operation 提交：撤回不能删除历史效果；创建后续目标前查真实状态并说明限制。
- 同一消息重复提交按 client_message_id/幂等 key 去重，不重复创建目标。目标语义去重仅辅助提示，不能把相似但合法的第二次部分退货永久屏蔽。

修改接口：POST /v1/runs/{id}/revisions，携带 expected_state_version、wait_generation、text、client_message_id、Idempotency-Key。仅接受等待态；与批准竞态时锁 run/相关 proposal 并检查版本，只有合法的一方推进，另一方返回 409。与协议 13 的固定锁顺序保持一致。

## 4. Loop 的输入、输出与控制权

单轮流程：恢复持久状态 → ContextBuilder → ModelGateway → DecisionValidator → ToolRouter/AskUser/Proposal → 保存观察 → CompletionGuard/ProgressMonitor → 下一步。

AgentDecision 延续 ToolCalls、AskUser、ProposeAction、FinalAnswer 四种；IntentFrame/goal_updates 作为相应决策的受校验字段，不引入第二套独立循环。模型不能直接返回“把 run.status 改为 SUCCEEDED”。FinalAnswer 是候选回答，必须经 CompletionGuard 决定是否允许结束。

持久化和工具执行顺序严格遵守 13 文档。完成/停止守卫在模型请求前、工具执行前、工具结果后均检查相应条件；预算耗尽后仍必须允许不需模型的结果回查/记录流程，以免将已提交操作丢成未知。

## 5. CompletionGuard：结束的是执行，不等于业务目标都成功

增加两个正交字段，澄清主规格旧状态命名：

- run.status 保持 QUEUED/RUNNING/WAITING_USER/WAITING_APPROVAL/SUCCEEDED/FAILED/CANCELLED/EXPIRED。
- run.outcome：FULFILLED、PARTIAL、DECLINED、ESCALATED、UNRESOLVED；非终态可为空。stop_reason 单独记录（目标完成、预算、无进展、不可恢复错误等）。

SUCCEEDED 表示处理流程正常收束，不自动等于所有用户目标达成。例如符合政策的明确拒绝可 status=SUCCEEDED/outcome=DECLINED；部分完成后用户不再继续可 PARTIAL。评测根据场景目标判断，不能直接把 status=SUCCEEDED 当任务成功。

### 5.1 正常完成判据

1. 每个目标有明确的 disposition，且不存在未处理工具、未解决 UNKNOWN 写入或仍需处理的审批。
2. 已执行操作有稳定回执，并完成所需的业务结果核验；不能只凭自然语言 success。
3. 问答包含必要且适用的证据引用；引用存在不自动证明语义支持，离线评测还需支持关系判定。
4. 最终回答与结构化结果一致；确定性的金额、订单、状态字段从服务端结果渲染，不让模型自由改写。
5. 剩余事项如主动撤回、合法拒绝或明确升级已记录；不能悄悄丢目标。

如果候选 FinalAnswer 过早，返回结构化 completion_errors 进入观察，只允许有限修正；仍不满足则明确未完成退出。无模型可用时用模板展示已核实结果和未完成事项，不捏造流畅总结。

### 5.2 暂停与失败

缺必要信息 → WAITING_USER；待批准 → WAITING_APPROVAL，保存状态并释放 Worker。人工等待不占活跃循环，不用 while 轮询用户。

取消/过期按原状态处理，仍记录已发生副作用。预算耗尽、无进展、模型协议持续错误 → FAILED，并按实际完成情况记录 PARTIAL 或 UNRESOLVED。外部写入 UNKNOWN → 停止自动重发、记录操作未知和人工核对需求；可以 FAILED/outcome=ESCALATED，但不得伪称人工工单已经创建，除非有真实工单回执。

## 6. ProgressMonitor：检测无效循环而不是禁止合法重复

进展信号由程序从持久记录提取：新增已解析实体/必填槽位、新的相关证据引用、目标状态推进、提案/operation 状态变化、明确消除一个歧义。token 增长、计划改写或模型自称“有进展”不算。

动作 fingerprint：action_kind + canonical_args_hash + goal_revision + relevant_observation_version。不要用自然语言输出全文哈希判断进展：措辞变化不意味着新信息。

初始策略（均需配置与测试）：

- 同 fingerprint 连续两次得到等价结果且无目标进展，阻止第三次原样执行；错误重试按独立 retry policy 计数，不另开无限额度。
- 最多允许一次有明确变化的恢复策略，例如补问、改变查询条件、查状态而非重发写入；仍无进展则退出/升级。
- 合法 get_operation_status/job status 轮询使用独立最小间隔、最大次数和截止时间；状态仍 pending 不当作新进展，也不能按通用重复规则立刻误杀。
- 查询改写的文字不同不能自动获得新预算；同一目标累计检索改写上限沿用主规格。

ProgressSnapshot 写入 checkpoint，重启不清零计数。恢复策略仍通过统一工具权限与审批，不能以“避免循环”为理由跳过校验。记录 loop_stop 事件包含具体原因，便于失败分析。

## 7. 数据/API 补充

新增或等价建模：task_goals(run_id,goal_id,revision,status,intent,mode)、goal_slots(provenance/verification)、conversation_summaries(coverage/version)、intent_frames(source refs/schema version)、progress_snapshots。关键更新与 checkpoint/事件同事务推进。

GET /v1/runs/{id} 返回安全的 goals[]、outcome、stop_reason、pending_question/proposals；不暴露内部 prompt 或私有思维链。前端显示逐目标处理结果，不只展示一个绿色成功标记。

工具与 memory 的敏感文本只以授权引用进入上下文；删除/保留策略不允许产生跨 owner 召回。最终日志导出只包含允许公开的结构化决策与工具轨迹，不要求模型生成思维链。

## 8. 验收矩阵

| ID | 场景 | 核心断言 |
|---|---|---|
| L01 | “退货条件是什么”与“退这件” | INQUIRE/REQUEST_ACTION 区分；前者不建执行提案 |
| L02 | 同一句两个目标 | 两目标均保留、独立结果，无静默遗漏 |
| L03 | “那个耳机”有两个候选 | 补问，不猜 ID |
| L04 | 用户给出他人订单 ID | 拒绝越权；不泄漏实体存在 |
| L05 | 缺退货数量、用户陈述与订单冲突 | 不默认全退；asserted/verified/conflicting 可追溯 |
| L06 | 两个待批提案，用户说“好的” | 不自动批准两笔；要求明确绑定 |
| L07 | 等待时改为换货，与批准同时到达 | goal revision 和旧提案失效原子化；冲突 409 |
| L08 | RUNNING 时修改意图 | 不实时偷偷 steering；明确取消/等待路径 |
| L09 | 已取消订单再说“别取消了” | 不抹掉已提交效果；说明实际状态 |
| L10 | 长历史压缩及摘要故意写错金额 | 结构化金额/授权不被摘要覆盖；来源可定位 |
| L11 | 用户 A/B 相似问题 | 不混入其他 owner 的消息/摘要/artifact |
| L12 | 模型提前 FinalAnswer 或虚构已退款 | Guard 拒绝；状态取回执，不取模型话术 |
| L13 | 单目标完成、另一目标待确认 | 不宣布全部达成；保持正确等待/部分结果 |
| L14 | 相同工具空转及表面改写搜索 | 有界停机；重启后重复计数不重置 |
| L15 | 合法状态轮询 | 不立即误杀，也不无限执行 |
| L16 | 暂停/预算耗尽/用户取消 | 暂停释放 Worker；预算持久；已完成操作仍可报告 |
| L17 | 合法拒绝与所有任务都转人工 | outcome 与任务评测区分，不能全算自动成功 |
| L18 | 模型服务不可用但业务已提交 | 模板返回核实结果，不能丢失已完成动作 |

确定性契约测试用脚本模型；真实模型意图集单独评估多意图覆盖、实体准确、槽位完整性、必要澄清率、误执行请求率和目标变更处理。按意图/模板分层切分，不用同一模板换商品名跨训练/测试；不将规则型 mock 通过率称作真实模型准确率。

M2 覆盖意图/memory/guard 的单元契约，M3 补等待态并发与断点恢复集成，M6 报告真实模型错误案例。将这些项加入 CI 和发布检查，不只留下文档。
