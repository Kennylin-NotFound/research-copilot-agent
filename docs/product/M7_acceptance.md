# M7 本机总验收与发布冻结

**状态：验收完成，等待记录 release source commit 与最终镜像 digest 后签发 LOCAL_READY。**

## Linux 发布候选

- `python:3.11.14-slim-bookworm` 构建 API/worker 镜像，PostgreSQL 使用 `pgvector/pgvector:0.8.6-pg16-bookworm`。
- API 和 worker 使用 UID/GID 10001、只读根文件系统、`no-new-privileges` 和独立持久文件卷；数据库、API、worker 均有 CPU/内存上限。
- 新卷启动应用了 8 个迁移；Linux `x86_64` 内 PyMuPDF 1.27.2.2 可导入，PDF/TXT/MD 三种资料均完成解析与 embedding。
- 容器内真实模型三任务：evidence-qa、paper-review、evidence-survey 全部完成，引用数分别为 3/3/6，成果数为 0/3/3。
- 空闲采样资源：API 约 63 MiB，worker 约 99 MiB，数据库约 33 MiB。该采样不代表并发峰值或服务器容量结论。

## 恢复与持久化

- API/worker/db 重启前后保留 3 个 Run 和 9 个 File。
- 数据库 custom dump 与文件卷归档恢复到全新 Compose project；9/9 blob SHA-256 一致，8 个迁移、3 个 Run、30 个 Span 均存在，孤立 FileVersion 为 0。
- 首次恢复因数据库尚未 ready 而失败；加入 health 等待后通过。失败记录未隐藏。

## G01–G20

`artifacts/product/M7/g01_g20_matrix.json` 逐项给出证据类型。确定性用例、故障注入、live 模型和人工 GUI 观察分开记录。GUI 主流程由用户在本线程操作并报告基本通过；本轮 Codex 浏览器复查被本机 URL 策略阻止，因此没有把它写成自动浏览器通过。两个 Run 定位问题已由代码与用户先前观察收尾。

## 当前边界

- M6 的 12 个真实任务和三个容器 smoke 任务属于小样本本机证据，不能说明生产成功率。
- 供应商未返回账单金额；费用只保留 token 和时延，金额未知。
- G11 是合成第二 owner 对已实现入口的测试，不是全面安全审计。
- HTTPS、域名、服务器重启、定时备份和外网 SSE 属于 M8。

最终签发项记录在忽略提交的 `artifacts/product/M7/release_manifest.json` 与 `acceptance.json`；源代码提交和镜像 digest 生成后，本文件状态更新为 `LOCAL_READY`。

