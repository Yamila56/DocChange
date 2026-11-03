# ShopResolve 进度与续接记录

2026-11-03（用户新授权：要求真实改善并复测低分检索，进行中）：新增可选文档语义检索，保持原FastAPI/SQL/Redis/Harness结构和默认BM25。工作台可选dense/hybrid/hybrid_rerank；BGE-base-en-v1.5 FP32 ONNX、RRF k60、MiniLM-L6交叉编码器top20，模型仓库revision见`evals/document-retrieval-models.lock.json`。原owner/revision读取、精确扫描、审批发布不变；语义索引为有界进程缓存，按owner/工作区/revision/内容隔离，计算在线程内。新增检索/语义/发布相关回归17 passed、17.63秒，报告`docs/evaluation/docchange-retrieval-regression-2026-11-03.xml`。

正在跑完整SCIDOCS1000+SciFact300、BM25/dense/hybrid/hybrid_rerank四组，脚本`scripts/evaluate_document_hybrid.py`。使用原题、原文档、原qrels、原K/TREC scorer；要求BM25精确重现旧分。没有删难题、扩大K后替换同名指标或用mock冒充成绩。当前GPU批次`.cache/evals/public-ir-v03-cuda-20261103`、session23808，编码/逐题结果断点落盘，源哈希已冻结，不要在运行中改neural/retriever/service或评测器。尚无完整新成绩，不能先宣传提升。

CPU早期批次`.cache/evals/public-ir-v03-20261103`因首次编码慢主动停止，只生成少量embedding，不是质量结果；正式GPU批次在任何新质量结果产生前固定改用相同BGE基础模型的FP32导出以适配CUDA。RTX4050本机6GB可用于小模型推理，不租卡、不训练、不调用生成API。已有Conda Torch CUDA DLL仅只读使用，GPU ONNX1.23.2通过`.cache/ir-cuda-runtime`隔离覆盖导入，未改原.venv/全局环境。安装临时目录改到本仓D盘，避免C盘空间不足；模型/Xet/轮子均在ignored D盘路径。全部CPU/GPU预试不计作完整基准。待全量完成后生成配对差异/互补性、更新简历与首页摘要，保留原始低分在完整报告，再提交私有GitHub。

2026-11-03 V0.2本轮已完成并推送：代码/工件提交`c2886cd`，简历排版提交`e517753`，已推私有`Yamila56/DocChange`的main；远端再次回读PRIVATE。本轮Actions run `41576363937`对应`e517753399213ff45158873ac56b95431a51c4b2`，unit/frontend/mysql三任务均成功：unit165 passed/16 skipped、28.40秒，MySQL配置任务174 passed/7 skipped、66.40秒；不是两套成绩相加，不代表Redis或Docker文档端到端通过。Ruff与前端构建亦在同次CI通过。110份本轮文件及未推送历史共132个blob做本地凭据扫描无发现；73份Git patch逐一与JSON预测及评分hash一致，35个本地文档链接有效。旧自建评测的未跟踪草稿没有纳入本版，未删除；`.env`、缓存、下载仓库、数据库不上传。本条替代下面“待提交/待CI”状态。仅剩本记录的文档提交，不再改代码或追加模型调用。

2026-11-03（最新授权：有限修改、突出已有后端与评测，不再扩建）：保持自研单Agent/FastAPI/SQL/Redis/React架构，不启动新的完整模型批次。保留公开基线175题综合25.6094、SciFact300与SCIDOCS1000完整结果；不能把两个检索集都称为高分，不能删掉低分伪造总成绩。新增`docs/RESUME-DOCCHANGE.md`和`docs/RELEASE-v0.2.0.md`，更新README、使用说明、设计18和基线后的修正说明，以“受控文档变更工作台”定位，强调版本审核、恢复和异步任务。

已有异步能力经代码核实：任务API返回202；SQL Outbox→Redis Streams→Worker、异步模型/SQL/Redis和独立Worker心跳已存在。一进程Worker逐项处理，可多进程消费；UI当前轮询，不冒充流式UI或吞吐实测。本轮仅将工作台BM25/精确扫描在数据库会话关闭后交给`asyncio.to_thread`，保留原排序、精确扫描语义与Revision发布，不新增异步索引服务。新增并发回归验证检索线程被控制阻塞时事件循环仍可完成数据库读取。

本轮定向回归实际35 passed/2 skipped、30.26秒，JUnit `docs/evaluation/docchange-v02-final-2026-11-03.xml`；Ruff check/format通过。仓库批读、定位读取、阅读清单、草稿复核、零匹配无证据处理及预算preflight修复纳入本版；没有公开同集复测，因此没有新Agent提升数字。此前strong-v2试运行因零匹配缺失evidence_id停止，已修复且回归覆盖，不能当作完整175对照；Windows/WSL中未发现仍运行的evaluate_dogbench。Codex适配接口已联通但仅为实验入口，订阅费用未知，计划/summary已避免显示伪造0元预算。

