# GitHub CI/CD

## 当前实跑状态

- 私有仓库：[`Kennylin-NotFound/research-copilot-agent`](https://github.com/Kennylin-NotFound/research-copilot-agent)。
- 当前生产标签 `v0.1.0-rc9`；release run [`35415767475`](https://github.com/Kennylin-NotFound/research-copilot-agent/actions/runs/35415767475) 已完成测试、GHCR/TCR 发布、SSH 部署和公网严格 HTTPS 检查。
- 手工回滚 run [`35429382917`](https://github.com/Kennylin-NotFound/research-copilot-agent/actions/runs/35429382917) 已实跑 rc9 → rc8；账号、项目、会话、Run 和文件保留。随后恢复 rc9 并再次验收。
- 个人免费账号的私有仓库当前无法启用 branch protection；该限制记录在验收报告中，未把它误写成已启用。

## 流水线

### `product-tests`

每次 push 和 pull request 执行：

- PostgreSQL + pgvector 服务
- 80 项产品测试
- Python 编译检查
- 前端 JavaScript 语法检查

同一 ref 的新运行会取消旧 CI，避免两套测试竞争同一 runner 资源。

### `release-and-deploy`

推送 `v*` tag 后执行：

```text
test → parallel publish (GHCR multi-arch + TCR amd64) → SSH deploy → public HTTPS smoke
```

发布阶段从同一提交并行生成两类制品：GHCR 保存 amd64/arm64 多架构镜像及 SBOM/provenance；腾讯云 TCR 只保存当前生产服务器需要的 amd64 镜像，以减少 GitHub Runner 到国内 Registry 的跨区上传量。生产服务器从地域内 TCR 按精确 digest 拉取；登录凭据通过 Secrets 和 `--password-stdin` 短时使用，拉取后立即 logout。

BuildKit 会把 SBOM 和 provenance 作为 OCI attestations 写入 GHCR。GitHub 平台级 Artifact Attestations 在公开仓库执行；个人免费账号的私有仓库不支持该 API，因此对应步骤会明确跳过，不影响 GHCR 内的 SBOM/provenance 或精确 digest 部署。

服务器保留 `.env.production`；工作流不接触 LLM、Embedding、搜索或数据库密码。

### `rollback-production`

手动触发并输入 `ROLLBACK` 后恢复 `previous.env` 指向的镜像和 release bundle，等待应用健康，再验证公网生产 health。生产部署与回滚共用 concurrency group，不会同时修改服务器；失败时不切换当前 state。

## GitHub 初始设置

1. 创建私有仓库并推送默认分支，先不要推送发布 tag。
2. 等待 `product-tests` 首次成功。
3. 在 `Settings → Secrets and variables → Actions` 添加以下 repository secrets：
   - `DEPLOY_HOST`
   - `DEPLOY_PORT`
   - `DEPLOY_USER`
   - `DEPLOY_SSH_PRIVATE_KEY`
   - `DEPLOY_KNOWN_HOSTS`
   - `TCR_USERNAME`
   - `TCR_PASSWORD`
4. 在 Variables 添加 `PRODUCTION_URL`（必须是 HTTPS 根地址）、`PRODUCTION_TLS_MODE`（`public` 或 `internal`）、`TCR_REGISTRY` 和 `TCR_IMAGE`。
5. 确保 Actions 可以写入 Packages。若组织策略限制 `GITHUB_TOKEN`，由仓库管理员允许 workflow 的 `packages: write`。
6. 可用时为默认分支启用保护：要求 `product-tests / test` 通过，禁止 force push 和删除。
7. 完成服务器准备、DNS 和 `.env.production` 后，再推送新版本 tag。

工作流引用的 GitHub/Docker Actions 固定到已核对的 commit SHA；升级 action 时先核对官方 tag 指向并通过本机 Actionlint，再单独提交。

私有个人仓库的 GitHub Environment 与 required reviewer 能力取决于账号方案，因此基础流水线只依赖所有私有仓库均可用的 repository secrets。版本 tag 本身是生产发布开关。

## SSH 密钥与 Host Key

生成专用 Ed25519 部署密钥：

```bash
ssh-keygen -t ed25519 -C research-copilot-github-actions -f research-copilot-deploy -N ""
```

- 公钥追加到服务器部署用户的 `~/.ssh/authorized_keys`。
- 私钥完整内容写入 `DEPLOY_SSH_PRIVATE_KEY`。
- 使用云控制台或提供商资料核对服务器 SSH host fingerprint 后，把对应 `known_hosts` 行写入 `DEPLOY_KNOWN_HOSTS`。
- 删除服务器授权即可立即撤销该流水线的 SSH 权限。

没有域名时使用 `PRODUCTION_TLS_MODE=internal`。部署工作流会从运行中的 Caddy 容器导出公开根证书，并通过 `curl --cacert` 验证公网 IP 的证书与健康状态；它不会用 `-k` 跳过验证。浏览器演示机需要单独信任该根证书，获得域名后切回 `public`。

## 版本与回滚规则

- 默认分支只代表已测试源码；生产只部署版本 tag 构建出的 digest。
- Compose 收到的是 `ccr.ccs.tencentyun.com/<namespace>/<repo>@sha256:<digest>`，不依赖可移动 tag；同一发布也保存在 GHCR。
- 每次部署前保留数据库与文件卷成对备份，并保存当前/上一个镜像状态。
- 应用健康失败自动回到上一镜像；公网 smoke 失败会使 Actions 标红，需要检查 Caddy/DNS/TLS 后决定重跑或回滚。
- 涉及数据库不兼容变更时，不用代码回滚代替数据恢复。
