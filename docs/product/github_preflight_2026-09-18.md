# GitHub 发布前验收报告

## 结论

Research Copilot 已达到“可创建私有 GitHub 仓库”的本机发布前门槛。当前没有配置 remote，也没有上传代码。服务器部署尚未开始。

- 分支：`productization/v0.1.0`
- 产品化源码提交：`e18d0c9`
- 回退标签：`m7-local-ready`
- 本机候选镜像：`research-copilot-agent:v0.1.0-rc2`
- 镜像 digest：`sha256:5434ed66c2894dbd3c58c4ac2332c652d36a437b09282ec3cf5e2b3f27a56254`

## 已完成

### 产品界面与 Skill

- 删除网页中的阶段编号和“本机开发版”文案。
- 工作方式改为“研究对话 / 联网论文检索 / 资料证据问答 / 单篇论文评议 / 多篇证据综述”。
- 右侧运行记录使用“Skill 名称 + 回答摘要”，完整 Run ID 和 Trace ID 仍在详情中。
- “查看运行记录”只显示在 Agent 回答下；右侧记录与中间回答可双向定位。

### 联网论文检索

- 新增独立 `paper-search` Skill；请求必须显式设置联网授权。
- Tavily 为当前主检索服务，Semantic Scholar 为服务端回退。
- URL 限定为 HTTPS 学术域名；网页使用 `textContent` 渲染外部标题和摘要。
- 搜索结果明确标为候选元数据，不作为论文结论证据；用户需上传原文后再使用证据问答或评议。
- 真实 Tavily 直接探针返回 3 条 OpenReview/arXiv 结果；候选镜像端到端运行返回 5 条，Trace 包含 `search_papers` Span 和 Action。

### 费用

- 价格源为 [DeepSeek 官方 Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing/)，核对日期为 2026-09-18。
- 支持 V4 Pro、Flash 和旧 Flash 名称映射，按工作日 UTC 峰谷时段估算。
- 优先使用接口返回的缓存命中/未命中 token；缺少拆分时按缓存未命中保守估算。
- 页面显示 LLM token 费用范围、价格日期和来源，不把 Embedding 或搜索服务费用算入 LLM 费用。
- 候选镜像真实 Flash 调用为 241 tokens，费用状态 `estimated`，验证时估算为 `$0.0001083`。

### 账号与持久化

- 新增 `python -m product.manage_user`，密码从 stdin 读取。
- 默认密码策略为至少 10 位；本机演示短密码必须显式使用 `--allow-weak-demo-password`。
- `kenny` 已写入开发数据库和本机发布候选数据库；明文密码未写入 Git、文档或运行配置。
- 更新用户保留 owner ID、项目和文件，并撤销旧 Session。
- 开发服务重启后登录成功；候选容器重启后账号和已有 Session 均保持有效。

### 发布工程

- `README.md` 已改为网页产品主线。
- `compose.production.yaml` 强制 live 模式、Secure Cookie、关闭网页 setup、显式 Host allowlist，并按 2 核 4 GB 单机给出资源上限。
- `.env.production.example` 只含占位值；生产秘密文件被 Git 和 Docker build context 排除。
- 新增生产部署、仓库边界、Security 和 GitHub Actions 产品测试配置。
- 本机 release Compose 与生产 Compose 均通过 `docker compose config --quiet`。
- staged secret gate 通过：34 个产品化文件没有包含当前配置的密钥、密码、私有数据库 URL 或运行目录。

## 验证结果

| 检查 | 结果 |
|---|---|
| 产品测试 | 80/80 通过 |
| Python compileall | 通过 |
| `app.js` / `rag.js` 语法 | 通过 |
| Git diff whitespace | 通过 |
| Compose release / production 配置 | 通过 |
| 真实 Tavily | 通过，无回退 |
| 容器真实论文搜索 | 完成，5 条来源，Trace/Action 完整 |
| 容器真实 LLM 费用 | `estimated` |
| dev/release 账号持久化 | 通过 |
| 候选镜像健康 | `version=v0.1.0-rc2`, `mode=live` |

## 事实边界与剩余事项

- 用户此前已完成 GUI 基本流程观察。本轮自动 GUI 复核受工具限制：内置浏览器禁止访问回环地址，Chrome 控制接口未暴露；因此没有把本轮标记为自动浏览器通过。静态页面、真实 API、服务端 HTML 和容器链路均已复核。
- GitHub Actions 文件只完成本地 YAML/依赖合同检查；必须在首次 push 后由 GitHub runner 实际执行。
- 生产 Compose 只完成本机配置验证，尚未在 Ubuntu 服务器验证 TLS、反向代理、SSE、重启、备份恢复和资源水位。
- 初次 GitHub 发布建议使用私有仓库。公开发布前需另行决定许可证，并审计完整 Git 历史和早期 CLI/评估资产是否适合公开。
- 当前仓库没有 remote；未收集、保存或使用 GitHub 账号与 token。

## 下一步

用户决定 GitHub 仓库名称和可见性后，创建 remote、推送当前分支、观察 GitHub Actions，再决定是否合并为默认分支。服务器部署必须使用已推送的不可变 tag 或镜像 digest。