导出器改为按预测原文写UTF-8字节，`.gitattributes`让评测patch不被换行转换；73份patch逐一与评分绑定hash核验通过。私有远端回读仍PRIVATE。实际提交与CI结果见顶部最新记录。未充值、租卡、运行新训练或追加模型评测。

2026-11-03（用户新授权继续改进，尚未交付）：旧 Flash 175题最终评分已完成并导出 `docs/evaluation/dogbench-2026-11-02/`（逐题CSV、73补丁、完整裁判、requests usage、metadata）。综合25.6094，Q16.3073，弃答recall59.6154，决策94/175，113终态（73patch40abstain）/62轮次失败；73/73原生patch可应用且未改工作树，63对应需改、57部分/全部有分、15无P0、一个rubric100仅拼写更正，不能宣传73或57个完全正确。检索SciFact300/SCIDOCS1000完整指标已在JSON、汇总CSV；人读基线报告 `docs/evaluation/docchange-public-2026-11-02.md` 已写。原始context95通过80被标记，兼容准入、17符号链接工具隔离及3旧仓库ID绑定均披露。1979 API请求+101未发API的预留，cache-aware峰值费用上界9.5917元（不是账单），接口2次另计。

本地源码已有7682fb9，仍未push；GitHub应保持PRIVATE。用户随后明确允许继续修复，不能把基线收尾当已完成新目标。当前改动增加Repository read_many最多4文件、零匹配过滤（Retriever默认不变，所以BEIR旧成绩仍有效）、剩余轮次提示、一次草稿复核；RecordedGateway增加preflight，先做费用预检再占模型轮次，修复101次空占；EvaluationBudget可选cache_hit_rate默认None兼容旧账本。相关回归最新22 passed，16.36秒；全仓Ruff还剩2处长行（repository.py:60、run_benchmark_linux.py:15）。新版Flash诊断 `.cache/evals/dogbench-v2-20261103`、`.cache/evals/dogbench-v2-final-20261103` 均已因前段重复探索/效果差而主动停止，不能报成完整175对照；第一个诊断发现预算提示放在观察前会破坏缓存，已移到最后。已发生费用各自账本保留，不得覆盖或无限新开批次。

正在接强模型的同Harness JSON模型适配（不切Agent框架）：`scripts/codex_repository_gateway.py`，通过已登录Windows Codex CLI的schema输出、禁工具/网络/只读，模型只返回决策，真正读写仍由RepositoryRuntime执行。`--subscription --model gpt-6.1-sol` 路由在evaluate_dogbench.py已加，但尚未证明联通，不得宣传GPT模型成绩/零费用（订阅额度单列）。WSL到Windows路径与环境桥接在scripts/score_dogbench.py、judge_windows_bridge.py、run_benchmark_linux.py；早期接口1–4因.EXE大小写导致未转Windows路径，5–6已进入CLI但models refresh超时，日志在ignored `.cache/codex-model-calls/*/events.jsonl`。当前接口6 session27759，原生Windows最小健康调用session18723（`.cache/native-model-health/`）；先查进程和结果，别重新调用完成项。接口调试目录与基线分开，不能算benchmark。尚需联通强模型并做有用的同公开任务验证，若可行再冻结完整175对照；完成新报告/简历/CI后再commit/push。不应再盲目扩大失败的Flash批次。

2026-11-02（进行中，替代此前未评测状态）：用户要求完成 DocChange 五项仓库适配、完整公开评测、简历与私有 GitHub 推送；不继续 Search Agent 后训练。新增 Git 仓库只读参考/分页检索读取/MDX-RST/新建删除/候选原生 patch 适配，共享原 RunStore 与补丁 guard；新增关键回归 6 passed。真实 BEIR SciFact300、SCIDOCS1000 已完成，结果在 `docs/evaluation/public-ir-2026-11-02/`，不等于编辑成功率。DoGBench 上游 commit `35bc80734093ffc3d0801476b02ceb2ab0c80bd2`、官方 dev 数据175条已下载校验；Ubuntu `.venv-bench-linux` 环境已建，密钥仍只在本地 .env。准备器保留原始 overlap finding，对精确哈希绑定的官方公开输入提供显式兼容准入，故不是原样官方 Docker CLI 榜单协议。全量准备175已尝试，158准备成功，17因原始符号链接/仓库身份不一致被拦截，未删除数据或绕过这两项。第一批 `.cache/evals/dogbench-20261102` 已有143记录、52完成、91错误（网络/轮次/总预算）；1026请求保守估算14.943616元，15元上限阻止后续调用。网络中断后不得抹除或伪装175模型成功完成。正在独立 `.cache/evals/dogbench-recovery-20261102` 恢复未完成项，额外5元上限、跨批次每题合计仍限16请求，不重复完成的语义决策。官方 Terra 裁判使用已登录 Codex CLI，原始提示/证据校验/数值计分不变，改为 read-only、禁用插件/工具、最多2次评分尝试；不要调用上游默认 bypass-sandbox。首个真实补丁已评分（0分），不是评分器未运行。尚需等恢复与全量评分、产出诚实报告/简历、完成 lint/关键回归并 commit/push；当前代码未推送，不能说本轮完成。

