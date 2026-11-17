# DocChange：版本化文档工作台

2026-11-18 仓库策略更新。独立 Git CLI 默认采用 `batched-react-v1`。模型按新证据决定下一批调查，运行时生成证据关系图和待审补丁。旧仓库策略可用 `--policy taskgraph-v2` 选择。详见[批量 ReAct 架构](design/25-Batched-ReAct仓库维护架构.md)。

2026-10-29，首版本地应用。复用 ShopResolve 的任务持久化、租约、检查点、调用日志、审批与队列基础，新增文档领域。旧业务在 `/commerce`，原数据保留。WorkflowRepair 本轮未继续改动。

## 使用

在仓库根目录运行：

```powershell
.\.venv\Scripts\python.exe -m scripts.run_docchange
```

打开 http://127.0.0.1:8011 。账号 alice 或 bob；本地演示码 `docs-local-2026`。这是仅监听本机的演示登录，不适合公网。SQLite 数据持久保存在 ignored `.cache/docchange/workspace.sqlite`，不会改原 MySQL、.env 或支付数据。

操作：创建示例工作区 → 提交默认修改任务 → 查看 guide.md/api.md 差异与依据 → 批准并发布 → 查看 R2。source 与 archive 为只读。可以拒绝、取消、刷新恢复，也可以导入自己的 Markdown JSON。

导入格式：

```json
{
  "guide.md": {"content": "# 指南\n\n原文", "role": "document"},
  "release.md": {"content": "# 来源\n\n变更依据", "role": "source"},
  "history.md": {"content": "旧版说明", "role": "archive"}
}
```

