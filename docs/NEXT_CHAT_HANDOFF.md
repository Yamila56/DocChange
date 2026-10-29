# 新对话交接：ShopResolve、实习项目与申请研究

2026-10-29 最新交付入口：主项目已改为DocChange文档工作台，使用说明 `docs/DOCCHANGE.md`，验收报告 `docs/evaluation/docchange-delivery-2026-10-29.md`，最新状态以PROGRESS顶部为准。真实Flash已做四类任务验收及针对协议修复的复测，14请求，保守估算0.111042元；原始失败不合并成冻结4/4。文档应用在127.0.0.1:8011，本地免费演示；`--remote`显式开启独立预算。第二项目WorkflowRepair已克隆并有CPU原型，训练尚未开始。不要继续扩电商或沿用下方旧预算/状态。

2026-10-28 后续实测更新：优先读 `docs/evaluation/integration-and-live-eval-2026-10-28.md` 和PROGRESS最上方新记录。Docker已用保留式运行目录改名恢复；密钥已本地配置；已授权并实际执行72次Flash生成，全部有usage，原12+追加60请求额度已用满。下文“尚未付费/无key/无浏览器QA”等是交接时旧状态，已被本轮报告部分取代。模型结果来自多轮开发修复，正式冻结benchmark仍未完成。当前有未提交变更，不可丢弃。

整理时间：2026-10-28（北京时间）。本文件压缩旧长对话，不意味着新对话自动拥有全部历史。
旧对话ID：019fbd35-dbe8-7fd0-9160-fe9b9d542741，标题“查找AI学习路线”。
需追溯遗漏时可用 read_thread 分页读取旧对话；不要为普通续建重新加载全部历史。

## 1. 先读与工作边界

实际代码仓库：`D:\证书及申请资料\ShopResolve`。执行命令必须显式以此为 workdir。
父文件夹含私人申请材料，不扫描、上传或 git add 父目录。本项目也不是 D:\新建文件夹 下的旧项目。

阅读顺序：AGENTS.md → PROGRESS.md → 本文件 → docs/evaluation/live-eval-readiness.md。
架构/控制变更前完整读 docs/design 下 10、11、13、14 四份规范；按其中要求做恢复与业务验收。
旧规范开头“待实现”是设计时状态，不是当前进度；以实际代码、最新报告和 PROGRESS 为准。
尚未完整验收不意味着需要重写已有项目。

不要读取或展示 .env 内容。密钥只由用户本地填写，程序读取即可，日志和聊天只报告是否已配置。
不改现有 Conda/全局 Python，不清理其他数据库、容器或卷，不执行危险 Git 重置。
本对话当前任务是交接，不是授权付费评测、购买额度、公开部署或公开 GitHub。

## 2. 用户目标与协作要求

- 金融工程背景转 CS/AI，正在准备27Fall硕士申请，同时申请实习；希望兼顾硕士就业与后续科研/博士。
- 当前优先做一份有业务和工程说服力的 Agent 开发简历，再配一个较短的后训练项目；申请研究另做，不能混称全部已完成。
- 用户曾做 Agent 相关科研助手，但自述没有论文成果，希望形成可信的独立研究、公开代码、技术报告，可能再发展为poster/论文。
- 不要求所有项目共用业务。可以分别有 Agent 开发、后训练/算法、搜广推备用简历，并为申请选择最好、最可信的经历。
- 偏非金融项目；不要又换回 FailureLens/金融主题。曾讨论 DeepTrace/FinTrace，但当前已选电商售后 ShopResolve。
- 时间有限、希望尽快形成可投递成果；租卡预算尚未确定，应提供分档方案，通常最多考虑几张4090，不默认H100资源。
- 希望不仅生成代码，还要理解架构/实现/失败案例，后续可以通过面试式追问帮助掌握。可以借助AI协作，但不能编造独立贡献、历史日期、真实用户量、线上收益或论文成果。
- 用户希望持续推进可做的工作，配额不足时保存进度；不要每到一个小里程碑就无故停工。但付费、密钥和真正的新范围仍需明确确认。
- 未经用户明确要求，不启动 sub-agent。此次用户选择新对话续接，保留同一代码，不需要 fork/worktree 或另一份实现。

## 3. ShopResolve 的确定定位与架构