续跑路径：Linux准备数据在 `/home/duyumeng/.cache/docchange/dogbench-prepared`；Windows通过 `scripts/run_benchmark_linux.py -m scripts.continue_dogbench` 启动恢复。已评测生产源码在第一批 plan/source 中冻结，恢复脚本核验源哈希；运行中不要修改 documents 源码导致恢复配置漂移。Windows评分 `python -m scripts.score_dogbench --predictions 最终predictions.jsonl --output .cache/judge-dogbench`，逐条hash缓存避免重复裁判。大TREC运行文件在ignored `.cache/evals/public-ir`，不可提交。后台进程可能随网络/WSL关闭，应先查结果和账本，勿重新开始付费批次。

2026-10-29 远程CI完成：首版源码4d6c36d对应Actions run 40873084174全部成功（unit、frontend、mysql）。同一源码tag触发的重复run 40873255403已取消，保留main分支成功记录；只改交付文档的后续commit使用[skip ci]，避免重复执行。未新增业务代码、模型调用或付费训练。

2026-10-29 私有GitHub首版已上传：安装官方GitHub CLI 2.103.3（用户目录 `C:\Users\legion\AppData\Local\Programs\GitHubCLI\2.103.3\bin\gh.exe`，官方SHA256校验通过，已添加用户PATH），用户完成浏览器登录，身份回读为Yamila56。助手新建 `https://github.com/Yamila56/DocChange`，回读可见性PRIVATE，已推送main及v0.1.0（源码commit 4d6c36dc59b410e6ed369158a95f881a58e8ab6b），origin/main跟踪已设置。上传前可达历史236个blob扫描无密钥发现，父目录/.env/cache未上传。首版远程CI已触发，实际结果见Actions；不把触发当通过。此前“无法建仓/未push”状态已被本条取代。没有新增模型调用或租卡。

2026-10-29 V0.1发布准备：本轮仅整理发布与后训练完整操作流程，没有新付费模型调用/租卡。DocChange本地全套实际157 passed/16 skipped、133.25秒，报告 `docs/evaluation/docchange-release-local-2026-10-29.xml`；修复历史分析脚本5处纯格式门禁，Ruff全仓通过。README已补完整安装与V0.1说明，说明源码托管与公网部署区别。发布说明 `docs/RELEASE-v0.1.0.md`。目前仍无远程仓库；GitHub账号Yamila56连接可读，但连接器不能新建repo、本机无gh，浏览器新建页不可用，未远程push。后训练完整步骤/硬件/环境/命令/未实现边界已写入 `D:\证书及申请资料\WorkflowRepair\RUNBOOK.md`；没有开始新域训练。GPU建议为同机2×A100 80GB，先完成CPU数据/adapter再租，绝不按旧聊天价格自动购买。

2026-10-29 DocChange首版本地交付：新增 `documents/` 领域、文档工作台、revision/ChangeSet及迁移。复用原RunStore/租约/checkpoint/工具记录，Redis Worker按domain路由；本地一键入口使用SQLite+持久DB轮询，运行于127.0.0.1:8011，演示alice/docs-local-2026，未改.env。文档导入、BM25/精确扫描、单/批读取、修改diff、引用、审批拒绝/发布、旧版本访问、事件时间线、导出已实现。实际浏览器演示R1→R2发布通过。小型索引同步完整构建随revision发布，未宣称异步索引或Dense/Rerank已接好；首版单Agent。文档固定检查已接原Sandbox Manager，但本轮Docker Engine不可用，真实容器复核未跑，不借用旧CSV成绩。

本轮专项最终 `docs/evaluation/docchange-final-2026-10-29.xml`：21 passed/2 skipped，21.35秒（包括既有网关、队列测试；Redis依赖用例跳过），不是全套或MySQL并发实测。新文档迁移从旧schema的隔离SQLite验证成功；历史全链SQLite迁移受旧外键ALTER限制，未改写旧迁移。Ruff与前端构建通过。工程fixture9/9（免费控制器，不是LLM）；用户授权后真实Flash验收原4题2/4，补日志诊断两题仍失败（发现相邻JSON读取）；只读批次支持后两失败题2/2复测通过。全部原始结果与失败保留，不拼接成冻结4/4成绩。累计14次调用、含2条未知usage保留预留的保守费用0.111042元，低于原20次/0.5元上限；没有充值租卡。使用/评测入口见 `docs/DOCCHANGE.md`。后训练项目本轮不继续扩展。

