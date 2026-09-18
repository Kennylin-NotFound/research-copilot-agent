# 生产部署说明

本说明用于 2 核 4 GB Ubuntu 单机部署。GitHub 发布前只验证配置合同与本机镜像；真正服务器部署需在取得域名、TLS 和 SSH 条件后执行。

## 文件与版本

- 服务器只拉取已审查的 Git tag 或不可变镜像 digest。
- 复制 `.env.production.example` 为未跟踪的 `.env.production`，填入随机数据库密码、域名、模型、Embedding 和搜索服务密钥。
- `compose.production.yaml` 不允许网页初始化用户。部署后用受控命令创建首个账号。
- 数据库存放业务元数据；`product_storage` 存放上传文件和生成成果。二者必须成对备份。

## 配置检查

```bash
cp .env.production.example .env.production
# 编辑 .env.production；不要提交或发送到聊天
docker compose --env-file .env.production -f compose.production.yaml config --quiet
```

生产配置在应用启动时强制检查：

- `PRODUCT_MODE=live`
- `PRODUCT_COOKIE_SECURE=true`
- `PRODUCT_ALLOW_SETUP=false`
- `PRODUCT_ALLOWED_HOSTS` 为显式域名且不能使用 `*`

## 启动

```bash
docker compose --env-file .env.production -f compose.production.yaml up -d
docker compose --env-file .env.production -f compose.production.yaml ps
curl -fsS http://127.0.0.1:18081/health
```

API 只绑定到服务器回环地址。Nginx 或 Caddy 在同机终止 TLS，并反向代理到 `127.0.0.1:18081`。SSE 路由应关闭响应缓冲并保留长连接。

## 创建首个用户

密码通过标准输入传入临时容器，不写入 Compose、Git 或 shell 参数：

```bash
read -s PRODUCT_USER_PASSWORD
printf '%s\n' "$PRODUCT_USER_PASSWORD" | docker compose --env-file .env.production -f compose.production.yaml run --rm --no-deps api \
  python -m product.manage_user <username> --password-stdin
unset PRODUCT_USER_PASSWORD
```

生产环境不要使用 `--allow-weak-demo-password`。

## 备份与恢复

1. 确认没有处于 `running` 或 `cancelling` 的 Run。
2. 使用 `pg_dump -Fc` 备份 PostgreSQL。
3. 归档 `product_storage` 卷。
4. 为两份文件记录 SHA-256 和应用镜像 digest。
5. 恢复时新建 Compose project 和空卷，先恢复数据库，再恢复文件卷，最后核对文件版本数、孤立版本数和 blob 哈希。

数据库迁移只向前执行，不提供自动 downgrade。代码回滚使用旧镜像；涉及数据结构回退时，恢复同一时间点的数据库与文件卷。

## 上线后验收

- HTTPS、Cookie、Host allowlist 和登录限流
- API、worker、数据库健康与重启恢复
- 上传、索引、RAG 引文回查、论文检索和 Trace 导出
- 失败重试、取消、错误代码与费用估算
- 数据库/文件卷备份恢复演练
- 2 核 4 GB 下的并发、延迟和内存水位
