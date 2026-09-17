# M3 原文 RAG 验收

2026-09-17：K3 通过。模型与 embedding 为真实外部 API；41 项产品测试使用真实隔离 PostgreSQL 与确定性模型替身，不混作真实模型成功率。

## 证据

- `artifacts/product/M3/product_tests.json`：41/41；覆盖跨用户/项目/所选文件过滤、回收与替换、缓存复用、索引失败、伪造引用、有限修复及发布前来源检查。
- `live_summary.json` 和四个逐题 JSON：PDF/TXT/MD 问答及无证据题；最初 PDF/MD 引用校验失败记录保留，不能说首次全部成功。
- `browser_run.json`：网页选 evidence-qa，仅选择 TXT，真实问答 Run `1cdc1494-7b4b-4dfc-9122-7916762ccc93`；Prompt `evidence-qa-2026-09-16.2`，一条回答、一条引用。打开引用后显示 TXT v2 当前版本、第 1 页原文、相同摘录及下载入口。
- 三份当前文件共 114 片段真实索引；TXT 3 个片段复用同项目内容缓存。PDF 107 片段、MD 4 片段。
- `semantic_review.md`：开发 Agent 逐项阅读回答、摘录与原文的人工式核查；不是独立标注团队或统计评测。

## 网页验收环境

浏览器恢复后原会话/文件保留。Codex 浏览器标注层 `codex-browser-sidebar-comments-root` 遮挡鼠标事件；通过控件键盘操作完成选资料、发送和打开引用，未修改网页运行状态或注入业务调用。正文与原文在 GUI 可见。后续总验收仍需复核普通浏览器鼠标流程。

## 边界与后续

- 引用 ID 与摘录匹配只证明定位，不自动证明结论；运行结果保留 `semantic_support=not_independently_checked`。
- PDF 样例一条摘录在 chunk 边界截断英文词，原文页可回查，属于表达质量缺陷；不据此宣称引文质量完美。
- 当前 evidence-qa 是限定原文问答，尚无完整跨轮研究状态与三 Skills Agent；M4 实现。
- 2048 维向量使用小语料精确查询；未验证大规模吞吐。
- M3 不证明生产可靠性；任务恢复、SSE、取消在 M5，完整评测 M6，总验收 M7。未上云。
