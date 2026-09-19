# 网页产品开发记录

## 当前断点：M8 已通过并签发 SERVER_READY（2026-09-19）

M0–M7 的本机开发、GUI 基本流程、80 项产品测试、真实模型小样本、Linux 容器、持久化和恢复已经完成；M8 已把同一产品发布到实际 OpenCloudOS 服务器，并建立 GitHub CI/CD、严格 IP HTTPS、备份恢复和跨版本回滚闭环。

当前生产入口为 `https://101.43.120.39`，版本 `v0.1.0-rc9`，源提交 `63fbc99`，TCR digest 为 `sha256:8eabe5f1e964c0662ef3058c03031cdce18d756b803c597b5a295e7e2ac86684`。浏览器首次访问前需信任 `artifacts/product/M8/research-copilot-caddy-root.crt`；账号密码、SSH 密钥、服务器 `.env.production` 和运行数据都不进入 Git。

## M4–M6 摘要

- M4：三个 Skills、多轮 Agent 决策、项目 revision、显式 Memory、ContextSnapshot 和版本化成果完成。历史失败 Run 保留为可观察性证据；导航已调整为只有 Agent 回答显示“查看运行记录”，Trace 与回答可以双向定位。
- M5：持久 Job、租约心跳、attempt、过期恢复、SSE 游标重放、取消、旧 revision/attempt 发布保护和分类重试完成。
- M6：Run/Span/Action/Context/反馈可按 owner 查询和脱敏导出；12 个 live 固定任务第三轮 12/12 完成，两个 DeepSeek 模型完成预声明同题小样本对照。

## M7 / LOCAL_READY

- 80/80 产品测试、G01–G20、Linux 容器 PDF/TXT/MD 入库、五种工作方式与备份恢复通过。
- API/worker/db 重启和隔离恢复后账号、Run、File、Trace 与全部 blob hash 一致。
- 用户完成本机 GUI 基本流程观察；该证据仍明确标为用户辅助人工 GUI，不改写为自动浏览器通过。

## M8 / SERVER_READY

- 私有仓库：`Kennylin-NotFound/research-copilot-agent`；main CI、tag release、GHCR/TCR 双镜像、SSH 部署与手工回滚均已实跑。
- 服务器：OpenCloudOS 9.4、4 vCPU、约 3.6 GiB；Docker 28.0.1、Compose 2.32.1。
- `v0.1.0-rc9` release run `35415767475` 成功，生产镜像按 TCR digest 固定；公网严格 TLS health 和页面静态资源通过。
- 生产账号 `kenny`、项目、会话、5 个正式验收 Run、100 页 PDF、RAG 引用、Trace 与版本化成果通过。
- 四容器重启后数据保留；成对备份恢复到隔离 project 后 24/24 blob hash 一致、0 orphan。
- 真实 GitHub 回滚 run `35429382917` 完成 rc9 → rc8，业务数据保留；再恢复 rc9 并复核通过。
- 详细事实与限制见 `docs/product/M8_acceptance.md` 和 `M8_report_zh.md`；机器清单在 `artifacts/product/M8/acceptance.json`。

## 后续可选加固

1. 获取域名并把 Caddy 从内部 CA 切换为公开 ACME，取消演示机的手工根证书信任。
2. 把 SSH/CD 从 root 迁移到独立低权限 deploy 用户。
3. 在需要多人使用时再增加速率、容量和长时间 SLO 测试；当前证据只支持单机个人产品与小范围试用。

历史失败、模拟测试、用户辅助 GUI、真实 API 验收和服务器运维证据继续分开陈述。
