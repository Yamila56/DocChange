# ShopResolve 进度与续接记录

2026-10-28 新对话交接入口：`docs/NEXT_CHAT_HANDOFF.md`，包括当前项目、需求、验收边界及后训练/申请研究的候选方向。未确定的研究题目不应视为已经开工。

更新：2026-10-27。仓库：D:\证书及申请资料\ShopResolve。只允许在此仓库工作。

## 当前结论（替代旧的阶段性记录）

本地开发版可运行，未完成全部规格、未上线、未创建远程仓库。自写单 Agent Runtime/Harness + FastAPI + MySQL + Redis + Docker 沙箱 + React，没有切换框架。所有订单/退款均为合成模拟数据。

最新完整设计—代码对照见 docs/implementation-audit-2026-10-27.md，含 R01–R18、L01–L18 的证据与缺口。不能把部分覆盖说成全通过，也不要随意估算完成百分比。

本轮业务正确性补强与最终复测已完成，达到受控三场景真实模型联调准入。
详情见 docs/evaluation/live-eval-readiness.md；不代表全项目发布验收或正式冻结集成绩。
下一步请用户只在本地配置API key并确认预估预算（建议2元、最多12次调用），不得索取聊天密钥。

## 已实现与待补

- 业务：取消/退货、审批、操作幂等、短事务、整数金额；运行绑定的提案执行会检查 lease/fencing。没有任意公开 execute HTTP 入口。
- 执行：数据库 checkpoint、outbox、Redis Streams、消息回收、数据库 reconciler、等待/恢复、SSE seq 续传；同一会话一个活动 run。真实事务前/后进程硬退出测试已有证据。
- Memory：messages + 带来源 goals/slots + observations/results + checkpoint；extractive 摘要带消息覆盖、来源/文本哈希和版本，随 checkpoint 保存。TTL/删除策略仍缺；本期不做跨会话长期画像。
- 实体与能力：新增 resolve_order 授权词法候选/补问/明确序号选单；calculate_eligibility 只读资格咨询；统一 capabilities 明确拒绝未实现意图。复杂语义/多目标回复绑定未完成，不把 demo 当 LLM。
- RAG：BM25/dense/RRF/量化重排/父节/owner+version；新增逐目标条款覆盖与引用绑定、一次初检+最多两次补检索的持久预算、动作前适用版本核验。缺完整适用条件、索引原子发布和逐声明语义支持评测。
- 取消/收尾：新增权威 operation 回执合并，修复“业务已提交但 loop 尚未存结果时取消，快照看不到业务结果”的缺口；不宣称取消回滚已提交动作。全部目标核实后可不再调用模型直接经 Guard 模板收尾。
- 沙箱：独立受认证 manager、owner CSV、真实 Docker 无网络/非 root/资源约束/安全收集；工作台独立模块。尚未接成 Agent 工具，资源/重启/孤儿清理矩阵未完成。
- 前端/CI：React 构建与 HTTP smoke 已验证；没有真实浏览器交互验收，CI 未远程运行。Compose 只有基础设施；没有完整应用容器编排或公网部署。
- 待优先补：用户级限流/队列背压、provider 熔断、剩余故障与控制测试、CSV Agent 工具、物流工单和商品库存替代业务。备份恢复演练尚未做。

## 本次 DeepSeek 接入补充

用户只有官方 API 余额，三四月充过25元，不确定剩余金额。充值不是绑定某个旧模型。无需先充值；账户实际余额仍需本地 key 后只读核验。

已核验官方当前 deepseek-flash 和人民币价（高峰缓存未命中输入2元/百万、输出8元/百万；可能变化）。
本次补齐专用官方 HTTPS 端点、显式非思考、输入字节/输出 token 上限、空/截断 JSON 拒绝、HTTP 连接池与退出清理。
CLI 增加有限 grace 退出；政策检索在 CPU 计算前释放 SQL session。
scripts/check_deepseek.py 仅查余额/模型，不调用 completion。
scripts/pilot_deepseek.py 默认 dry-run；显式 --execute 加本地付费 opt-in 才能调用。
试跑3个独立合成场景，最多12次请求、建议2元预估预算；逐请求费用预留持久化到 ignored SQLite ledger，未知响应不退预留。
该预估不是官方账户硬扣费上限；新批次不覆盖原证据。详情 docs/evaluation/deepseek-pilot-plan.md。

当前没有真实模型 key，没有付费调用，不得标真实模型成功率。不要在聊天里索取密钥或打印 .env。

## 最新验证证据

