2026-11-18 批量ReAct复核恢复：初稿之前最多3轮调查，复核专用1轮，总模型决策上限10。补查后沿用现有草稿和交付目标，REVISE只提交增量，通过编辑ID显式替换。no_change要求文档与实际实现两侧引证；仅文档存在时返回未解。无效路径提示可由可用查询继续，跨轮去重依据实际读取区间。复核降载保留原引文定位和哈希，共享编辑按绑定整体修补并保留同文件无关编辑。全仓本地242通过/16跳过，随后调整调查输入和对照统计，相关11项复核与Ruff检查通过。记录历史32对10和当前同预算10对10的复现方式。本轮未运行生成API或DoGBench评分，暂无新版质量成绩。

2026-11-18 批量ReAct首次远端单元和前端通过，MySQL暴露阶段名超过现有24列宽以及旧浮点截止时间恢复fixture不稳定。缩短阶段名且不做迁移，fixture改为实际整数毫秒；对应18项本地回归通过，等待修复后的远端CI。

2026-11-18 批量ReAct仓库策略已实现，覆盖通常的Git CLI与DoGBench入口。h4定稿49题：41未解、5模型输出校验失败、3项最终决定、1项方向正确，旧批次存档。新策略让模型选下一批调查问题，运行时稳定排序并发工具批次，最多3轮/10模型调用/1局部修补；独立复核可补查，覆盖按交付目标和真实编辑派生。Git候选对象进入临时库，不再污染封存工作区。全仓232通过/16跳过（99.29秒），随后新增局部范围测试并合在新策略专项5项通过（7.59秒）；Ruff/format通过，WSL样本验证补丁可应用且原HEAD/tree/文件/对象库不变。新策略尚无生成API或公开质量成绩。设计25和评测复现入口已更新。

2026-11-17 H3 固定49题官方导出0/49，49题诊断见docs/evaluation/taskgraph-h3-failure-analysis-2026-11-17.md。22份草稿，Hamilton待审批补丁因重复status漏记，已独立恢复并过Git/轨迹检查，官方规则单独50分，不改原分。H4分离unresolved和系统错误，局部合并repair，按依据校验no_change，最终依据闭包，修复出口status。最终定向48通过、Ruff通过；新49题同Flash、检索、预算与时限独立启动，不扩175。

2026-11-17 续接核验：763af99 CI44103043977通过，unit224/16、mysql233/7、前端通过。已删除原版和h1中止批次14条失败结果导出及对应汇总，保留正式基线。h2暂停时0最终输出。已修复证据哈希排序和路径提示抢不到名额，保持搜索顺序并优先传目标批读与相关片段，2文件/120行边界。DoGBench核验49/49，40/40有实现仓库的匿名HEAD^树及相关blob符合冻结输入，另9无代码。定向44通过，真实Materialize WSL本地读取桥接首100文件与原读取一致，8811文件完整返回，冷搜索12.84秒。新批次dogbench-taskgraph-v2h3-20261117运行Flash/原49，CUDA继续负责文档向量检索。Reviewer覆盖仍限计划和候选引用。

2026-11-17 h1真实试跑发现任务/实现引用传Git观察工具的接口错配；原正式批次中止，保留4份失败及所有账本/冻结源，不与新成绩混算。h2补齐引用领域适配、将编辑/no_change及review条件写入Schema，加入跨运行2个冷代码索引槽位防内存压力；不改语料、召回、K及单任务32/180/1800预算。h2新批次dogbench-taskgraph-v2h2-20261117原49题待CI后执行；定向32通过，尚无h2模型质量数字。

2026-11-17 整数时间修复cf030ad已push PRIVATE，CI44098515163全部SUCCESS：unit219/16skip、mysql228/7skip、frontend成功。本轮原49题已恢复同一账本，原1结束输出与8计划保留，不重复生成/不重置期限。候选源23文件按amendment校验一致；配对报告增加中断耗时标记和未中断题的描述性时延，全部49题仍进质量分母。离线分析7项通过，尚无完整49题新成绩。

2026-11-17 后续CI发现MySQL JSON浮点往返导致CHECKPOINT_INVALID：暂停新批次，期限与耗时改整数毫秒，模型/Prompt/Schema/候选校验/检索方法逐函数源码一致。保留同批已结束1项及8份计划，SQLite备份+源码amendment+engineering-recovery审计，不重置账本/调用数/原期限；定向22通过。MySQL本机未启动、复验连接拒绝，不算通过；待新CI通过再续评测。

