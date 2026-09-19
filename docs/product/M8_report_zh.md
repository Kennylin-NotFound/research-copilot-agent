# M8 服务器部署阶段报告

## 结论

Research Copilot 已完成实际云服务器部署和 GitHub CI/CD 闭环，并于 2026-09-19 签发 `SERVER_READY`。生产运行 `v0.1.0-rc9`，镜像由 TCR 精确 digest 固定；真实任务、重启持久化、成对备份/隔离恢复、资源采样以及 rc9 → rc8 → rc9 跨版本回滚恢复均已通过。

## 完成内容

- 私有 GitHub 仓库、main push/PR CI、tag release、GHCR amd64/arm64 归档、TCR amd64 生产镜像、SSH CD 和手工回滚工作流。
- OpenCloudOS 9.4 服务器运行 PostgreSQL/pgvector、FastAPI、独立 worker 与 Caddy；应用镜像按 digest 部署。
- IP HTTPS 使用 Caddy 内部 CA；GitHub Runner 和本机验收均使用导出 CA 严格校验，没有跳过证书验证。
- 生产账号 `kenny` 已持久化；强密码仅在本机 Git 忽略文件保存。
- 外网真实任务覆盖普通研究对话、联网论文检索、资料证据问答、单篇论文评议和多篇证据综述。
- 100 页 PDF 与项目笔记完成解析、embedding 和索引；证据问答生成 3 条原文引用，评议与综述各生成 3 个版本化成果。
- Run 可查 Trace、Span、Action、Event、模型、费用状态与直观 display name。
- 产品四容器完整停止/启动后，账号、项目、会话、Run、文件与索引仍在。
- 数据库和文件卷成对备份并恢复到隔离 Compose project；24/24 blob hash 一致，孤立 FileVersion 为 0。
- 约 5 分钟、68 组采样覆盖文件索引和五种工作方式；所有容器峰值内存都低于各自限制的 12%。
- GitHub 手工工作流把 rc9 回滚到 rc8，严格 TLS 与业务数据通过；随后用保存的 rc9 release/digest 恢复并再次验证同一批数据。

## 最终发布

- 源提交：`63fbc994f468697ff19def82c6c9bca0512b5ab0`
- 标签：`v0.1.0-rc9`
- TCR digest：`sha256:8eabe5f1e964c0662ef3058c03031cdce18d756b803c597b5a295e7e2ac86684`
- release run：[`35415767475`](https://github.com/Kennylin-NotFound/research-copilot-agent/actions/runs/35415767475)
- rollback run：[`35429382917`](https://github.com/Kennylin-NotFound/research-copilot-agent/actions/runs/35429382917)
- 入口：`https://101.43.120.39`

## 失败、根因与修复

1. rc3 平台级 Artifact Attestation 失败：个人免费账号的私有仓库不支持该 API。改为只在公开仓库调用，私有仓库仍保留 GHCR OCI SBOM/provenance。
2. rc4 服务器拉 GHCR 过慢：保留 GHCR 归档，增加腾讯云 TCR 生产镜像，服务器按 TCR digest 拉取。
3. rc6 API 实际启动但 health 返回 400：内部请求使用 `Host: 127.0.0.1`，被 TrustedHost 白名单拒绝。rc7 对所有 health 显式设置公网 Host。
4. rc6 SSH 在 health 等待期间断开：CD 配置 20 秒 keepalive、15 秒连接超时，并输出等待进度。
5. worker 首启曾早于 API 完成迁移：worker 启动先运行相同的 advisory-lock 幂等迁移。
6. 第一轮线上验收脚本错误要求论文检索产生 `references`：产品正确把外部搜索摘要放进 `web_sources` 并保留 `references=[]`。修正验收断言，保留 discovery-not-evidence 边界。
7. 合成容量 PDF 只有占位句，证据问答正确返回 evidence gap。随后把容量边界和引用质量分开验收，用明确机制笔记取得 3 条可核验引用。
8. rc7 同版本重部署从 `current` 符号链接调用脚本时形成自引用。运行容器和数据未丢失；立即恢复链接和 state，rc8 使用 canonical release 路径、release 根边界和 `ln -sfnT`，并增加 Linux 符号链接回归。
9. 回滚脚本原先在容器启动后立即检查一次 health。rc9 改为最长 60 次、每次间隔 2 秒，并在失败时输出日志且不切换 state。真实 workflow 在 39 秒内完成。

## 证据边界与剩余加固

- 这是单机、单数据库、单 worker 的个人产品部署，不代表高可用集群或多人规模证明。
- 实测服务器是 4 vCPU/约 3.6 GiB 的 OpenCloudOS 9.4，不是早期假设的 2 vCPU Ubuntu。
- 暂无域名。服务器端 TLS 已严格验收；浏览器演示机仍需信任随附内部 CA。取得域名后切换公开 ACME。
- 真实模型结果仍属于小样本。M8 证明环境和链路可用，不外推准确率或长期 SLO。
- GitHub 个人免费账号的私有仓库无法启用 branch protection；当前使用固定 Action SHA、CI、不可变 tag/digest 和保留失败记录降低风险。
- 当前 SSH 部署用户为 root；迁移到低权限 deploy 用户是安全加固项。