不是购物搜索/推荐Agent，而是模拟电商售后与订单异常办理：
自然语言诉求 → 查本人订单事实 → 查适用政策 → 补信息 → 提案 → 明确批准 → 受控模拟执行 → 回读凭据。

核心已做：查单、政策咨询、取消待发货订单及模拟退款、创建退货申请（不代表退款已到账）。
所有用户/订单/政策/退款是合成数据，不接真实支付、不声称真实企业落地。

选型已明确：FastAPI + Pydantic + 自写单 Agent Runtime/Harness + MySQL + Redis Streams + React + Docker。
参考 Pi、mini/SWE-agent 的分层与工具边界、Claude Code公开权限/沙箱思路；不是复刻其私有源码。
不要擅自换 LangGraph/LangChain/Pydantic AI 的第二套循环，也不要为了名字增加 Multi-Agent。

主路径：React → FastAPI → MySQL/Outbox → Dispatcher → Redis Streams → Worker → Runtime。
Runtime 使用 ContextBuilder、ModelGateway、ToolRouter、CompletionGuard、ProgressMonitor 和 RunStore。
CommerceService 是确定性规则/事务边界。RAG只提供解释依据，不产生订单事实或执行权限。
通知至少一次；本地操作通过稳定operation_id、唯一回执、事务和fencing防重复，不声称全系统exactly-once。

## 4. 已完成的工程与最近一次补强

最新本地提交：`e962edd`（此前 `2fd02f7`），工作树在交接开始时干净。

- 类型化 IntentFrame/Decision、多目标工作状态、槽位来源、补问和审批等待/恢复。
- 自写循环、模型响应先落盘再执行、预算持久化、无进展守卫、服务端事实渲染。
- 授权词法 resolve_order：候选多/空/截断就补问；支持明确序号选单；“好的”不等于选择或批准。
- calculate_eligibility：具体订单资格咨询必须只读核规则，不创建提案/批准。退货探测按单件，不是最终金额或用户已选数量。
- capabilities 清单：未实现的换货、商品比较、人工工单明确拒绝；多目标独立处置。
- 逐目标条款覆盖/引用绑定；每目标初检+最多两次补检索，先持久化预留；动作前核验检索政策是否仍活跃。
  这是有限领域 scope/coverage，不是通用语义蕴含判断，也不等于真实问答准确率。
- MySQL短事务、请求幂等、操作回执、审批绑定、版本检查、金额整数分、部分退货数量约束。
- Redis Streams、outbox、reconciler、租约/心跳/fencing、checkpoint、取消、SSE持久seq续传。
- 修复关键竞态：业务已提交但loop尚未存结果时取消，收尾回查权威回执，保留已提交结果，不谎称取消回滚了业务。
- 已核实目标可以经Guard直接模板收尾；模型故障不应遮蔽已提交业务，不强制再付费生成总结。
- extractive摘要有来源消息/来源哈希/文本哈希/版本，随checkpoint保存；关键业务字段不靠摘要保存。
- RAG底座：BM25/中文BGE向量/RRF/量化BGE重排/父子片段、owner与policy-version过滤。
- 独立Sandbox Manager、owner CSV上传、无网络非root Docker、资源/日志限制、安全输出收集；React有独立CSV面板。
- DeepSeek官方HTTPS JSON适配器、显式非思考、输出/输入限制、空/截断响应拒绝、HTTP连接池、有限退出grace。

## 5. 真实验证证据（不能包装成模型得分）

- `docs/evaluation/mysql-business-readiness-v2.xml`：153 passed，284.44秒，0失败/错误/跳过。
  主业务/竞态/故障测试使用专用MySQL；workflow回归各自用SQLite；包含Redis与Docker集成。
  不是153个真实模型任务，也不保证全部R01–R18/L01–L18均覆盖。
- `docs/evaluation/business-focused-v2.xml`：41 passed，52.35秒，和全套重叠，不能相加。
- 实际 HTTP smoke：API/outbox/Redis/worker/订单核验/SSE/静态工作台通过。
- 真实子进程强退：业务事务前/后，以及模型完整决策落盘后恢复复用原工具请求。
- MySQL批准/修改竞态、取消/执行竞态与提交后取消保留回执已测试。
- Ruff check、format check、免费pilot dry-run通过。React之前构建通过，但没有浏览器交互/视觉验收。
- 检索16条合成冻结草案+8 dev：BM25 Recall@3=93.75%、hybrid+rerank=100%；
  Recall@1仅多1题（+6.25个百分点），CPU重排P50约1343ms。标签由助手构造，待独立审阅。
