# 仓库与发布边界

## 单一源码策略

开发版、发布候选和生产版不复制源码。`product/` 是网页服务主入口，差异由环境变量、数据卷、Compose 文件和镜像标签表达。

| 区域 | 用途 | 进入生产镜像 |
|---|---|---:|
| `product/` | API、worker、RAG、Skills、Trace、前端 | 是 |
| `agent/`、`domain/` | 网页产品复用的 Agent 状态、决策合同 | 是 |
| `product/migrations/` | PostgreSQL/pgvector 迁移 | 是 |
| `scripts/` | 本机开发、验收和运维入口 | 否 |
| `tests/product/` | 隔离产品回归 | 否 |
| `evaluation/` | 离线与受控 live 评估资产 | 否 |
| `main.py`、`tools/`、`rag/`、`observability/` | 早期 CLI 原型与复现资产 | 否 |
| `docs/product/` | 架构、部署与历史验收记录 | 否 |

早期 CLI 代码暂时保留，因为它承载可复查的演化历史，且部分产品策略由 `agent/`、`domain/` 复用。Dockerfile 使用显式 `COPY`，不会把旧 CLI、测试、文档或本机数据带入生产镜像。

## 不进入 Git 的内容

- `.env`、`.env.production`、`.local/`
- PostgreSQL 数据、数据库备份和 session
- 用户上传论文、生成成果、Trace 导出和运行日志
- `artifacts/`、`data/`、`output/`、`reports/`
- 产品化过程中的 `.planning/`

## Git 分支与版本

- `m7-local-ready` 是产品化前的本机回退标签。
- `productization/v0.1.0` 用于本轮网页产品整理。
- 发布提交应使用语义化 tag，例如 `v0.1.0`；生产镜像使用 tag 加 digest，不能只使用 `latest`。
- GitHub 首次发布建议创建私有仓库；公开前再单独审计历史提交、许可证和可公开的论文/评估资产。

## 发布前门禁

1. 产品测试与前端语法检查通过。
2. 真实论文搜索、LLM 用量费用、登录和重启持久化通过。
3. 本机发布镜像重建后通过健康检查和关键流程。
4. `git diff --check` 无格式错误。
5. 只暂存本轮产品文件并运行 `scripts/product_check_staged.py`。
6. `git status` 中不得出现密钥、数据库、上传文件或运行缓存。
7. GitHub remote、仓库可见性和首次 push 在用户授权账号操作后再执行。
