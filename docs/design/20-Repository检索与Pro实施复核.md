# Repository 检索与 Pro：建议复核和本轮实现

2026-11-03。依据用户提供的“分析文档Agent架构”讨论，对照本仓实际代码复核。本轮开始时已跟踪文件干净，最新提交为`02d5726`；没有未结束的旧模型评测或半写源码。旧自建评测草稿保留，未作为新的公开评测上传。

## 核心判断

原建议对“两个检索路径尚未统一”的判断正确：工作台已经支持BGE Dense/RRF，但Repository/DoGBench路径仍使用文件级BM25。科学文献检索提升不能证明仓库维护定位或补丁质量提升。更应改进工具提供的信息和跨文档覆盖，再用相同公开任务验证。

保留FastAPI、SQL Outbox、Redis Streams、RunStore、自研单Agent和人工审批。当前采用以下有限改动：

| 建议 | 本轮状态 | 实际约束 |
|---|---|---|
| 文件级改为章节级检索 | 已实现 | Markdown/RST标题上下文、原文片段、路径与行号；不生成虚构摘要 |
| 向量与混合检索接入Repository | 已实现 | section_bm25/dense/hybrid；可选择精确符号或语义路径 |
| search直接给证据、合并多查询 | 已实现 | 最多4个查询，一次返回片段与evidence_id；保留分页 |
| 每文件独立定位的read_many | 已实现 | `reads=[{path,query,offset,limit},...]`，保留旧paths兼容 |
| 自动提供实现差异信息 | 已实现 | 变更路径、语法标识符、有限diff原文；明确截断与未验证语义 |
| 已观察范围检查 | 已实现 | 替换的所有原文出现位置须已展示，删除须完整观察；过期片段不能授权新正文 |
| checkpoint恢复范围状态 | 已实现 | 搜索/读取观察与精确字符范围持久化；复用工具回执时恢复观察 |
| 长检索独立心跳 | 已接入 | 保留lease/fencing；心跳失败取消运行，不能授权陈旧Worker写入 |
| 结构化coverage plan与逐编辑证据映射 | 后续优先项 | 必须来自任务与公开实现，不能从rubric反向生成计划 |
| 独立语义Verifier/覆盖闭环 | 尚未实现 | 目前仍是草稿复核提醒；不能把模型自报covered当作正确性证明 |
| 训练检索器、多Agent、GraphRAG | 不作为本轮实施项 | 先用仓库定位结果确认真实瓶颈，再决定是否扩建 |

搜索片段计作“观察”，但只授权展示过的字符范围，不等同于读取整篇文件。引用ID存在、原文出现过、修改事实正确是三个不同层次；本轮加强前两者，语义正确性仍需任务评分与人工审核。

## Pro 已切换

新的文档远程入口、仓库CLI及候选评测默认使用`deepseek-v4-pro`，开启thinking/high。输出预算8192 token，包含思考和最终JSON；只保存最终结构化决策与usage，不保存私有思考文本。Pro请求不带无效的temperature控制。

工作台使用新账本`interactive-pro-budget.sqlite`：最多20请求、峰值估算2元；仓库CLI新账本`repository-pro-budget.sqlite`：最多100请求、峰值估算2元。旧Flash账本和已发布结果保留，不重置、不冒充Pro成绩。模型费用预检先于逻辑轮次预留，未知usage保留预留。

高峰价按官方2026-11-03页面：Pro缓存命中输入0.30元/百万、未命中9元/百万、输出27元/百万；不是Flash的0.04元缓存价。[官方定价](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)。协议依据：[思考模式](https://api-docs.deepseek.com/zh-cn/guides/thinking_mode/)。

真实Pro协议验收已完成1次：返回模型`deepseek-v4-pro`，新Schema有效，usage包含57个reasoning tokens；峰值估算0.017694元。这证明接口与协议可用，不是端到端任务成功率。详见[验收记录](../evaluation/pro-contract-2026-11-03.json)。

## 三层评测分开

1. 已完成：SciFact300/SCIDOCS1000，验证检索组件实现，原结果保留。
2. 本轮仓库定位诊断：针对DoGBench公开dev中123个patch-needed任务，比较文件BM25、章节BM25、Dense、RRF及diff标识符扩展。标签来自原数据`docs.paths`，只在排名生成后进入评分，候选函数不接收标签、outcome或rubric。
3. 尚未得到新全量成绩：Pro＋新工具的175项端到端维护评测。原25.61/100仍是旧Flash基线，不据检索诊断改写它。

人类实际改过的路径只是维护面定位代理，不等同于canonical rubric要求；新增文档路径在旧快照中无法被检索，须分别报告全部路径与既有路径分母。定位提升也不能替代最终补丁的完整性、P0和准确性检查。

本轮工程回归报告：[repository-discovery-pro](../evaluation/repository-discovery-pro-2026-11-03.xml)。论文依据与取舍见[设计21](21-检索论文启发与适配取舍.md)。