- 20条workflow是deterministic demo工程回归，不是LLM成功率。历史报告保留，以最新指定报告为准。

## 6. 当前可以做什么评测、还需要用户提供什么

已达到“受控本地三场景真实模型联调”的工程门槛，不是全量最终评测/生产发布完成。
最后核验时：本地未配置模型key、paid opt-in=false，至今未调用付费模型。

用户只有官方DeepSeek API余额，约三四月充过25元、不确定剩余。无需先充值；充值是账户余额，不绑定旧模型。
计划首个模型是官方 deepseek-flash，非思考模式；模型名/可用性/价格需执行当天再次核验，不能照抄旧价。
首轮建议最多12次物理请求、2元预估预算。用户尚未确认此付费授权。
必须让用户在本地.env填写 SHOPRESOLVE_MODEL_API_KEY，聊天只回复“已配置”并确认预算，不接收明文key。
配置provider/model/base_url/opt-in时保留原数据库/沙箱等秘密，不覆盖整个.env。

入口：
- `.venv\Scripts\python.exe scripts/check_deepseek.py`：只读余额/模型预检，不请求completion。
- `.venv\Scripts\python.exe scripts/pilot_deepseek.py`：默认免费dry-run。
- 确认授权后才运行 `scripts/pilot_deepseek.py --execute --max-calls 12 --max-cost-cny 2`。

数据：evals/data/deepseek-pilot-v1.json，查单、取消政策咨询、批准后模拟取消三个场景。
每场景独立临时SQLite+固定业务clock；预期答案不进Agent上下文。
评分检查状态/金额/批准/操作数/他人订单哨兵，语义支持仍需人审。
尝试费用预留写 ignored SQLite ledger，失败/未知响应不退预留；本地估算不是供应商账户扣费硬上限。
报告默认 ignored .cache/evals，不自动发布轨迹。更多见 deepseek-pilot-plan.md、live-eval-readiness.md。

正式评测：自建多轮业务集为主、故障安全集和RAG集分开，之后固定公开retail benchmark版本作补充。
按场景/政策模板切开发与冻结集；同模型同工具比较固定流程与Agent；重复trial，报告失败、分母、置信区间、时延/费用。
旧tau-bench题库有过时提示；维护仓库为 https://github.com/sierra-research/tau2-bench 。版本执行时核验。
改编工具/政策的数据不能称官方榜单成绩；不得只跑别人的默认Agent然后当成本项目成绩。

## 7. 尚未完成，禁止被新对话误报为完成

- 全面用户限流、队列背压、provider熔断与分布式并发预算。
- CSV尚未接入Agent自动工具；仍是独立工作台能力；沙箱OOM/PID/孤儿TTL等验收不完整。
- 物流人工工单、完整物流事件、商品库存与替代品业务未实现；暂不支持必须明确说明。
- 自然语言消歧仅有限词法；复杂多目标回复绑定、变更体验、合法状态轮询与全部恢复组合仍需扩展。
- Memory完整TTL/删除、跨run/跨会话策略未齐，本期不做长期画像。
- RAG多地区/品类/生效时段、索引原子发布、逐声明语义支持和完整基线/消融未齐。
- 完整应用容器编排、真实浏览器QA、备份恢复、远程CI、生产认证/TLS、公网上线未做。
- 真实LLM评测、公开基准、独立人审标签、正式冻结集/重复实验未做。

## 8. 环境与复现

项目专用.venv，Python3.12；MySQL8.4.6在127.0.0.1:13316，Redis7.4.5在127.0.0.1:16379。
shopresolve是合成演示库；shopresolve_test是唯一允许测试重建的数据库。保留既有容器/卷。
沙箱镜像shopresolve-sandbox:v1。上轮没有常驻API/Worker/manager，新会话先检查实际进程，不猜当前状态。
.env/.venv/.cache/模型缓存/node_modules/dist均ignored；不打印凭证或加入Git。

