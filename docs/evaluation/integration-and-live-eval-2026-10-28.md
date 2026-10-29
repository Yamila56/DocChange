# Docker 恢复、本地联调与真实模型开发评测

日期：2026-10-28，北京时间。结论：Docker 已恢复；本地核心业务、浏览器工作台和独立 CSV 沙箱已实际走通；真实 Flash 开发评测发现并修复多处问题。正式冻结测试、模型对比和公开 benchmark 尚未完成。

## 1. Docker 恢复与数据保留

Docker Desktop 4.87.0 先因 Ingest 的 `sailor-ingest.sock` 无法访问而退出，随后暴露 Secrets Engine 的同类错误。完全停止相关 Desktop/backend 进程，确认确切目标目录不是 reparse point 后，将以下运行目录改名保留，让 Docker 重新生成：

- `%LOCALAPPDATA%/Docker/run.stale-shopresolve-20261028-200540`
- `%LOCALAPPDATA%/Docker/run.stale-shopresolve-20261028-200649`
- `%LOCALAPPDATA%/docker-secrets-engine.stale-shopresolve-20261028-200649`

没有重置 Docker/WSL、删除数据盘、清空数据库或卷、修改安全配置、上传诊断。原项目 MySQL 卷与沙箱镜像仍存在；只启动本项目 mysql/redis。Engine 返回29.7.2，MySQL健康。此方法参考 [Docker问题记录](https://github.com/docker/desktop-feedback/issues/531)，是本次实测恢复办法，不是永久修复保证。备份目录保留，不自动删除。

## 2. 实际联调

| 项目 | 实际结果 |
|---|---|
| API→outbox→Redis→Worker→Runtime→查单→SSE续传 | HTTP smoke通过；最终修复后再次运行通过 |
| CSV上传→内部认证manager→无网络Docker→结果收集 | HTTP smoke通过，参考结果44800分；最终补丁后再次复测 |
| 浏览器登录、任务提交、政策咨询、引用与时间线 | 实页操作通过，使用免费demo控制器 |
| 浏览器拒绝操作 | 提案REJECTED，未取消订单 |
| 浏览器批准操作 | 独立合成订单`order-ui-20261028`取消，19900分模拟退款，界面回读FULFILLED |
| 浏览器CSV上传与计算 | 上传合成CSV，页面显示COMPLETED及`total_cents=44800` |
| 浏览器刷新 | 发现当前选择丢失，修复后自动恢复同一任务、结果和事件 |

上述浏览器验收使用demo，不冒充真实模型驱动的浏览器长流程。真实模型走同一Runtime/工具/业务代码，但评测库为每任务隔离SQLite；MySQL/Redis/多进程另列工程验收。仅新增独立UI测试订单，既有演示订单没有被本次UI取消。

截图与临时进程记录位于ignored `.cache/acceptance-20261028/`。本地演示服务监听127.0.0.1:8000及manager18081，进程强制demo/禁止付费，未改`.env`付费配置。临时演示码为`acceptance-local-code`，仅本地验收进程使用；模型密钥不在前端。

## 3. 本次发现与修复

1. SlotCandidate的schema允许任意name，运行时只允许四种，模型生成`order_reference`后重复失败。将schema收紧为`order_id/item_id/quantity/reason`，保留来源与权限检查。
2. resolve_order忽略完整订单号，可能返回全部候选。新增授权候选内的完整ID匹配；未知ID不退化成所有订单；否定表达继续保守补问。
3. 模型批量只读工具触发SQLite事件序号冲突。同批日志事务串行化，实际工具IO仍并行；跨Worker权限仍由数据库lease/fencing负责。增加真正并行启动、事件连续性的回归。
4. 模型在取消提案内生成reason，被业务层拒绝。把动作形状约束前移到决策校验，并在模型schema/提示中明确取消不得带item/quantity/reason，退货理由仍须来自用户。
5. 模型把resolve_order当作最终查单，并把call_id当证据。保留Guard，补上get_order要求、证据ID来源及具体错误反馈。最新独立查单复测通过。
6. pilot只保存用量，失败决策难审计。现保存结构化决策、不保存私有思维链；单case基础设施异常单独记失败；支持仓库内开发数据集与共享累计预算ledger。
7. 已知usage此前不结算费用预留。新增不可覆盖settlement记录；仅完整用量按Flash高峰且忽略缓存优惠结算，未知响应保留预留，物理请求计数不重置。
8. 前端保存当前任务选择，刷新恢复；退出/鉴权失效清除本地显示的任务和订单状态。
   截图检查还发现窄侧栏下的横向溢出，调整grid最小宽度和文件输入宽度；重新构建并在浏览器确认页面宽830px、视口845px，无横向溢出。
9. 首次sandbox HTTP smoke留下一个已完成但未清理的项目容器：终态manifest先发布，smoke可能提前结束manager。改成容器清理后再发布终态，补阻塞清理时仍显示RUNNING的集成测试。仅清理了标签、job_id、挂载路径及COMPLETED manifest均核实的确切遗留容器；未prune其他资源。

## 4. 真实模型开发评测：所有阶段分开保留

模型：官方`deepseek-flash`，非思考、temperature=0，最大输出1500tokens。执行当天[官方文档](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)对应DeepSeek-V4.1-Flash-1017；API别名未来可能变化。没有调用Pro、用户模拟模型或模型裁判。

| 批次/阶段 | 观察 |
|---|---|
| initial，原三场景 | 1/3通过；查单和取消因协议失败，无未授权写入 |
| 单请求诊断与第一次取消复测 | 找到非法slot与显式ID未缩小候选；未完成，不作为成功 |
| postfix，三场景 | 查单/咨询通过；取消遇SQLite事件冲突，报告只完成2项，第三项是基础设施失败，不能称2/2完整通过 |
| dev-v1，五项 | 3/5通过；取消批准和拒绝均被错误reason拦截 |
| final-v1，五项 | 5/5通过：取消批准、单件退货、他人订单、缺数量、取消拒绝 |
| final-pilot，三项 | 2/3通过；查单提前收尾/错误引用被Guard拦截，咨询与取消通过 |
| read-final-repair，独立查单复测 | 1/1通过，3次真实调用，resolve_order→get_order后确定性收尾 |

没有删除失败，没有把不同修复版本拼成一个“最终100%”。五项5/5是开发样本上的一次观察，不是总体准确率；按二项Wilson 95%区间约56.6%–100%，且小样本/非独立标签/开发调试限制更强。后续仍需固定最终版本、独立审阅、按场景族分开发与冻结集，重复trial，再比较固定流程、单次检索Agent与完整Harness。

最新观测已分别覆盖查单、政策咨询、批准取消、拒绝取消、退货申请、越权和缺数量补问。各case检查状态/数量、目标订单、其他用户哨兵、操作数、批准主体和精确模拟退款；新增退货item/quantity/status精确断言。语言证据支持仍需独立人审。

数据：`evals/data/deepseek-pilot-v1.json`与`evals/data/live-development-v1.json`。全部为合成开发数据，预期答案只给grader、不进入Agent上下文。完整原始结构化轨迹留在ignored `.cache/evals/`，不自动上传。

## 5. 费用与请求保护

原批次12次；用户追加授权先30次，后提高到60次，追加费用上限保持5元。两套ledger分开，最终物理生成请求共72次，72次均有返回usage，无用量未知请求。输入254467tokens，输出7370tokens。

按高峰输入2元/百万、输出8元/百万且完全不计缓存优惠，估算0.567894元。此估算不是实际供应商账单；账户余额及密钥不写入报告。真实扣费受时段与缓存等影响。原始attempt预留记录保留，settlement追加结算记录；没有为了获得新额度覆盖ledger。请求上限已用满，后续生成须另获授权。

## 6. 工程与检索证据

- Docker恢复后原版全套：`mysql-acceptance-2026-10-28.xml`，153 passed，276.77秒。
- 中间修复全套：`mysql-acceptance-final-2026-10-28.xml`，157 passed，255.32秒。
- Runtime/契约/费用修复后全套：`mysql-acceptance-final-v2-2026-10-28.xml`，160 passed，256.45秒，0失败/错误/跳过。
- 在上述全套之后新增沙箱终态发布补丁及测试：`sandbox-cleanup-2026-10-28.xml`，7 passed，17.87秒；不把两者相加冒充161项最终全套。
- 新预算/网关专项21 passed；前端tsc+Vite构建、Ruff check/format与git diff --check通过。后续若继续改代码需重新执行对应门禁。
- 检索复测：`retrieval-acceptance-2026-10-28.json/.md`。沿用16条小型合成测试及8条dev，未以本次结果调参；标签仍待独立审核。

| 配置 | Recall@1 | Recall@3 | P50毫秒 |
|---|---:|---:|---:|
| BM25 | 68.75% | 93.75% | 0.38 |
| dense | 56.25% | 81.25% | 5.12 |
| hybrid | 62.50% | 93.75% | 5.05 |
| hybrid+rerank | 75.00% | 100% | 1653.59 |

重排相比BM25的Recall@3仅多命中1题，不能宣称普遍提升；重排延迟P95约5624ms，本次负载下波动明显。16/16的普通bootstrap区间退化为[1,1]，不能用于零不确定性声明；Wilson 95%约80.6%–100%。默认仍BM25。

## 7. 尚未完成

正式冻结与多trial可靠性、语言证据独立人审、同模型基线/消融与Pro等模型对照、公开τ-bench协议适配与评测均未做。本轮优先验证自有政策与工具，不运行不匹配的默认Agent冒充项目成绩。

完整限流/背压/熔断、CSV接Agent工具、完整沙箱资源/重启/TTL矩阵、物流工单/库存替代业务、生产部署与备份恢复仍有缺口。此次修复一个清理竞态，不等于完整孤儿回收已经实现。前端浏览器使用demo、真模型使用隔离Runtime评测，两者分别验证；真实模型驱动的浏览器长流程仍待后续。没有公网部署、创建远程仓库或push。
