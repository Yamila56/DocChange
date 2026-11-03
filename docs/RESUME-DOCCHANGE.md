# DocChange 简历项目描述

**DocChange｜基于自研 Agent Harness 的版本化文档变更工作台**

独立开发项目｜2026.10 – 至今

Python、FastAPI、Pydantic、SQLAlchemy、MySQL / SQLite、Redis Streams、BM25、BGE、ONNX Runtime、React、Git

- **业务闭环与版本管理：** 面向接口、配置及示例更新中的跨文档维护任务，设计“依据定位—修改提案—差异审核—版本发布”流程；以不可变 Revision 保存正文与检索索引，通过 ChangeSet、hash 绑定审批及版本冲突检查，约束未经审核的修改与过期提案覆盖。
- **Agent Harness 与受控工具：** 实现结构化工具调用、按需检索、批量读取、精确修改和补问循环，结合 Schema 校验、模型预算、超时与持久 Checkpoint 恢复执行；Git 仓库适配器通过只读实现参考、读取证据绑定和临时 Git Index 导出候选补丁，保留原工作树；仓库任务以带来源的维护要求驱动检索和修改，通过独立上下文逐项复核与有界定向修补控制交付。
- **异步任务与可靠执行：** 基于 FastAPI + SQL Outbox + Redis Streams 解耦任务提交与模型执行，提交返回 HTTP 202；以 Worker Lease/Fencing、独立异步心跳、幂等回执及通知回收处理重复投递和中断恢复。检索计算与仓库文件操作移出事件循环，避免阻塞模型 I/O 和心跳。
- **语义检索与公开优化：** 引入本地 BGE 语义召回和按 owner/revision/内容哈希隔离的有界索引缓存，保留精确扫描覆盖标识符更新；在完整 BEIR SciFact **300** 查询上达到 Recall@5 **81.54%**、Recall@10 **87.42%**。在 SCIDOCS **1,000** 查询上，Recall@10 相对纯 BM25 **提升48.70%**、Hit@10 达 **62.60%**。
- **配对评测与失败归因：** 固定 **1,300** 条公开查询，对比 BM25、Dense、RRF、RRF+Rerank 四配置，结合逐题差异、配对 bootstrap 与候选互补性选择检索方案；另完成 DoGBench公开dev **175** 项文档维护任务的适配评测，留存逐题准则评分和 **73** 份可应用候选补丁，据预算空占与重复探索等问题补充调用前预检、定位读取和草稿复核。

数值来源：[完整检索对照](evaluation/docchange-retrieval-improvement-2026-11-03.md) · [仓库维护基线](evaluation/docchange-public-2026-11-02.md)。检索指标与文档修改质量分别计分，48.70%为相对提升。

演示与面试准备：[项目边界、工程验证及演示顺序](INTERVIEW-DOCCHANGE.md)。

仓库定位补充：在DoGBench公开需修改任务的110项可评分路径标签上，章节混合检索Recall@10由文件BM25的60.88%提升至69.10%（+8.22pp），Hit@10为81.82%；[完整分母和退步案例](evaluation/repository-localization-2026-11-03.md)。这是离线定位指标，不是补丁成功率；Pro本轮按用户要求在约50项收尾，实际49/175项有结果、126项缺失，不能替换旧完整基线，也不在简历宣称端到端质量提升。详见[最终报告](evaluation/docchange-final-2026-11-03.md)。