2026-11-17 用户要求先修复再评测：停止旧taskgraph-v2试跑，保留所有结果/账本/冻结源，禁止用新代码续接旧批次。已拆阶段Schema、加入计入预算的单次协议纠错、收紧多需求及覆盖格Repair范围，修复SQLite写锁升级、心跳异常传播、取消信号、恢复截止时间及Windows工件原子写入；docs/code按根加锁，发现后释放索引，增加耗时记录。不扩K、不换检索、不加重规划。全仓217通过/16跳过（112.20s），最终定向27通过（18.15s），Ruff/format通过。新批次dogbench-taskgraph-v2h1-20261117固定原49题，Flash非思考/32决策/180s请求/1800s任务/8并发，准备冻结后执行，尚无新版模型结果。设计24和硬化报告已写，后续先完成逐题评分和失败分析，不自动扩175。

2026-11-17 TaskGraph融合改进已完成并推送PRIVATE GitHub：源码ac0ff0c，Actions44083612897全部SUCCESS（unit/MySQL/frontend）。12项新策略验收，全套本地204通过/16跳过，最终新旧仓库定向20通过；CI对最终源码重新完成全套验证。机器报告源码哈希以已提交Git对象字节为准，修正测试文件本机CRLF/提交LF造成的hash差异。设计23、工程验收、简历与入口文档已提交；本轮0生成API、0训练，不继续扩展功能或跑付费A/B。最后仅文档收尾提交，不重复跑已通过的CI；以最新origin/main为准。

2026-11-17 TaskGraph融合改进已实现，待最终提交/推送：新taskgraph-v2独立接在RepositoryRuntime的既有RunStore/租约/回执上；PLAN一次给出影响假设与查询DAG，自动发现不消耗模型决策，结构化覆盖/编辑/独立复核，最多一次限定需求与路径的修补；Requirement/Evidence/Coverage/Edit/Review关系随检查点保存并可由CLI导出。run_repository默认v2，可显式coverage-v1；DoGBench默认旧策略，仅新增显式切换参数。网页DocumentRuntime、后端、检索、全部历史成绩不改。借鉴DeepResearch分层DAG模式，未复制其运行时。全套本地204通过/16跳过，最后边界调整后新旧仓库定向20通过；Ruff通过。新策略没有模型benchmark收益数字，本轮0付费生成、0训练、不租卡。新评测和设计报告见docs/evaluation/docchange-taskgraph-2026-11-17及design/23。待发布检查、私有main推送和实际CI。GitHub CLI独立登录已失效，但现有Git凭据有效，.cache/read_docchange_ci.py仅用同一Git认证读取本仓CI，不保存或输出Token。旧未跟踪评测草稿保留且不纳入提交。

2026-11-17 README对外展示修订：重写项目定位、工作流程、系统设计、公开评测与本地启动入口，移除首页中的私人预算、交接与过时状态。仅文档编辑，未运行模型或重新执行应用测试；提交前检查Markdown链接和git diff。

2026-11-04 Flash延时配置固定49题复测已完成：49/49结束并评分，正常返回25项、执行失败24项、最终补丁24份。原Pro同题正常11、失败38、补丁6。检索/Agent策略不变；Flash非思考、32轮、请求180秒、单题1800秒、8并发，属于联合配置比较，不声称模型单因素提升或175题全量。完整报告、原始预测、评分、补丁和配对数据独立导出，旧结果不覆盖。Key/余额/SQL不上传。后续仅发布检查、私有GitHub推送和CI，不追加功能或训练。

2026-11-04 Flash同题复测进行中：用户授权使用现有余额重新评测，固定原Pro已结束的49个任务ID。DeepSeek Flash非思考，32决策、请求180秒、单题1800秒、8并发；检索和coverage-v1不变。新batch .cache/evals/dogbench-flash-long-20261104，生成/评分/导出后台流程 .cache/run_flash_long.ps1（PID21580），发布后台流程 .cache/publish_flash_long.ps1（PID33424）。源evaluate_dogbench.py已冻结入新plan，运行中不要修改。workspace-repair-audit记录49/49输入再验证；旧Pro资料不覆盖。最终比较及原始工件须相同49题都评分后才发布。没有充值或租卡，不继续后训练。

