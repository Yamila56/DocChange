# M2–M5 本地验证记录（开发中）

日期：2026-10-27。不是完整项目完成声明。

- MySQL + Redis + Docker 可用的本地全套：87 passed，175.37 秒，见 mysql-regression-latest.xml。
- 未设置 MySQL 测试地址的本地集：81 passed、6 skipped，51.40 秒，见 local-regression.xml。跳过项均是 MySQL 特定测试，不计为通过。
- 两个真实子进程硬退出点：事务提交前、业务提交后/检查点前，恢复后各仅一个退款账本效果。
- 独立多进程 API/outbox/Redis/Worker/SSE smoke 通过；前端静态入口 HTTP 可用。
- 独立 API 上传/manager 内部认证/真实 Docker 计算 smoke 通过，预期金额 44800 分。
- Docker runner 五项测试通过：计算及同 ID 重放、网络/凭证/宿主隔离、墙钟超时、符号链接输出拒绝、路径穿越拒绝。
- TypeScript 编译、Vite 构建、Ruff 通过。未完成浏览器视觉交互 QA；CI 尚未远程执行。

沙箱调试发现并修复：local 日志驱动的压缩/max-file 配置冲突；Docker cp 无法按预期收集该 tmpfs 内容。最终方案由可信 supervisor 保持容器存活，经容器内 tar 导出，宿主仅解析受大小限制的普通文件，不提取任意路径。容器在结果收集后按精确名称与 label 校验清理。

工程回归集 20/20 通过、额外账本案例 0，但 controller 是 deterministic demo，不是真实模型。神经检索实验用实际公开 ONNX 权重，具体百分比及延迟见配套检索报告。

待办包括真实模型评测、CSV 与 Agent 工具整合、物流工单/替代商品、更多资源/重启/恢复测试和发布 QA。不能把现有通过率称作普遍“生产级 100% 可靠”。最后两项守卫补丁后的结果以最新 PROGRESS 为准。
