# 生产部署说明

本说明用于单台 2 核 4 GB Linux 服务器。宿主发行版不要求 Ubuntu；运行边界是 Docker Engine、Compose v2、`curl`、`tar` 和 `sha256sum`。先执行只读探测，再按 Docker 官方文档安装与实际发行版匹配的 Engine。

## 1. 部署结构

```text
Internet :80/:443
        │
   Caddy (自动 TLS)
        │ internal Docker network
   FastAPI API ─ PostgreSQL + pgvector
        │                 │
      worker ─────────────┘
```

- Caddy 终止 TLS 并把 SSE 低延迟转发到 API；域名模式使用公开 ACME 证书，临时 IP 模式使用 Caddy 内部 CA。
- API 仍只在宿主回环地址暴露诊断端口，外部访问只经过 Caddy。
- `.env.production`、数据库卷、文件卷和 Caddy 证书卷只存在服务器。
- Actions 从同一提交并行发布 GHCR 多架构归档和地域内 TCR amd64 生产镜像，服务器从 TCR 使用不可变 digest 部署；服务器不从 Git 工作区现场构建。

## 2. 服务器探测

将 `scripts/server_probe.sh` 复制到服务器并运行：

```bash
bash /tmp/server_probe.sh
```

记录 OS ID/版本、CPU 架构、内存、根盘空间、Docker/Compose 版本、sudo 能力，以及 80、443、18081 端口占用。不得把 SSH 私钥或服务器密码写入报告。

