# Research Copilot 五分钟演示

1. **项目与会话（30 秒）**：登录，切换项目；新建会话并说明 Session、Project、Conversation 与 Run 的边界。
2. **文件与 RAG（60 秒）**：展示 PDF/TXT/MD 状态、版本和页预览；选择一份原文，用 evidence-qa 提问并打开页/chunk/原文摘录。
3. **Agent 与 Skills（90 秒）**：切换 paper-review 或 evidence-survey；展示 Agent 读取来源、停止条件、MD/JSON/CSV 成果及旧版本 stale 标记。
4. **多轮状态与 Memory（45 秒）**：修改研究维度，说明 revision 防止旧结果覆盖；展示可编辑、可撤销的显式项目 Memory。
5. **失败与恢复（45 秒）**：打开保留的失败 Run，说明引用截断或无效 Tool Call 如何被校验拒绝；展示有限重试、保守 fallback、取消和 SSE 重放。
6. **Trace、反馈与评测（45 秒）**：从 Agent 回答定位运行记录，展开模型/工具/校验 Span，导出脱敏 Trace并提交反馈；展示 12 个固定任务和两个模型三组同题结果。
7. **发布边界（25 秒）**：展示 Linux API/worker/db、备份恢复和镜像 digest。明确当前证据是本机单用户发布候选；服务器部署、长期多人使用和生产 SLA 尚未发生。

