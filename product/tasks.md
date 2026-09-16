# M0 真实资料任务

机器可读输入与预先定义标准：`evaluation/product/tasks.json`。
原文固定版本：ReAct v3、Reflexion v4；PDF 和页级文本保存在 `artifacts/product/M0/sources/`、`source_pages.json`。

| ID | 模式 | 输入范围 | 期望结果与停止条件 | 本次状态 |
|---|---|---|---|---|
| M0-QA-01 | evidence-qa | ReAct 第 1–3 页 | 两个机制证据点、原文页/引用与范围限制；结构化输出后停止 | Pro/Flash 均完成实际调用；引文定位通过；已逐条原文复核 |
| M0-REVIEW-01 | paper-review | Reflexion 第 1–3 页 | 至多三条机制 claim；反馈/Memory/不更新权重；未知内容明确 | 实际调用完成；输出 4 条超限，部分引文不精确，质量不通过 |
| M0-SURVEY-01 | evidence-survey | 两篇第 1–3 页 | 比较有来源；设计推断标明；不跨实验强行排名 | 实际调用完成；一处引文断词不匹配，严格引文检查未通过 |

性质：真实公开论文 + 为本项目预先编写的测试问题；不是历史用户日志，也不是已完成的 GUI Skills。所有任务均走同一结构化 API smoke harness，最终 Skill 执行链尚未实现。

复核方式：代码检查 schema/source_id/page/quote；本次助手逐条对照页原文检查含义，并保存 `artifacts/product/M0/task_review.md`。此记录不代表用户本人已经做过学习练习。

结论：继续产品化；需要将输出数量、有效引文、原文与生成内容分离落实为代码检查。小样本不支持模型优劣或实际生产效果结论。
