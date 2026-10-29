# DocChange V0.1.0

2026-10-29。发布定位：可运行的本地开发版，适合先放私有GitHub仓库保存与展示源码。不代表公网服务已经部署。

主要功能：版本化Markdown工作区、owner隔离、BM25/精确搜索、Agent读取/补问/修改提案、差异与引用、审批拒绝/幂等发布、历史快照、持久任务与调用轨迹。旧commerce域保留。

本地快速入口：`python -m scripts.run_docchange`（项目虚拟环境）。真实模型入口加`--remote`，使用持久交互预算；`.env`与模型密钥不打包。

发布前本地结果：157 passed / 16 skipped，133.25秒；Ruff check/format通过；前端已构建。跳过项和已有环境依赖不等同通过，详见 `evaluation/docchange-release-local-2026-10-29.xml`。远程CI尚未运行。

真实模型验收：原始首轮2/4；补诊断定位相邻JSON读请求；增加受控read_many后两失败题复测2/2。14请求、保守费用0.111042元。工程fixture9/9属于免费控制器。详见交付报告，不能合并成独立4/4准确率。

## 发布前已处理

- 首页给出干净克隆后的安装/构建/启动步骤，主项目标识DocChange。
- 本地Key不在Git跟踪内容，缓存/数据库/模型忽略，发布包从Git提交生成。
- 修复历史分析脚本的格式门禁，不改变历史实验数据或评分。
- 保留原失败报告与功能边界，禁止把规划功能写成已完成。

## 不阻碍私有源码首版、后续再补

- 文档域真实MySQL/Redis与Docker检查联调，本轮仅本地SQLite等环境有证据。
- 更大规模异步索引、Dense/Rerank、Multi-Agent等只在有任务/性能证据时增加。
- 独立文档任务测试、对照与消融；当前不声称普遍的准确率提升。
- 若要公网服务，需要完整生产身份/会话与部署配置；GitHub Pages不能直接运行Python后端、数据库与Worker。

## 上传状态

本地首版提交、tag和zip将用于源码交付。当前仓库没有远程origin，GitHub连接器没有新建仓库接口，本机没有gh命令；本轮浏览器创建页未能打开，因此没有创建或上传远程仓库。已连接账号为Yamila56，但“账号可读”不等于“仓库已建立”。

创建名为DocChange的Private空仓库后（不初始化README），可以在本仓库执行：

```powershell
git remote add origin https://github.com/Yamila56/DocChange.git
git push -u origin HEAD
git push origin v0.1.0
```

这些是待执行命令，不是本轮已完成的远程操作。保持Private，后续公开需用户明确决定。