2026-11-03 最终范围变更并收尾（取代下方继续175/旧29题状态）：用户明确要求约50项即可导出并尽快推送GitHub，不再继续剩余模型任务。实际保留49/175已结束任务，126项缺失；全部49项按原官方规则评分，返回6份最终补丁、5份不修改，决定正确10/49，执行失败38。composite仍None，不将阶段子集称为新175全量。旧完整Flash与1300查询检索结果原样保留。停止时在途任务维持缺失，不补零或重做已完成决定；费用/usage只保留原记录，不再调整资金保护文件。最终报告、简历和全部工件已更新；私有GitHub提交/CI以最新git和Actions为准。不继续后训练或额外功能。

2026-11-03 费用错误已更正、恢复全量任务（替代下方“结束”状态）。用户指出官网仍近40余额，已查官方：工作日9-12/14-18峰价，其余半价。本批413次请求的验证时间窗为北京时间19:01–20:09，全部空闲；旧59.0805576是错误用于停机的峰值保守数，正确已知费用19.3976388，含未知预留上界29.5402788。旧budget.sqlite不改写，复制到budget-offpeak.sqlite保留413条ID/usage；仅显示/控制按半价。用户最新授权“余额提醒之前可以一直”，由用户设置提醒；已按现有预付资金增加39元可用续跑额度，不自动充值、不再以旧60元峰值估算挡住任务。billing-correction.json保存来源、窗口、金额与授权；私人精确余额只在ignored private-balance-snapshot.json，不上传。

已启动scripts.resume_offpeak_dogbench，exec session22216；固定候选模型/Prompt/检索/每题16轮不变，29项所有已完成/失败决定不重生成。新增UsageCapturingGateway仅保留异常响应中的usage以正确记账，不改变解析和模型输入。11项计费专项通过；计费脚本只允许整个120秒超时窗口为空闲时段，避免跨峰期仍半价。新摘要含known_usage_cost和unknown_reserve分别列示，内部仍peak-equivalent单位，禁止把数据库原数当实际CNY。已开始第414+条请求，按新口径继续146项。

最终发布刚才被链接检查拦住，尚未push错误的“29题最终报告”。已核实Materialize36075历史候选实际为abstain，先前草稿中“修改候选改为弃答”是错误推断，已改为复核后继续执行并正确弃答；不存在rejected-draft补丁，artifact-validation计数已改0，断链已删除。下方旧29题关闭描述为历史阶段，待完整生成/评分后重写最终摘要。当前所有final文档/导出都unstaged保留，不得误推成全量完成；源码remote27fd441此前CI全绿。剩余工作：等session22216，官方评分续跑、重新导出/最终报告/简历/commit/push。不要重跑已完成语义决定，不追加后训练。

2026-11-03 本轮最终模型评测已停止，不再发模型/训练/检索调参。60元累计预估上限触发；最终账本59.0805576元含22未知usage预留，不是实际扣费。413次模型预留与SQL各Run计数总和一致，410条回执（3次控制器中断无完整回执）。29/175项结束并按官方规则评分：19需改/10无需改，8项最终决定（3patch/5abstain），21执行失败（15轮次耗尽/3多JSON/1截断/2超时），7/29决定正确，3patch15/53.8/20分，2份无P0但均非全部要求满足。146项缺失，composite=None；旧175 Flash25.61保留，不可替换或补齐新Pro。已有调用中所有带model字段的378条均Pro，未用Flash。

预算已阻止新任务，调用日志静止超过6分钟后停止余下无付费准备（目标PID33496/34848）；session49440不再运行。generation-stop.json与同账本审计保留。仅对已有29个result文件汇总，不把缺项变成0；未重新生成语义结果。官方29项全部取得评分，judge目录 `.cache/evals/dogbench-coverage-pro-judgments-20261103`。公开导出docs/evaluation/dogbench-pro-2026-11-03，最终人读报告docchange-final-2026-11-03.md；全费用/失败/源码修复/输入校验/覆盖状态/三份canonical补丁均保留。旧2次0API启动故障另存缓存不计入模型任务。

当前源码e0e0194/27fd441已PRIVATE push，对应CI41628284339/41631329012均SUCCESS（unit181/16，MySQL190/7，frontend成功）；导出小修复与最终报告/README/简历尚待最后commit/push和必要CI确认。不得宣称全量Pro完成或整体质量提升。仓库定位123全完成，110适用标签R@10混合69.10 vs文件BM2560.88、Hit81.82；科学IR1300全量原报告保留。当前工作仅剩发布检查、最终push/CI与向用户如实交付，不再追加评测，也不继续后训练。