- 本轮最终 docs/evaluation/mysql-business-readiness-v2.xml：153 passed，284.44秒，无失败/错误/跳过。主集成使用专用MySQL；workflow评测器回归各自用SQLite，并含Redis/Docker测试，不是153个LLM任务。
- 第一套 docs/evaluation/mysql-business-readiness-v1.xml：148 passed，251.25秒；其后新增资格咨询/严格政策主题检查，以v2为最终报告。
- 本轮针对业务正确性19 passed；摘要2 passed；网关/摘要/试跑预算组合21 passed。此前20条 workflow 数据现在也加入 pytest 工程门禁，不算真实模型成绩。
- docs/evaluation/business-focused-v2.xml：业务正确性/摘要/既有workflow组合41 passed，52.35秒；不与全套测试相加。最终全套之后实际HTTP smoke再次通过；本地key仍未配置、付费开关false。
- 本轮新代码实际 Agent HTTP smoke、免费 pilot dry-run、Ruff check/format check 通过；未付费调用。真实 MySQL 竞态和模型响应落盘后子进程强退见本轮完整报告。
- docs/evaluation/mysql-deepseek-preflight.xml：本次真实 MySQL 全套 106 passed，163.44秒，包含 Redis、Docker 和真实退出用例，无 skipped。随后新增的 pilot 客观 grader 单测另跑4 passed，不能把报告冒充已含该新增测试。
- 本次网关/预算新增集：18 passed；随后扩充 pilot grader 后 test_pilot_budget.py 为4 passed。
- docs/evaluation/local-deepseek-preflight.xml：后续本地全套101 passed / 6 skipped，64.03秒；与 MySQL 报告范围不同，不相加。退出时停止领取新工作的补丁后，队列/网关/预算组合21 passed。
- Ruff check 与 format check 通过（最终提交前再检查）。
- scripts/pilot_deepseek.py 默认 dry-run 实际运行通过，没有 API 请求。
- 上一阶段 docs/evaluation/mysql-final-pass.xml：88 passed；mysql-regression-latest.xml：87 passed。历史失败文件保留，不当作最新结果。
- 上一阶段真实 HTTP Agent/Redis/SSE smoke、上传→manager→Docker 44800分计算 smoke 均通过；临时进程均已终止。
- 本次 CLI/连接池修改后再次运行 scripts/smoke_agent_http.py 通过：API/outbox/Redis/worker/订单核验/SSE/静态工作台；临时进程已结束。
- 真实浏览器 QA、付费 LLM、公开基准均未运行。

既有检索实验：16条合成冻结草案 + 8 dev；BM25 Recall@3=93.75%，hybrid+rerank=100%；Recall@1仅多1题（+6.25个百分点）；重排CPU P50约1343ms。标签由助手构造，待独立审阅，不能声称普遍提升。
既有20条 workflow 回归20/20、0额外账本案例，控制器为 deterministic demo，不是LLM成功率。
pilot 新增金额/目标订单/审批/operation和其他用户哨兵检查；正式冻结集、语言证据支持和更多trial仍待补。

## 环境、数据与 GitHub

项目专用 .venv，Python3.12.13；不改全局 Python/Conda。MySQL8.4.6 在127.0.0.1:13316，Redis7.4.5在127.0.0.1:16379。
shopresolve 为合成演示库；shopresolve_test 为唯一允许重建的测试库。不得清理其他数据库、Docker容器或卷。
沙箱镜像 shopresolve-sandbox:v1 已构建。Compose MySQL/Redis 保留；没有常驻 API/Worker/manager。
.env、.venv、.cache、模型缓存、node_modules、dist均 ignored，不提交。上层是证件/申请材料，绝不可 git add 上层。
GitHub 先私有，检查后才能公开。账号 Yamila56，远程仓库未创建/推送。已完成本地提交历史保留，不覆盖。

## 续接与命令

先读本文件、AGENTS.md，再完整读 docs/design/10、11、13、14；以当前 diff/报告核实，不把设计文档开头的“待实现”当当前实现状态。
文档本身不是完成证据。

- .venv\Scripts\python.exe -m ruff check .
- .venv\Scripts\python.exe -m ruff format --check .
- .venv\Scripts\python.exe -m pytest -q
- .venv\Scripts\python.exe scripts/test_mysql.py --junitxml=新的报告路径
- .venv\Scripts\python.exe scripts/smoke_agent_http.py
- .venv\Scripts\python.exe scripts/smoke_sandbox_http.py

用户要求持续推进，不因里程碑自然停工；本轮配额已重置，不沿用旧记录“98%用完”。不要自动购买额度或消耗重置权益。
真实评测试跑需要用户本地配置密钥及确认费用；这不阻止继续无密钥工程工作。
