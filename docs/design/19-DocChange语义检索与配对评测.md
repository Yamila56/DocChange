# DocChange：语义检索与公开配对评测

2026-11-03。在现有文档领域内扩展检索器，保留 FastAPI、SQL、Redis Streams、单 Agent Harness、ChangeSet 和 Revision 发布流程。

## 改进动机

BM25 按词项匹配排序，适合标识符、参数名和原文定位。SCIDOCS 的查询为论文标题，相关性包含论文间的主题关联，相关文档可能没有足够字面重合。此次将语义召回作为可选工具能力，评测是否补足这一缺口。

新增 `SemanticDocumentRetriever`，与普通 BM25 继承相同标识符、语料单位和词法评分器；公开评测直接使用此实现。模型读取、向量计算与相似度排序留在检索层，Agent 仍通过既有 search/read/propose 循环操作。

## 固定候选方案

- BM25：原 tokenizer、BM25Okapi 默认参数和稳定排序，要求精确重现已发布基线。
- Dense：BGE-base-en-v1.5、CLS pooling、L2 归一化、余弦排序，输入截断至512 token，查询使用模型作者推荐的英文检索前缀。
- Hybrid：BM25 和 Dense 各取前1000，等权 RRF、k=60，按融合分数排序。
- Hybrid + Rerank：MiniLM-L6 cross-encoder 重排融合后的前20项，尾部顺序保留。

模型使用固定 Hugging Face revision 和模型文件哈希。BGE 使用 FP32 ONNX 导出；本机 CUDA 推理和工作台默认 CPU 推理使用同一图与权重。配置在产生本轮质量结果前固定，没有使用 qrels 训练、搜索融合权重或挑题。

## 工作台集成

配置 `SHOPRESOLVE_DOCUMENT_RETRIEVAL_MODE=bm25|dense|hybrid|hybrid_rerank`；本地入口提供 `--retrieval-mode`。默认仍为 BM25，准备模型后可启用新模式。任务创建时将模式写入持久 config，恢复时继续使用该模式。

权限检查先读取指定 owner/工作区/Revision；语义索引仅依据该快照构建。有界进程缓存以 owner、工作区、revision、内容哈希和模式隔离，容量8项。索引可由权威快照重新生成，未把内存缓存变成事实源。只读数据在线程内执行模型和检索，数据库会话此前已关闭。

精确扫描始终按原文逐片段检查，不走向量排序或 top-k。修改、引用、hash 审批、旧版本保护和原子发布沿用现有实现。没有新增外部向量数据库或后台索引发布服务。

## 评测边界

完整 SCIDOCS test1000、SciFact test300，原文档 title+abstract、原qrels、相同K与TREC scorer。四组都保留；分析按同一查询配对，输出改进/持平/退步、bootstrap区间和Dense/Sparse相关命中互补性。

这两套公开测试集此前已经看过，结果用于描述本次系统改动，不能称为未见留出测试。它们测检索组件，不测最终文档修改。DoGBench 基线属于另一任务，本轮检索复测不替代其 Agent 内容质量分。

线上延迟与离线批处理分开：本轮查询向量批量预计算，单项计时不包含查询编码；报告不把它与旧BM25 CPU端到端调用耗时直接比较。GPU环境通过本仓 ignored 目录隔离加载，未修改系统CUDA或原Python环境。

模型依据：[BGE模型说明](https://huggingface.co/BAAI/bge-base-en-v1.5)、[BGE ONNX导出](https://huggingface.co/Xenova/bge-base-en-v1.5)、[MiniLM重排模型](https://huggingface.co/Xenova/ms-marco-MiniLM-L-6-v2)。模型锁见 `evals/document-retrieval-models.lock.json`。