2026-11-03 19:36进度：最新源码/测试e0e0194已push PRIVATE GitHub，Actions41628284339全部SUCCESS（首次41627711281的MySQL失败是恢复测试模拟时间问题，已仅修测试；不得误称首次全绿）。本地真实专项17通过、全套此前181/16；当前CI提供最终跨平台验证。GC/完整attestation校验session69037已正常退出0，175/175检查完成，workspace-repair-audit.json保存HEAD/tree不变审计；仅匿名cache清理不可达候选对象，未改用户文件。

当前正式Pro累计60元批次继续运行：session49440，进程父33496/实际34848，batch `.cache/evals/dogbench-coverage-pro-frozen-20261103`。已完成6→7项（失败保留），累计API约140次、峰值含未知预留估算约23元；运行中summary最准。不要重复启动模型或改candidate源码。仍须等待完成175或60元cap、再正式score/export、最终README/简历/报告和docs push。原175 Flash25.61依然唯一完整维护综合分；新Pro不能冒称全量。

后续执行：官方评分 `python -m scripts.score_dogbench --predictions <batch>/predictions.jsonl --output .cache/evals/dogbench-coverage-pro-judgments-20261103 --workers 4`，既有3条失败零分已评分（无新judge请求）。export脚本scripts/export_docchange_final.py保留所有predictions/完整评分/coverage状态、费用、未完成ID，canonical补丁写bytes避免Windows换行，另存未复核草稿。导出要同时复制source-repair-amendment、engineering-recovery和workspace-repair-audit以说明缺陷恢复。README/简历仍未提交的最新修改待结果后改为真实完成状态。不能删低分或用旧Flash填新Pro缺题。若cap先到，report composite必须None并说明未完成，不再自动请求增加预算。

2026-11-03 19:30工程缺陷修复与续跑：正式Pro运行出现Reviewer对list返回的字符串调用.get的确定性错误，及旧候选Git对象导致匿名输入校验失败。已停原budget续跑session4877（仅杀本批明确PID16132/15872），未改变检索/Prompt/模型/轮次策略；修复仅Reviewer筛掉非dict项，新增list→read→draft→review回归，两分支均通过。新定向17 passed/20.89秒，Ruff通过。源码d1574de已push PRIVATE GitHub，Actions41627711281正在运行；报告尚未最终完成。

预算仍累计60上限，旧59+次尝试/未知usage保留同一账本。`.cache/evals/dogbench-coverage-pro-frozen-20261103/source-repair-amendment.json`精确记录repository.py和评测控制器两个文件before/after哈希，原plan不覆盖。只恢复1项Reviewer在发出review请求前异常的同一候选（模型次数保留）与2项0模型调用的准备失败；原结果改名before-*留存，工程恢复清单在engineering-recovery.json。所有真正完成语义决定、轮次耗尽与截断失败不重生成。

Linux离线清理/校验匿名评测cache正在session69037进行，40/175已核对；严格限定prepared/sealed/workspaces，只清理Git不可达候选对象，HEAD/树/源文件不变，逐项原始attestation重验。当前前40准备已通过，后续大队列预清理领先模型执行。已重新启动同账本续跑，新的exec session见最近工具返回；原session18675与4877均不再运行。不能只信旧完整175分数，仍须等待新生成与官方评分。

2026-11-03 费用授权更新：用户已明确同意“允许最多60元，我去补足余额”。官方只读账户预检现可覆盖剩余授权上限，未打印余额，未自动充值。新控制器scripts/resume_authorized_pro_budget.py仅用于显式调整同一本账本上限，写入amendment审计；原冻结plan/候选哈希和每题16轮保持不变，已有完成/失败结果不重生成。当前原10.9批还在收尾，须待session18675退出后再启动该控制器，避免并发修改账本。

首批复杂Azure任务已出现真实模型轮次耗尽、输出截断以及读取参数超限，均保留，不挑选重跑。原2次Windows导入故障启动为0模型调用；与正式55+次Pro调用分开。不能称新架构已经改善内容质量，更不能用检索分替换补丁评分。继续跑同一冻结候选至175完成或累计60元费用边界；若费用边界先到，缺题保留未完成，不伪造综合分。

