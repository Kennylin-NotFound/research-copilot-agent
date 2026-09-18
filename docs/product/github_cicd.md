# GitHub CI/CD

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
test → publish GHCR digest + SBOM/provenance → SSH deploy → public HTTPS smoke
```

发布 Job 使用 GitHub 自动生成的 `GITHUB_TOKEN` 写入当前仓库关联的 GHCR 包。部署 Job 只获得 `packages: read`，通过 SSH 把该次短期 token 输送给 `docker login --password-stdin`，拉取完成后立即 logout。

服务器保留 `.env.production`；工作流不接触 LLM、Embedding、搜索或数据库密码。

### `rollback-production`

手动触发并输入 `ROLLBACK` 后恢复 `previous.env` 指向的镜像和 release bundle，再验证公网生产 health。生产部署与回滚共用 concurrency group，不会同时修改服务器。

## GitHub 初始设置

1. 创建私有仓库并推送默认分支，先不要推送发布 tag。
2. 等待 `product-tests` 首次成功。
3. 在 `Settings → Secrets and variables → Actions` 添加五个 repository secrets：
   - `DEPLOY_HOST`
   - `DEPLOY_PORT`
   - `DEPLOY_USER`
   - `DEPLOY_SSH_PRIVATE_KEY`
   - `DEPLOY_KNOWN_HOSTS`
4. 在 Variables 添加 `PRODUCTION_URL`（必须是 HTTPS 根地址）和 `PRODUCTION_TLS_MODE`（`public` 或 `internal`）。
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
- Compose 收到的是 `ghcr.io/<owner>/<repo>@sha256:<digest>`，不依赖可移动 tag。
- 每次部署前保留数据库与文件卷成对备份，并保存当前/上一个镜像状态。
- 应用健康失败自动回到上一镜像；公网 smoke 失败会使 Actions 标红，需要检查 Caddy/DNS/TLS 后决定重跑或回滚。
- 涉及数据库不兼容变更时，不用代码回滚代替数据恢复。
