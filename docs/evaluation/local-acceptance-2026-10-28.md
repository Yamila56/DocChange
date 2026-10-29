# 本地验收启动记录（2026-10-28）

结论：局部回归通过，完整浏览器/基础设施联调被 Docker Desktop 启动故障阻塞，未完成验收。

## 实际执行

- 配置只输出布尔状态：provider=demo、enable_demo=true、key_configured=false、allow_paid_model=false；没有展示 `.env` 或凭证。
- `scripts/pilot_deepseek.py` 默认 dry-run 通过，明确 NO API requests。
- 专项 pytest：test_api.py、test_runs.py、test_business_readiness.py、test_memory_summary.py、test_gateway.py、test_pilot_budget.py。
- 结果：60 passed、1 skipped，31.90 秒。唯一跳过：`test_mysql_two_workers_cannot_claim_same_run`，原因 Requires MySQL locks。本次为隔离 SQLite 回归，不能替代 MySQL/Redis/Docker 验收。
- JUnit：`local-acceptance-2026-10-28.xml`。不与历史153项累计。
- 已启动本地 API（127.0.0.1:8000），进程显式强制 demo、禁止付费；没有改 `.env`。
- 实际浏览器打开现有 React 构建：登录页标题、账号选择、本地演示码和进入按钮可见；没有执行有效登录或声称登录后工作区验收通过。
- HTTP：`/`=200、`/health/live`=200、`/health/ready`=503。
- 本地截图：`.cache/acceptance-20261028/login.png`；API PID及日志在同目录。截图/临时进程信息不提交。

## 阻塞及保留边界

Docker Desktop 报错：初始化 Ingest server 时无法访问自身 `sailor-ingest.sock`，Linux engine named pipe 不存在。尝试启动与重启 Docker Desktop 进程后，`docker ps` 仍失败。未删除 socket，未重置 Docker、WSL、数据库、容器、镜像或卷，未上传诊断。

该故障发生在应用连接基础设施之前，不能据此判定项目业务代码失败。当前也不能据历史通过记录判定本次全栈通过。

## 恢复后的验收顺序

1. Docker engine 恢复后检查既有项目 MySQL/Redis 与 sandbox 镜像；只启动本项目所需服务，保留数据。
2. 重跑 Agent HTTP 与 sandbox HTTP smoke。
3. 浏览器验证登录、查单、只读政策咨询、补问、提案拒绝/批准、结果状态、SSE事件、刷新恢复和CSV上传计算；会修改订单的案例使用隔离测试数据。
4. 用户本地配置完整 Key 后只读余额/模型预检；真实模型 completion 仍需明确费用预算和本地 opt-in，不沿用联调请求推定付费授权。

完整业务流程、Redis调度、沙箱计算、真实模型表现均仍待本轮实际运行。

## 后续账户只读预检

用户随后将完整密钥保存到项目 `.env`，程序已正确读取，无星号遮蔽标记。实际执行 `scripts/check_deepseek.py` 成功：账户可用，模型列表包含 `deepseek-flash`、`deepseek-v4-pro`。余额和密钥不写入报告。该验证只涉及余额与模型列表接口，不证明 completion 或本项目协议已联调通过；没有产生生成调用，默认provider=demo、付费开关=false。现有pilot脚本仅支持Flash，Pro费用预留需单独调整。
