# 网页产品开发记录

## 当前断点：M3 已通过，进入 M4（2026-09-17）

M0–M2 已验收并提交（ffed885、c8e5fe0、63398dd）。M3 原文 RAG、索引、首个 Skill、引用回查已通过 K3；41 项隔离 PostgreSQL 产品测试通过，PDF/TXT/MD 与无证据真实 API 案例已复核，网页 TXT 选源问答与引用原文预览通过。详见 docs/product/M3_acceptance.md 和 artifacts/product/M3/acceptance.json。

本机 API http://127.0.0.1:18080，worker/live、Docker 原数据库卷已恢复；启动管理 scripts/product_services.ps1。不要输出 .env 或 .local/dev.env。智谱充值后同一 key 已恢复，不需新 key。

下一步按 K4：三个 Skills、v2 Agent 状态图适配、追问/执行/修改、revision 与 Memory、生成成果。M4–M7 未通过；M7 LOCAL_READY 前不上云。浏览器标注层遮挡鼠标，本次键盘 GUI 完成；M7 复核普通鼠标交互。

历史阶段证据分别在 artifacts/product/M0–M3，初次真实模型失败保留，不把受控测试宣称为生产效果。
