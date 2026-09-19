# M8 服务器部署与验收

**状态：`SERVER_READY`（2026-09-19）。** 服务器端发布、真实任务、持久化、备份恢复和跨版本回滚均已通过。由于当前使用 IP 内部 CA，普通浏览器首次访问前需在演示机信任随附根证书。

## 发布身份

- 源提交：`63fbc994f468697ff19def82c6c9bca0512b5ab0`
- 发布标签：`v0.1.0-rc9`
- main CI：[`35415662310`](https://github.com/Kennylin-NotFound/research-copilot-agent/actions/runs/35415662310)
- 发布与部署：[`35415767475`](https://github.com/Kennylin-NotFound/research-copilot-agent/actions/runs/35415767475)
- 回滚演练：[`35429382917`](https://github.com/Kennylin-NotFound/research-copilot-agent/actions/runs/35429382917)
- 私有仓库：[`Kennylin-NotFound/research-copilot-agent`](https://github.com/Kennylin-NotFound/research-copilot-agent)
- TCR 生产镜像：`ccr.ccs.tencentyun.com/chinitsu/research-copilot-agent@sha256:8eabe5f1e964c0662ef3058c03031cdce18d756b803c597b5a295e7e2ac86684`
- schema：8 个迁移，最新为 `0008_observability_feedback.sql`

## 实际环境

- OpenCloudOS 9.4、x86_64、4 vCPU、约 3.6 GiB 内存、1 GiB swap。
- Docker 28.0.1、Compose 2.32.1；根盘约 40 GiB。
- 页面入口：`https://101.43.120.39`。Caddy 在 443 提供内部 CA 签发的 IP HTTPS；API 诊断端口只绑定 `127.0.0.1:18081`。
- 交付 CA 文件 SHA-256：`B683A769B422046BFEA171EC5F6832A14FE47F5267B4FB3A7F1ABFF1B527A1CA`。
- 宿主 80 已被既有 nginx 使用，产品 HTTP 入口映射到 18080 且未作为公网入口。当前以 443 HTTPS 为唯一演示入口。

## CI/CD 结果

- main push/PR 执行 PostgreSQL + pgvector 下的 80 项产品测试、Python 编译和前端 JavaScript 语法检查。
- `v*` tag 从同一提交生成 GHCR amd64/arm64 归档与 TCR amd64 生产镜像；生产按 TCR 精确 digest 部署。
- SSH 使用专用密钥、固定 host key、BatchMode 和 keepalive；Registry 凭据短时使用，部署后 logout。
- release bundle 以提交 SHA 固定，部署前成对备份数据库与文件卷；内部 health 和 GitHub Runner 公网 health 都严格验证生产模式与版本。
- GitHub 个人免费账号的私有仓库不能启用 branch protection，也不支持平台级 Artifact Attestation。固定 Action SHA、80 项 CI、GHCR OCI SBOM/provenance 与 digest 部署仍生效。

## K8 验收

| 项目 | 结果 | 证据 |
|---|---|---|
| 严格 HTTPS、版本、安全头与 GUI 静态资源 | 通过 | 导出 CA 严格校验；首页与 5 个 JS/CSS 资源均为 200，未使用 `-k` |
| 生产账号与 Secure Session | 通过 | 持久账号 `kenny`；密码只在本机 Git 忽略文件，生产禁用弱演示密码 |
| 普通对话与 Trace | 通过 | Run `b61cca06-a5f3-48bc-a95e-fc7c15adb90d`，Trace/Span/Event/模型/费用状态可查 |
| 联网论文检索与 Tool Action | 通过 | Run `5dc11f48-982a-4410-bf52-a5ed24f79a74`，3 个 web sources、1 个 tool action |
| RAG 与引用 | 通过 | Run `f4eb4feb-c35e-4bbf-84d3-6946c72cb5c4`，3 条本地原文引用 |
| 五种工作方式与版本化成果 | 通过 | 普通对话、检索、证据问答、单篇评议和多篇综述全部 completed；后两者各 3 个成果版本 |
| 100 页文件边界 | 通过 | PDF `c5826cd6-b4d8-4445-aac5-94efe81cf26b`，100 页、46,114 bytes、ready |
| 重启与持久化 | 通过 | 四容器停止/启动后账号、项目、会话、5 个 Run 与 2 个 ready 文件保留 |
| 成对备份与隔离恢复 | 通过 | 8 个迁移、17 个 Run、85 个 Span、24/24 blob hash 一致、0 orphan FileVersion |
| 资源水位 | 通过 | 约 5 分钟、68 组样本；API/worker/db/proxy 峰值内存 75.35/106.1/69.08/14.69 MiB |
| GitHub 跨版本回滚 | 通过 | rc9 → rc8 工作流成功并保留业务数据；随后从 rc9 具体 release 恢复，数据再次通过 |

机器证据位于 `artifacts/product/M8/`：`acceptance.json`、`server_acceptance.json`、`public_gui_assets.json`、`resource_stats.json`、`restart_persistence.json`、`backup_restore.log`、`after_github_rollback_rc8.json` 和 `after_restore_rc9.json`。

## 失败、修复与边界

- rc6 的内部 health 使用 `Host: 127.0.0.1`，被 TrustedHost 正确拒绝；worker 还可能早于 API 迁移。rc7 为 health 设置公网 Host，并让 worker 在读任务表前运行 advisory-lock 迁移。
- rc7 经 `current` 符号链接做同版本部署时形成自引用。运行容器和数据未中断；rc8 改为解析 canonical release、限制 release 根目录并使用 `ln -sfnT`，Linux 回归通过。
- rc9 为回滚增加最长 120 秒的 health 等待。真实 GitHub 回滚和恢复均通过，不通过删除或隐藏失败记录实现。
- 外部搜索内容只作为候选论文发现信息，`web_sources` 不冒充已上传原文引用。缺少原文时评议/综述会保留 evidence gap。
- 自动验收已覆盖公网 TLS、页面和静态资源、API、SSE/任务、Trace、RAG、持久化和恢复；未修改本机系统根信任，因此没有把公网 IP 的普通浏览器视觉访问写成自动 GUI 通过。导入根证书后可直接使用网页。
- 这是单机、单数据库、单 worker 的个人产品部署；它不证明高可用、多人规模或长期 SLO。当前 SSH 部署用户为 root，迁移到低权限 deploy 用户是后续加固项。