2026-10-29 两项目路线已设计：用户提供“科研经历写法指南”讨论并要求克隆后训练源码、设计后训练+Agent后端两项目。本轮读取该讨论并核验上游，独立克隆 `AndrewLngdn/dsl-debug` 至 `D:\证书及申请资料\WorkflowRepair`（commit b27c282c665846e3e364411d684f900242bba044，MIT）。新目录专用venv与API工作流环境原型已建立：8项标准库测试通过，上游核心导入/生成smoke通过；未训练、未下载模型、未租卡、未做新付费调用。两项目定案见 `docs/design/16-两项目定案与实施路线-2026-10-29.md`，后训练详细方案见新目录 `LOCAL_PROJECT.md`。应用方向保持用户已选的DocChange版本化文档工作台，旧电商代码与结果保留复用；方案15中的数据工程首选建议已被此次定案取代。当前尚未实施文档域迁移，原电商中断补丁状态仍需实施前核验。新研究必须替换文本成功标记奖励来源、独立dev选checkpoint、按模板/依赖图分组测试；原作者数字不属于用户成果。

2026-10-28 选题重新设计：用户已要求停止原开发/付费评测并先比较完整应用方案。本轮新增 `docs/design/15-Agent应用重设计候选方案-2026-10-28.md`，覆盖数据工程、文档维护、售后工单、虚拟病历四案的业务、架构、Loop/Harness、单/多Agent、后端、迁移及评测；工程推荐为公开 DBT 任务支撑的数据工程工作台。该文件是候选设计，不代表选型已确认或功能已实施，不替代现行架构规范。本轮仅查阅本地文件及一手公开资料、编写设计文档，未修改应用代码、前端、.env，未执行新测试、训练或付费模型调用。新方向资源尚未下载/运行，应先做环境与数据准入；不要沿用旧批次预算或旧自建成绩作为新业务结果。此前模型对比结果已存在 `docs/evaluation/model-comparison-v2-results.json`，中断前候选补丁与测试状态仍需另行核验，不能把下方旧联调摘要当成所有后续工作的最终状态。

2026-10-28 本轮最新结论：Docker已恢复，保留运行目录备份与原MySQL卷；核心HTTP、浏览器审批/拒绝/政策、CSV沙箱及刷新恢复已实测。真实Flash共72次生成请求（原12+用户追加60，追加费用上限5元），完整usage保留；高峰且忽略缓存优惠估算0.567894元。请求额度已用满，后续不得自动继续生成。模型开发评测边测边修、全部失败保留，final-v1五项5/5、后续三项2/3、查单最后独立修复复测1/1，不能拼成冻结准确率。完整报告 `docs/evaluation/integration-and-live-eval-2026-10-28.md`。最新Runtime/费用修复MySQL全套160 passed/256.45秒；之后沙箱终态发布补丁专项7 passed/17.87秒，不相加冒充最终全套。React构建、Ruff通过。临时演示服务仍强制demo/禁止付费，`.env`密钥保留。当前代码有本轮未提交变更；没有远程push。正式冻结、多trial/基线、独立人审、公开benchmark及既有功能缺口仍待推进。

2026-10-28 后续账户预检：用户已在本地 `.env` 保存模型密钥。实际执行 `scripts/check_deepseek.py`，官方余额与模型只读接口均成功，账户可用，返回 `deepseek-flash` 和 `deepseek-v4-pro`。不记录密钥或账户余额到报告；没有调用 completion，付费开关仍为false，默认provider仍为demo。现有pilot仅支持Flash费用模型，Pro须先更新预算预留与验证。Docker启动阻塞仍待恢复。

2026-10-28 本轮验收启动：见 `docs/evaluation/local-acceptance-2026-10-28.md`。免费 pilot dry-run 通过；API/Runtime/业务/摘要/网关/预算专项 60 passed、1 skipped（MySQL锁专项），31.90秒，为SQLite回归，不替代历史MySQL全套。实际浏览器已显示前端登录页，`/`与live为200；Docker Desktop因 `sailor-ingest.sock` 访问错误无法启动Linux engine，ready=503，因此登录后业务、Redis与沙箱全栈验收阻塞。未删除或重置Docker数据、未配置密钥、未付费调用。临时demo API监听127.0.0.1:8000，PID记录在ignored `.cache/acceptance-20261028/api.pid`。下一步先恢复Docker，再继续HTTP和浏览器验收。

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

首次试跑前没有真实模型key或付费调用；2026-10-28已本地配置并完成授权开发联调，见顶部更新和本轮报告。仍不得将开发修复结果标成冻结模型准确率。不要在聊天里索取密钥或打印.env。

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