2026-11-03 最终范围已实施，用户要求本轮做完后不再增加功能。最新冻结源码93a3493，核心策略b8220be：Repository CLI/DoGBench启用coverage-v1，PLAN→EXECUTE→独立REVIEW→最多一轮定向修补；要求带来源、逐edit绑定要求与证据，补丁和弃答均复核，未通过不发布。网页小快照仍走DocumentRuntime，不冒称所有入口共用新策略。保持后端/队列/版本审批架构。设计22与仓库定位人读报告已提交；123定位全部完成，110有标签/102有既有标签，hybrid R@10 69.1046% vs fileBM25 60.8838%，Hit@10 81.8182% vs72.7273%，全方法保留。

本地全套181 passed/16 skipped，151.34秒（docchange-coverage-local-2026-11-03.xml），新策略/仓库/Pro定向17 passed，Ruff全仓通过；未执行新Docker/Redis文档实机验收。相邻范围测试的root/read错位已修复。原37项报告早于最后变更，不作为最终全套。

Windows新控制器首两次启动因subprocess局部/全局导入错误在模型执行前失败，缓存final/release保留，均0次API预留，不当作Agent成绩；已修复导入并以UTF8模式启动正式批次。当前正式运行`.cache/evals/dogbench-coverage-pro-frozen-20261103`，exec session18675，Windows本机CUDA1.23.2+既有DLL，读取WSL匿名快照，验证通过Linux只读桥执行。模型仅Pro/high；所有阶段合计16轮，任务900秒。策略已冻结，不按分数重选输出或继续改策略。175项结果未完成，原Flash25.61/100保留。

已有余额cap记录仅ignored `.cache/evals/final-account-preflight.json`，当前账本10.90CNY预估上限。已异步询问用户是否允许累计60元并自行补足官方余额（不自动充值、不索取Key）；未收到答复前不得提高。预算不足项deferred，不写Agent失败、不伪造全量分数。工程已支持保存run_id/Checkpoint恢复，但当前预算配置不可静默提高，若用户补充授权须记录预算续跑来源与冻结候选哈希，避免重生成已完成结果。

源码仅本地commit，尚未push本轮。用户授权最终push至PRIVATE Yamila56/DocChange；当前远程仍02d5726。待生成/评分、报告/简历、密钥检查与GitHub CI完成；若余额阻塞继续完成可独立的交付并如实报告。已剔除旧自建3脚本的staging但保留工作区草稿，不git add -A。仍不可上传父目录私人材料/.env/cache/DB/完整下载仓库。后训练项目不继续。

# ShopResolve 进度与续接记录

2026-11-03（新主任务进行中：吸收架构建议、Repository检索升级、后续改用Pro）：已完整读取用户引用的“分析文档Agent架构”讨论，核实工作台Dense与Repository文件BM25路径割裂。开始时tracked工作树干净、无旧评测进程，未发现半写源码。新代码新增`repository_search.py`：标题/路径上下文章节、section_bm25/dense/hybrid、多查询RRF、内容寻址向量缓存；Repository搜索直接给原文/行号/evidence，read_many支持逐文件锚点。替换全部位置须落在当前仍一致的已观察字符范围，删除须完整观察；范围随checkpoint保存并能从缓存工具结果恢复。RepoRuntime提供确定性diff摘要和独立心跳；不把摘要当语义真值。结构化coverage plan、逐edit证据映射、独立语义review仍未实现，不能宣称本轮已完成。

新文档远程/仓库CLI/候选评测默认`deepseek-v4-pro` thinking/high、8192总输出token、120秒请求、120KB输入上限。新独立Pro账本：工作台20请求/峰值预估2元，仓库CLI100请求/2元；旧Flash账本与基线保留不重置。Pro缓存价改为0.30元/百万（非Flash的0.04）。真实Pro协议验收仅1次，返回Pro且Schema有效，57个reasoning tokens，usage已知、峰值估算0.017694元；记录`docs/evaluation/pro-contract-2026-11-03.json`，不是新任务成功率。没有再调用Flash、充值或租卡。真实密钥及余额不打印不上传。

