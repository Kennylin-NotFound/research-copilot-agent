# 网页产品开发记录

## 当前断点：M6 已通过，进入 M7（2026-09-18）

M0–M3 已验收并提交（`ffed885`、`c8e5fe0`、`63398dd`、`2a7ddb1`）。M4 已完成三个 Skills、多轮 Agent 决策、项目 revision、显式 Memory、ContextSnapshot 和版本化成果；59 项隔离 PostgreSQL 产品测试通过，三个真实论文任务、模糊需求追问、状态/成果失效与人工引文语义复核通过。详细记录见 `docs/product/M4_acceptance.md` 与 `artifacts/product/M4/acceptance.json`。

本机 API 为 `http://127.0.0.1:18080`，live worker 与 Docker 数据卷保留；使用 `scripts/product_services.ps1` 管理服务。不要输出 `.env` 或 `.local/dev.env`。M7 `LOCAL_READY` 前不上云。

## M4 失败记录与 GUI 收尾

界面中保留的失败 Run `b24c0b43-3fa2-493a-a0e6-1a7750557cf9` 不是服务中断：它在两次生成后均被确定性引用校验以 `citation_truncated_quote` 拒绝。随后加入不完整 chunk 尾部裁剪、截断引文检查与有限修复；同一证据综述任务由 Run `a2dd9a52-a037-4e4c-bb29-a7a4c34ba056` 成功完成。失败记录保留为可观察性证据。

用户报告 M4 GUI 基本流程通过，并指出两个导航问题。网页已调整为仅在 Agent 回答下展示“查看运行记录”；点击右侧运行会滚动并短暂高亮对应回答，回答侧入口会选中右侧 Trace。JS 语法及完整 59 项产品测试回归通过；普通鼠标完整复核仍纳入 M7 总验收。

## M5 完成

持久 Job 已加入租约心跳、attempt 和过期恢复；RunEvent/SSE 支持游标重放；协作取消、旧 revision/attempt 发布保护和分类重试已接入。65 项产品测试通过，详见 `docs/product/M5_acceptance.md` 与 `artifacts/product/M5/acceptance.json`。

## M6 完成

Run/Span/Action/Context/反馈现可按 owner 查询并脱敏导出，网页可展开步骤、导出 Trace 和提交回答反馈。固定 12 个 live 任务在第三轮新鲜执行中 12/12 完成；两个 DeepSeek 模型的三个预声明同题对照 3/3 完成。真实模型的无效决策 Tool Call 已保留失败证据，并通过“仅在服务端已验证来源范围内按未读顺序回退”修复。69 项产品测试全部通过。详见 `docs/product/M6_acceptance.md`。

## 下一步

按 K7 完成本机 GUI、故障回归、Linux API/worker/db 容器、备份恢复、G01–G20 证据矩阵和发布冻结。只有取得 `LOCAL_READY` 才进入 M8 服务器部署。

历史真实失败样例继续保留；模拟测试、人工复核与生产可靠性分别陈述。
