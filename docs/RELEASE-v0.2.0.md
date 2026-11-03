# DocChange V0.2.0

2026-11-03后续检索优化已完成：新增可选BGE Dense/RRF/Rerank，完整复测SCIDOCS1000和SciFact300，Dense相对BM25的Recall@10分别提升48.70%（相对）与9.86个百分点。最新能力、CI与完整结果见[改进报告](evaluation/docchange-retrieval-improvement-2026-11-03.md)；下方保留本版最初基线交付记录。

2026-11-03。定位为受控文档变更工作台，保留 FastAPI / SQL / Redis Streams / React / 自研单 Agent Harness 架构。

## 本版内容

- 工作台增加任务修改范围、可新建路径、必需保留/消除的文本条件与影响清单；支持 MD、MDX、RST、TXT 的精确替换、新建与删除。
- 新增按需读取的 Git 仓库适配器，代码参考只读；通过临时 Git Index 导出候选 patch，原工作树不变。与小工作台共享 Edit guard 和 SQL RunStore，网页版本发布与 Git patch 导出分别说明。
- 针对公开任务暴露的问题补充最多4文件批读、定位式读取、阅读清单、草稿复核和调用前预算预检；修复零匹配读取缺少 evidence_id 引发的异常。
- 保留既有异步任务提交、Redis Worker 和独立心跳；将工作台检索计算移出事件循环，并在数据库会话关闭后执行。
- 交付完整 SciFact300、SCIDOCS1000、DoGBench公开dev175结果、73份补丁、逐题评分、数据绑定与复现脚本，附[简历与面试口径](RESUME-DOCCHANGE.md)。

## 验证与效果

修改后定向工程回归35通过、2跳过；Ruff检查与格式检查通过。公开基线仍是修改前策略成绩：SciFact Recall@10 77.57%，SCIDOCS Recall@10 15.43%，DoGBench综合25.61/100。73份patch均可应用，但内容正确性差异明显。本轮不追加大批模型复测，不声称新策略带来已测提升。[完整报告](evaluation/docchange-public-2026-11-02.md)。

提交`e517753`的[远程CI](https://github.com/Yamila56/DocChange/actions/runs/41576363937)三任务全部成功：unit165通过/16跳过，MySQL配置任务174通过/7跳过，前端构建通过。两套工程测试独立记录，不相加为模型评测数量。

## 后续边界

保留人工审查作为发布步骤；复杂开放仓库维护尚不能无人值守。未新增多Agent、Dense/Rerank、异步索引服务或大仓库网页Git发布。Docker固定检查仍需可用Engine实测，文档域负载压测尚未开展。后续按具体用途补充，不作为本版无限扩建前提。