当前限制：50篇、单篇60KB、合计500KB；可编辑 MD、MDX、RST、TXT 文本。角色隔离在工具层执行，不执行文档中的任意代码。大仓库按需读取与候选 Git patch 导出使用[独立入口](PUBLIC_BENCHMARK_RUNBOOK.md#5-普通仓库使用)。

免费模式是显式替换控制器，不理解任意自然语言。填写演示原文/新文，它会查来源、扫描全部匹配、读取目标、生成提案。查询返回检索原文，不冒充LLM答案。

真实模型模式：本地 `.env` 已配置完整 Key 时，停止演示进程后显式运行：

```powershell
.\.venv\Scripts\python.exe -m scripts.run_docchange --remote
```

当前使用官方`deepseek-v4-pro`、thinking/high，每响应最多8192 token（包含思考与最终JSON）。工作台任务文本由模型理解，演示替换字段不作为自动执行指令。新Pro交互账本最多20请求/峰值预估2元，重启不重置；存于`.cache/docchange/interactive-pro-budget.sqlite`。旧Flash账本保留。未知响应保留预留，不修改.env、不充值；费用是应用侧估算，不是供应商硬扣费上限。

Pro真实接口及新Schema已验收：[记录](evaluation/pro-contract-2026-11-03.json)。仓库CLI同样默认Pro，并支持章节词法/向量/混合检索、带证据片段、多查询和独立定位批读，详见[实施复核](design/20-Repository检索与Pro实施复核.md)。

## 当前架构

```text
React → FastAPI → MySQL/SQLite + Outbox → Redis Streams → Worker
                                                     ↓
                     DocumentRuntime → Gateway / 检索 / 读取 / 精确补丁
                                                     ↓
                         ChangeSet → 人工审批 → 版本与索引同时发布
```

`run_docchange` 是免基础设施的本地入口，使用同一个 RunStore/DocumentRuntime，通过持久数据库轮询领取任务，未使用 Redis。现有 `shopresolve.cli worker/dispatcher` 已按 domain 路由文档任务，可使用原 MySQL/Redis 配置；首次需运行 `alembic upgrade head` 增加文档表。两种运行方式分别标明，不把本地轮询演示称为 Redis 实测。

工作区按 owner 隔离。每个任务绑定不可变 revision。快照保存正文与分段索引，校验 source_hash；新候选完整构建后在短事务中发布。基础索引为小型工作区同步构建的BM25；可选Dense/RRF/Rerank由授权快照派生并在进程内有界缓存，不是独立异步索引服务。精确扫描枚举全部标识符，top-k检索不承担全量替换。

2026-11-03新增英文语义模式：安装`.[dev,retrieval]`后执行`python -m scripts.prepare_document_models`，再使用`python -m scripts.run_docchange --retrieval-mode dense`。固定BGE-base-en-v1.5权重，CPU本地推理；`hybrid`与`hybrid_rerank`为对照可选项。完整1300条公开查询复测中Dense在两套数据的Recall@10均最佳：[结果与差异分析](evaluation/docchange-retrieval-improvement-2026-11-03.md)。

异步任务已经实现：提交返回HTTP202与run_id，后台执行而不占住原请求；模型、SQL、Redis为异步I/O，Redis Worker独立心跳。文档检索在读取快照并关闭数据库会话后经线程计算，保持事件循环可服务。一进程Worker顺序消费，可多进程部署；本地轮询模式与Redis模式分别说明，未作吞吐量或高并发提升声明。

模型动作：search、scan、read/read_many、propose、answer、ask。只读批次最多12文档；相邻JSON只读对象可规范化为一个read_many，混合写动作不会被抢救执行。工具响应先记录，检查点恢复可复用决策。

ChangeSet 记录基准版本、候选内容、差异、索引、引用和payload hash。审批只能针对这个hash，发布前检查当前revision；过期则STALE。批准事务写新revision、切换指针并记录回执，重复请求返回同一结果。检索索引和正文在同一快照内切换，避免半发布。原版本可继续查询。

可见时间线来自持久事件；UI目前轮询快照和事件，通用SSE接口继续存在。首版单Agent，未增加子Agent与跨会话长期记忆。

## 沙箱

原 Sandbox Manager/Docker 接口已接入“沙箱复核”按钮。提交的是固定文档检查程序与候选内容，不运行模型提供的代码；初版只检查反引号围栏配对和NUL字符，不把它叫完整Markdown语义验证。

普通补丁和内部链接检查直接在可信程序执行。沙箱复核是可选独立检查，不是每次修改必须启动容器。

需要配置原来的 `SHOPRESOLVE_SANDBOX_TOKEN`、`SHOPRESOLVE_SANDBOX_URL` 并启动manager。若未配置或Docker不可用，明确返回不可用，不谎报检查通过。当前会话Docker Engine不可用，未完成本轮真实容器复核；之前CSV沙箱成绩不挪作新文档检查成绩。

## 验证与量化

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_documents.py tests/test_gateway.py tests/test_queue.py -q
.\.venv\Scripts\python.exe scripts/evaluate_documents.py --output docs/evaluation/document-engineering-v1-results.json
.\.venv\Scripts\python.exe -m scripts.pilot_documents
```

最后一条默认只输出计划，不调用模型。付费验收结果已保留，原批次禁止覆盖；`--execute` 需要明确预算授权。

工程任务集 `evals/data/document-tasks-v1.json`：9个开发fixture，不是9个独立真实用户需求。验证发布、拒绝、历史保护、精确替换、查询、缺匹配、内部链接错误和版本失效。按最终文档、保护内容、版本数量和索引hash核验，不只看status。

真实验收集 `evals/data/document-live-v1.json`：4个不同类型任务，Agent只看到任务与文档，评分条件由隔离评测端持有。用于受控联调，不是独立标注/未见泛化集。原始失败与复测分开保存。

完整结果与口径见 `evaluation/docchange-delivery-2026-10-29.md`。现阶段可以报告功能、实际验收任务与成本；没有基线/消融时不写“成功率提升X%”。后续只有出现明确失败或性能瓶颈时再扩测。

## 后训练还差什么

WorkflowRepair已有小型确定性环境，不是训练就绪版本。下一步依次完成正式任务生成与分组划分、可信verifier审查、verl工具/奖励适配与小型rollout、SFT/GRPO、验证集选checkpoint与对照测试。先完成文档应用收尾，再启动这些工作；不会仅租一张卡就自动获得可信研究结果。


本轮代码与评测已结束，边界见[最终交付报告](evaluation/docchange-final-2026-11-03.md)。Pro本批按用户要求以约50项收尾，实际49/175项有结果，其余126项未完成；全部已有结果已评分，没有新的175项全量综合分。不再启动剩余模型任务。