本轮最终定向回归37 passed/30.22秒（`repository-discovery-pro-2026-11-03.xml`），包括缓存搜索落盘后/checkpoint前故障恢复、范围还原且不重跑搜索；不是新37个LLM任务或OS级kill全矩阵。Ruff全仓与格式通过。Python113/JSON45的读入语法检查时未发现残缺文件；旧自建评测未跟踪草稿未纳入新评测。新设计20/21记录建议采用范围和ICLR2024 RAPTOR、ICLR2025 CDE、ACL2026 Findings Agentic-R及A-RAG预印本的具体取舍，不宣称复现论文。

正在运行完整DoGBench公开dev中123个patch-needed任务的独立“人类变更路径定位”诊断，5方案file_bm25/section_bm25/dense/hybrid/hybrid_diff；生成器只收原文与context/diff查询，`docs.paths`只在评分端读取。全路径/既有路径分母分开；这是定位代理，不是canonical rubric或175项Agent综合分。GPU正式批次`.cache/evals/repository-retrieval-b32-20261103`，session21246，输出目标`docs/evaluation/repository-retrieval-2026-11-03.json`，逐任务缓存可恢复。第一批`.cache/evals/repository-retrieval-20261103`只完成2项后为本机吞吐调整批大小而停止；旧记录保留，不纳入全量分数。相同模型/runtime/provider的内容向量缓存复用；32批与8批同图、无标签调参，单次长输入吞吐预试2.46秒→2.336秒，仅资源诊断，不宣传系统加速比例。正式结果当前未全量完成，不先报质量提升；首个超大Azure快照51940章节，冷编码较慢，后续相同章节复用缓存。勿在运行中编辑`evaluate_repository_retrieval.py`、`repository_search.py`、`retriever.py`、`neural.py`或模型锁，防止冻结源漂移。仅Pro入口、文档、测试等独立修改可继续。

仓库CLI现会先离线准备本地索引再创建run/领取租约和请求模型；预热观察清空，不授予模型修改权限，冷建索引不消耗付费模型轮次。仍需等待123定位诊断完整完成、检查报告/标签局限、源码提交及远程CI，再推完整工件。暂不启动Pro整批175，原25.61基线不改写；不继续后训练项目。父目录私人材料、.env、缓存、完整下载仓库均不可上传。

2026-11-03 检索全量复测完成（替代下方进行中状态）：正式CUDA批次session23808已正常退出0，完整SCIDOCS1000/SciFact300四组（BM25、Dense、RRF、RRF+top20重排），BM25全部质量指标精确复现旧结果。Dense两套均最佳：SCIDOCS R@10 15.4283%→22.9417%（+7.5133pp，相对+48.70%），nDCG@10 14.9529%→21.7119%，Hit@10 47.8%→62.6%；SciFact R@5 81.5389%、R@10 77.5667%→87.4222%（+9.8556pp）、nDCG@10 74.0391%。RRF与重排未优于Dense，完整保留，不继续按test分数换模型调参。

配对bootstrap2000次/seed20261103：SCIDOCS R@10提升95%区间[6.4516,8.5735]pp，352提升/565持平/83退步；SciFact区间[5.7883,14.4561]pp，43/247/10。SCIDOCS Dense独有相关前10命中518，BM25独有148，共有614；有互补但固定融合未胜出。正式结果、所有K/逐题数据、模型/源哈希在`docs/evaluation/public-ir-v03-2026-11-03/`；人读报告`docchange-retrieval-improvement-2026-11-03.md`。新简历/首页精选有证据的改进，旧弱分和退步保留完整报告；另存`docs/INTERVIEW-DOCCHANGE.md`供边界核对。DoGBench175未重跑，不宣称其生成质量改善。

检索代码已提交并推私有main：`6a82b0053f99f39d0179af60e546e9210305a5a6`，Actions `41579224498` unit/frontend/mysql全部成功：168 passed/16 skipped、17.70秒；MySQL配置任务177 passed/7 skipped、68.17秒。真实CPU ONNX工作台HTTP查询200、任务提交202、后台引用返回SUCCEEDED的冒烟已完成（demo控制器，不算LLM质量），17项相关回归通过。本次文档提交收录完整报告、简历与15份更新工件；1300×4逐题汇总、冻结源hash核验通过，59个本地链接有效，待发布内容未发现本地密钥。不再改实现或新增评测。本轮无生成API、充值、租卡、训练；GPU推理进程正常退出，末次回读GPU占用0MiB。最终文档提交与推送以本仓git记录和origin/main为准，保持PRIVATE。

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
