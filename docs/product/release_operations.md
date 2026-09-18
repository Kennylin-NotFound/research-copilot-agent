# 本机发布候选运行与恢复

本文件只描述 M7 本机 Linux 发布候选。服务器部署属于 M8，必须使用 M7 冻结的同一源码和镜像配置。

## 配置

- `.env`：模型与 embedding 供应商配置，只保存在运行主机。
- `.local/dev.env`：至少包含 `PRODUCT_DB_PASSWORD`；不提交。
- Compose 在容器内固定 `PRODUCT_DATABASE_URL`、`PRODUCT_DATA_DIR`、cookie 和 host 边界，不读取主机开发库地址。

## 构建、启动与停止

```powershell
docker compose --env-file .local/dev.env -f compose.release.yaml config --quiet
docker compose --env-file .local/dev.env -f compose.release.yaml build api
docker compose --env-file .local/dev.env -f compose.release.yaml up -d
docker compose --env-file .local/dev.env -f compose.release.yaml ps
Invoke-RestMethod http://127.0.0.1:18081/health
```

API 启动时在 PostgreSQL advisory lock 内执行幂等迁移；任一已应用迁移的 hash 改变会拒绝启动。正常停止不删除卷：

```powershell
docker compose --env-file .local/dev.env -f compose.release.yaml stop
docker compose --env-file .local/dev.env -f compose.release.yaml start
```

查看最近日志时不要导出容器环境：

```powershell
docker compose --env-file .local/dev.env -f compose.release.yaml logs --tail 200 api worker db
```

## 备份

先确认没有正在发布结果的 Run。数据库用 custom-format `pg_dump`，文件卷单独归档；两者完成后生成 SHA-256。M7 实跑产物在 `artifacts/product/M7/backup/`，该目录被 Git 忽略。

```powershell
$backup = Join-Path (Resolve-Path '.').Path 'artifacts\product\M7\backup'
New-Item -ItemType Directory -Force -Path $backup | Out-Null
$db = (docker compose --env-file .local/dev.env -f compose.release.yaml ps -q db).Trim()
docker exec $db pg_dump -U copilot -d copilot -Fc -f /tmp/copilot.dump
docker cp "${db}:/tmp/copilot.dump" (Join-Path $backup 'copilot.dump')
$mount = $backup.Replace('\','/')
docker run --rm -v research-copilot-release_product_storage:/data:ro -v "${mount}:/backup" alpine:3.22 tar -czf /backup/storage.tgz -C /data .
Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $backup 'copilot.dump'),(Join-Path $backup 'storage.tgz')
```

## 恢复到新环境

恢复必须使用新的 Compose project 和新卷；不要对当前个人卷执行 `down -v`、`rm` 或覆盖式解压。先启动数据库并等待 healthy，再执行 `pg_restore`。M7 首次演练因未等待数据库 ready 而失败，随后加入健康等待并成功恢复；该失败已保留在验收证据中。

```powershell
$env:PRODUCT_COMPOSE_PROJECT = 'research-copilot-restore-m7'
$env:PRODUCT_RELEASE_PORT = '18082'
docker compose --env-file .local/dev.env -f compose.release.yaml up -d db
docker compose --env-file .local/dev.env -f compose.release.yaml ps
# 仅在 db healthy 后继续：复制 copilot.dump，pg_restore --clean --if-exists --no-owner
# 将 storage.tgz 解压到新建的 research-copilot-restore-m7_product_storage 卷
docker compose --env-file .local/dev.env -f compose.release.yaml up -d api worker
Invoke-RestMethod http://127.0.0.1:18082/health
```

恢复验收需比对：schema 迁移数、用户/项目/会话/File/FileVersion/Run/Span 数、孤立版本数，以及原卷与恢复卷中每个 blob 的 SHA-256。M7 实跑为 9/9 blob hash 一致、孤立 FileVersion 为 0。

## 回滚边界

镜像按 release source commit 标记并记录 image ID/digest。代码回滚只切换旧镜像；数据库迁移当前仅向前兼容，没有自动降级脚本。需要数据库回退时恢复对应时间点的数据库与文件卷成对备份，不能只回滚其中一个。