常用命令（均在仓库内）：
- `.venv\Scripts\python.exe -m ruff check .`
- `.venv\Scripts\python.exe -m ruff format --check .`
- `.venv\Scripts\python.exe -m pytest -q`
- `.venv\Scripts\python.exe scripts/test_mysql.py --junitxml=新的报告路径`
- `.venv\Scripts\python.exe scripts/smoke_agent_http.py`
- `.venv\Scripts\python.exe scripts/smoke_sandbox_http.py`

GitHub：用户要求先私有、检查后再公开。账号Yamila56，尚未创建远程仓库或push。
用户之前授权完成后帮助私有推送，但不能以此授权现在公开仓库或上传父目录。

## 9. 后续项目/研究：已表达的需求，不是已经确定或完成的选题

这部分旧对话中有大量探索，但本仓库没有研究立项/实验/代码证据。
不可凭记忆补出“已选定的两个具体idea”“已跑的研究结果”或把建议写成已发生经历。
如果需要此前助手推荐方案的精确名称，先分页读取旧对话对应段，而不是编造。

### A. 实习用短后训练项目（独立于ShopResolve）

- 用户希望约一周可做初版，训练/租卡成本可控；不能只跑通教程，必须说明解决的问题、基线、改进与评测。
- 探索过DeepSearch-RL、搜索/工具使用、多轮长程Agent训练、GRPO类训练。
- 用户提过Hands-on Modern RL（形容像新一代medical GPT）、agentic-grpo-LongHorizon；精确仓库链接未在当前可见记录确认，不猜链接或假装读过。
- 可以做小规模后训练/SFT/RL对照，但具体模型、算法、数据、奖励和预算未最终选定，没有开始训练。
- Nanochat是用户感兴趣的模型推理/后训练学习项目，曾倾向后置，不应突然要求先完整学完再做当前Agent。

### B. 申请用独立研究（大约两个，尽量不同方向）

- 和一周实习项目分开，可以有独立假设、基线、消融、失败分析、公开代码/小技术报告。
- 目标是有价值的可复现小成果；poster/论文是可能方向，不承诺顶会或任何级别录用。
- 关注Agent后训练/自进化RSI、可靠性/记忆/长程工具任务，也对多模态、VLA和具身软件侧/RL感兴趣，不走硬件主线。
- 不要求两个研究都做Agent；第二方向可以多模态/CV/具身，但并未正式选定“视觉文档”等题目，别把先前探索误当最终需求。
- 用户希望有较新的可切入问题，又担心赛道拥挤和成本；需要立项前系统查当期论文/公开复现，而不是凭“冷门”断言创新性。
- 用户给过候选参考 Open-MOPD：https://github.com/BytedTsinghua-SIA/Open-MOPD 。明确说可以借鉴，但不一定采用，也不是已经开工。
- 资源预算未知，按CPU/API、单卡4090、少量多卡分档，先做最小证伪实验，再扩大。
- 经历必须使用真实开始/完成时间；不能把现在新做的项目改写成过去科研，不能伪造RA/论文/实习。

### C. 搜广推备用方向

- 用户考虑独立备用算法简历，曾提MiniOneRec/OpenOneRec等生成式推荐项目；没有最终选型、训练或结果。
- 不要为了覆盖所有岗位同时开启多个大项目。先评估数据/算力/时限和可解释的个人贡献，再排序。

### D. 建议的推进顺序（不是锁定研究题目）

1. 先用真实模型确认ShopResolve闭环，再修失败、完成正式评测和展示/README，形成可信实习项目。
2. 另立一周后训练项目，先定问题和预算，不把它混进ShopResolve的工程范围。
3. 对两个申请研究方向做小规模文献/资源审查，选题可不同，但讲清动机和真实贡献。
4. 搜广推作为是否值得投入的备用选项，未确认前不扩张。

## 10. 新对话首轮应做什么

先读取交接和当前Git状态，简短确认已接上已有代码和研究规划。
不要重新询问已经确定的业务/技术栈，不要重新建项目，不自动启动付费或大规模测试。
用户尚未给key/预算确认：可以继续安全的无密钥工作，或等待用户在新对话指定下一步。
需要真实模型时仅说明缺本地密钥和明确预算，不索取聊天明文。
