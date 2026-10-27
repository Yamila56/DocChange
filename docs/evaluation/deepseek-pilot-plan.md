# DeepSeek 余额、费用与真实评测计划

核验日期：2026-10-27。未产生付费模型调用。

## 账户与模型

充值的是 API 账户余额，并非买断某个旧模型。三四月充值不意味着只能调用三四月的模型。
实际剩余额度以官方账户或 GET /user/balance 为准；可用模型以 GET /models 为准。
充值金额不等于当前余额，赠送余额也需看有效情况，不能凭聊天推断剩余25元。

首轮选官方 deepseek-flash，显式非思考模式。官方文档当前对应 DeepSeek-V4.1-Flash-1017；
兼容别名和价格可能继续变化，跑正式批次前再次查验，不沿用旧 deepseek-chat 的经验。
使用自有 JSON 决策协议，不在此阶段接付费搜索/抓取、LLM 用户模拟器或 LLM 裁判。

官方来源：

- [模型与人民币价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)
- [余额接口](https://api-docs.deepseek.com/api/get-user-balance/)
- [模型接口](https://api-docs.deepseek.com/api/list-models/)
- [JSON 输出](https://api-docs.deepseek.com/guides/json_mode/)
- [思考模式开关](https://api-docs.deepseek.com/guides/thinking_mode/)

## 预算怎么估

当前 Flash 高峰、缓存未命中：输入2元/百万token，输出8元/百万token；空闲时段各减半。
忽略缓存优惠，按高峰价保守估算：

费用（元）= 输入token × 2 / 1,000,000 + 输出token × 8 / 1,000,000。

每次请求输入10,000、输出1,500 token：约0.032元；100次约3.2元。
一个任务通常有多次请求；100个任务若各8次，且每次都达到上述量，则约25.6元。
这些是假设用量的算式，不是实测平均值、固定任务单价或供应商收费保证。
首轮先不充值，建议授权的本地预估预算为2元/最多12次请求；验证后再决定扩大。
首批失败不能删除后只保留“跑通的”：保留失败类型、usage unknown 和全部物理请求预留。

## 已准备的命令

默认免费 dry-run（已在本机验证，无网络请求）：

```powershell
.\.venv\Scripts\python.exe scripts/pilot_deepseek.py
```

用户在本地 `.env` 配置密钥，不能发聊天；账户只读检查：

```powershell
.\.venv\Scripts\python.exe scripts/check_deepseek.py
```

仅在明确同意预算后，本地设置以下配置；不要在日志/截图泄露密钥：

```dotenv
SHOPRESOLVE_PROVIDER=deepseek
SHOPRESOLVE_MODEL=deepseek-flash
SHOPRESOLVE_MODEL_BASE_URL=https://api.deepseek.com
SHOPRESOLVE_MODEL_API_KEY=仅在本地填写
SHOPRESOLVE_ALLOW_PAID_MODEL=true
SHOPRESOLVE_MODEL_MAX_OUTPUT_TOKENS=1500
```

显式付费执行入口（尚未运行）：

```powershell
.\.venv\Scripts\python.exe scripts/pilot_deepseek.py --execute --max-calls 12 --max-cost-cny 2
```

批次只测试查单、政策咨询、批准后模拟取消三个场景；各使用独立临时 SQLite 状态。
预期结果只在评测器中，不放进 Agent 上下文。共享调用/预估费用预算耗尽即停止新请求。
每次请求先把保守费用预留写入本地 SQLite ledger；响应丢失不退还预留。
这是本地预估保护，不是官方账户硬扣费上限，字节估算也不是精确 tokenizer。
进程被强杀时完整结果报告可能尚未生成，但尝试预留仍保留。新批次不会覆盖旧证据。
报告存 ignored `.cache/evals`，不自动公开模型轨迹。此 smoke 不是最终面试准确率。

## 正式评测数据和指标

1. 当前自建20条是工程回归；16条检索问题是小型合成草案。现有100%不能写成LLM成功率。
2. 新增独立审阅的开发/冻结测试场景，覆盖多意图、缺信息、拒批、过期政策、越权、提示注入、状态变化等。
   按场景/政策模板切分，不只换订单编号；预期状态与攻击标签不进入 Agent。
3. 每任务检查目标订单状态、金额、指定 operation、批准/政策约束，以及不应变化的他人订单/账本哨兵。
   当前三场景 smoke 已检查目标/哨兵订单、精确模拟退款金额、operation 数量和批准主体；
   仍不等于完整正式 grader：多意图、逐声明证据支持、全部业务不变量和独立审阅仍待补。
4. 每个冻结任务多次独立运行，报告任务成功率、违规执行数/机会数、重复副作用、证据支持率、
   P50/P95、token和每成功任务费用；样本量和置信区间必须同时给出。检索消融单列。
5. 公开基准优先考虑 [τ-bench retail](https://github.com/sierra-research/tau-bench) /
   [τ²-bench](https://github.com/sierra-research/tau2-bench)。固定 commit、许可、任务和协议后才能运行。
   原始环境的工具/政策与本项目不一致，改编题只能称“参考/改编评测”，不得冒称官方榜单成绩。
   用户模拟器也可能产生费用，不直接开全量多trial。

DeepSeek 可作为首个真实模型，不必为此租 GPU。它的得分只代表该模型、该协议和本项目环境，
不是整个 Harness 的普遍安全性证明；故障恢复仍需真实 MySQL/Redis/进程实验独立验证。
