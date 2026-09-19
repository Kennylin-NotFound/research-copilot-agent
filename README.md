# Research Copilot

Research Copilot 是一个面向个人研究工作的网页 Agent。它把连续对话、项目文件、RAG、显式 Skills、可恢复执行、Trace、反馈和费用估算放在同一个工作区中。

当前版本面向个人使用、小范围试用和单机服务器演示，已完成 GitHub CI/CD 与实际服务器部署验收。搜索结果只用于发现候选论文；涉及论文结论时，系统要求用户上传原文并通过本地证据链回答。

## 核心能力

- 多项目、多会话与服务端持久 Session
- PDF / TXT / Markdown 上传、版本管理、解析和向量索引
- `联网论文检索`：显式联网授权，返回公开学术来源候选
- `资料证据问答`：只根据已索引原文回答并保留页码、原句和版本
- `单篇论文评议`：受控多步执行并产出版本化成果
- `多篇证据综述`：跨来源对比，保留证据边界
- 项目目标、显式记忆、修改历史和 revision 冲突保护
- Run / Span / Action / Event / Context / Citation / Feedback 全链路 Trace
- 失败分类、租约恢复、重试、取消和迟到结果发布保护
- DeepSeek 官方峰谷价格下的 token 费用估算

## 运行架构

```text
Browser GUI
    │ HTTP + SSE
FastAPI API ───── PostgreSQL + pgvector
    │                    │
Durable worker ──────────┘
    │
LLM / Embedding / Tavily or Semantic Scholar
```

源码只有一套。环境差异通过配置和 Compose 文件隔离：

- `compose.local.yaml`：本机开发数据库
- `scripts/product_services.ps1`：本机 API 与 worker
- `compose.release.yaml`：本机 Linux 发布镜像验收
- `compose.production.yaml`：服务器生产约束，强制 HTTPS Cookie、关闭网页初始化、显式 Host allowlist

生产环境由 Caddy 提供 HTTPS。版本 tag 触发 GitHub Actions 测试、GHCR 多架构归档、TCR amd64 生产镜像发布、SSH 部署和公网健康检查；服务器只保存运行密钥。当前服务器验收见 [M8 报告](docs/product/M8_acceptance.md)，流水线与运维见 [GitHub CI/CD](docs/product/github_cicd.md) 和 [生产部署说明](docs/product/production_deployment.md)。

## 本机开发启动

要求：Python 3.11、Docker Desktop、Node.js（仅用于前端语法检查）。

```powershell
python scripts/product_local_init.py
docker compose --env-file .local/dev.env -f compose.local.yaml up -d db
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.product.lock.txt
.\.venv\Scripts\python.exe -m product.db
powershell -ExecutionPolicy Bypass -File scripts/product_services.ps1 Start
```

打开 <http://127.0.0.1:18080>。

模型、Embedding 和搜索密钥保存在根目录 `.env`；产品数据库和文件配置保存在 `.local/dev.env`。两者均被 Git 忽略。

### 创建或轮换本机用户

密码从标准输入读取，不进入命令历史参数。默认要求至少 10 位。短密码只允许显式的本机演示用途：

```powershell
# 强密码
Read-Host | .\.venv\Scripts\python.exe -m product.manage_user <username> --password-stdin

# 本机演示短密码
Read-Host | .\.venv\Scripts\python.exe -m product.manage_user <username> --password-stdin --allow-weak-demo-password
```

更新已有用户会保留其项目和文件，并撤销旧登录会话。

## 测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests/product -v
node --check product/web/app.js
node --check product/web/rag.js
```

测试使用独立的 `copilot_test` 数据库，不清空开发数据库。真实接口探针会消耗 API 配额，必须与离线测试结果分开记录。

## 本机发布镜像

```powershell
docker compose --env-file .local/dev.env -f compose.release.yaml build api
docker compose --env-file .local/dev.env -f compose.release.yaml up -d
Invoke-RestMethod http://127.0.0.1:18081/health
```

`compose.release.yaml` 只用于本机发布验收。服务器使用 `compose.production.yaml` 和未提交的 `.env.production`。部署步骤见 [生产部署说明](docs/product/production_deployment.md)。

## 证据与费用边界

- 外部搜索的标题、摘要片段和链接是候选线索，不是论文事实证据。
- 本地 RAG 引用会绑定文件版本、chunk、页码、原句和哈希；来源变化后旧成果会标记失效。
- Trace 不保存密钥或隐藏推理正文，导出前执行脱敏。
- 页面费用为估算值。DeepSeek 实际账单是最终依据；价格配置记录了来源与核对日期。
- 本仓库中的测试、受控 live 任务和本机演示不代表生产规模的成功率。

## 目录

```text
product/                 网页产品 API、worker、Skills、RAG、Trace 与 UI
product/skills/          仓库拥有并受 allowlist 约束的 Skills
product/migrations/      只增不改的 PostgreSQL 迁移
scripts/                 本机服务、验收、脱敏和运维入口
tests/product/           产品隔离测试
docs/product/            架构、开发、部署和验收说明
agent/ + domain/         产品复用的受控 Agent 状态与策略组件
```

## 发布安全

提交前运行：

```powershell
.\.venv\Scripts\python.exe scripts/product_check_staged.py
```

`.env`、`.local`、数据库、上传文件、运行证据、账号密码和生成缓存不得提交。生产镜像使用不可变标签；数据库和文件卷必须成对备份、成对恢复。