Docker 尚未安装时，根据探测到的发行版使用 [Docker Engine 官方安装入口](https://docs.docker.com/engine/install/)。完成后验证：

```bash
docker version
docker compose version
```

## 3. 一次性服务器准备

服务器应有独立的普通部署用户。Docker 安装完成后执行：

```bash
sudo bash scripts/server_prepare.sh <deploy-user>
```

该脚本创建 `/opt/research-copilot/{releases,backups}` 并把部署用户加入 Docker 组。重新登录后验证该用户无需 sudo 即可执行 `docker ps`。

安全组和主机防火墙必须允许 TCP 80/443；若启用 HTTP/3，可同时允许 UDP 443。有域名时，A/AAAA 记录必须指向服务器，Caddy 会自动申请公开证书，前提见 [Caddy 官方说明](https://caddyserver.com/docs/automatic-https)。

没有域名时设置 `PRODUCT_PUBLIC_HOST=<公网 IP>` 与 `PRODUCT_CADDYFILE_PATH=./deploy/Caddyfile.ip`。该模式仍使用 HTTPS 和 Secure Cookie，但证书由 Caddy 内部 CA 签发：流水线导出其公开根证书并严格校验，浏览器首次访问需显式信任该根证书。获得域名后把路径切回 `./deploy/Caddyfile`，即可使用公开信任证书。

## 4. 服务器秘密配置

在服务器复制模板并限制权限：

```bash
install -m 0600 .env.production.example /opt/research-copilot/.env.production
editor /opt/research-copilot/.env.production
```

必须设置：

- `PRODUCT_PUBLIC_HOST`：已经解析到服务器的域名，或临时部署使用的公网 IP；不带协议或路径。
- `PRODUCT_ALLOWED_HOSTS`：通常与公网入口一致。
- `PRODUCT_CADDYFILE_PATH`：域名模式为 `./deploy/Caddyfile`，IP 模式为 `./deploy/Caddyfile.ip`。
- `ACME_EMAIL`：证书通知邮箱。
- `PRODUCT_DB_PASSWORD`：随机长密码。
- DeepSeek、Embedding 和 Tavily 密钥。

`PRODUCT_IMAGE` 与 `PRODUCT_APP_VERSION` 在 CD 时由不可变 digest 和 Git tag 覆盖；模板值只用于 Compose 配置解析。生产秘密不进入 GitHub Actions，GitHub 只保存 SSH 部署凭据。

## 5. GitHub CD 配置

完整配置见 [GitHub CI/CD](github_cicd.md)。仓库需要以下 Actions repository secrets：

- `DEPLOY_HOST`
- `DEPLOY_PORT`
- `DEPLOY_USER`
- `DEPLOY_SSH_PRIVATE_KEY`
- `DEPLOY_KNOWN_HOSTS`

并创建 repository variables：

- `PRODUCTION_URL=https://<PRODUCT_PUBLIC_HOST>`
- `PRODUCTION_TLS_MODE=public`（公开域名证书）或 `internal`（临时 IP 内部 CA）

部署密钥应单独生成、无口令、仅安装到部署用户，不复用个人长期 SSH 私钥。`DEPLOY_KNOWN_HOSTS` 必须来自已通过云控制台或服务器提供商核对的主机指纹。

## 6. 发布与自动部署

合并并验证默认分支后创建版本 tag：

```bash
git tag -a v0.1.0-rc3 -m "Research Copilot v0.1.0-rc3"
git push origin v0.1.0-rc3
```

`release-and-deploy` 依次执行：

1. PostgreSQL 环境中的 80 项产品测试和语法检查。
2. 并行构建 GHCR amd64/arm64 OCI 归档（含 SBOM/provenance）和 TCR amd64 生产镜像。
3. 通过固定 SSH host key 上传部署 bundle。
4. 部署前拒绝未完成任务，暂停 API/worker，成对备份数据库与文件卷。
5. 拉取精确镜像 digest，启动数据库、API、worker 和 Caddy。
6. 检查容器内 production health，再从 GitHub runner 验证公网 HTTPS、安全头和版本。
7. 清除服务器上的短期 registry 凭据。

## 7. 创建生产账号

首次部署成功后，在服务器中输入强密码；密码不会进入参数或 shell 历史：

```bash
cd /opt/research-copilot/current
read -s PRODUCT_USER_PASSWORD
printf '%s\n' "$PRODUCT_USER_PASSWORD" | docker compose \
  --env-file /opt/research-copilot/.env.production \
  -f compose.production.yaml exec -T api \
  python -m product.manage_user kenny --password-stdin
unset PRODUCT_USER_PASSWORD
```

生产环境不得使用 `--allow-weak-demo-password`。

## 8. 备份、恢复与回滚

部署前备份保存在 `/opt/research-copilot/backups/<UTC timestamp>/`，包含：

- `copilot.dump`
- `product_storage.tgz`
- `SHA256SUMS`
- 不含秘密的版本清单

手动备份：

```bash
DEPLOY_ROOT=/opt/research-copilot bash /opt/research-copilot/current/scripts/server_backup.sh
```

从某次备份恢复到隔离 Compose project 并自动核对数据库、孤立版本和全部存储文件哈希：

```bash
DEPLOY_ROOT=/opt/research-copilot bash /opt/research-copilot/current/scripts/server_restore_drill.sh \
  /opt/research-copilot/backups/<UTC timestamp>
```

演练结束会删除隔离容器和临时卷，不修改当前生产卷；备份目录中保留恢复后的文件哈希清单作为证据。

自动部署健康失败时会尝试恢复上一镜像。也可以在 GitHub Actions 手动运行 `rollback-production`，并输入 `ROLLBACK`。数据库迁移只向前执行；需要数据库结构回退时，必须把同一时刻的数据库 dump 与文件卷一起恢复。

## 9. SERVER_READY 验收

- HTTPS 证书链、HTTP→HTTPS、Secure Cookie、Host allowlist 和登录限流；IP 模式用固定的内部 CA 验证，不用 `curl -k`。
- 外部浏览器完成登录、对话、文件、五种工作方式、RAG、成果与 Trace。
- 反向代理后的 SSE 重连、取消、长任务与错误恢复。
- API/worker/数据库/Caddy 重启后账号、Session、文件和 Run 持久。
- 备份恢复到隔离 Compose project 后，数据库数量、孤立 FileVersion 和 blob hash 一致。
- 2 核 4 GB 下完成最大允许文件和一个活跃任务，记录 CPU、内存、磁盘和延迟。
- 线上 `/health` 版本等于发布 tag，运行镜像等于 Actions 保存的 digest。
