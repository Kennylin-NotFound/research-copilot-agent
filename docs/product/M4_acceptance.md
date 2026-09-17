# M4 验收记录｜三个 Skills、多轮状态与成果

时间：2026-09-18。阶段结论：K4 passed。运行证据位于 `artifacts/product/M4/`。

## 实现结果

- `evidence-qa`、`paper-review`、`evidence-survey` 由统一注册表加载并锁定运行时版本；服务端验证 Skill、工具白名单、Function Calling 结构和目标维度。
- v2 Research Graph 适配本项目的 `read_chunks`、`retrieve_evidence` 与 `write_artifact`，记录继续追问、工具读取、生成和结束决策。模糊的多论文评议会先询问目标论文。
- 项目目标、研究维度与显式 Memory 使用 `expected_revision` / `expected_version` 原子更新。ContextSnapshot 固定每次运行的项目状态、记忆、消息、Skill 和来源版本。
- Markdown、JSON、CSV 成果进入受管 `outputs`，记录来源 Run、项目 revision 与原文版本；新要求或原文变化使旧成果标记 stale，来源文件不被覆盖，也不把生成成果作为 RAG 原文。
- 引文执行连续原文、定位、截断和语义支持检查；失败可做一次有界修复，仍不合格时拒绝发布或交付明确的部分结果。

## K4 证据

- 三个真实资料任务均完成：`7d8ae830-4edb-4051-b888-f4ef45a6342e`（evidence-qa，4 引用）、`39ee0f49-513f-4164-9977-02c6b5914198`（paper-review，6 引用、3 成果）、`a2dd9a52-a037-4e4c-bb29-a7a4c34ba056`（evidence-survey，8 引用、3 成果）。三者使用项目 revision 3 和 `grounded-skills-2026-09-17.13`。
- Run `e210f9a4-46a0-44c2-8a41-fc74c5a44194` 对“请评议这篇论文”只提出一个必要问题，并未读取或生成成果。
- 单元与集成测试覆盖非法工具/结构、预算终止、Skill 快照、项目/Memory 版本冲突、跨会话使用、撤销后不复活、跨项目隔离、成果版本与 stale 状态。
- `state-revision-live.json` 记录 revision 2→3、有效原文保留以及既有成果失效；最终三个真实任务均在 revision 3 重新完成。
- 用户完成了主要 GUI 流程并报告基本通过：项目状态/Memory、Skill/来源、回答、引用回查、成果与 Trace 可见。后续反馈的双向 Trace 导航已修复；完整普通鼠标复核保留到 M7。

## 失败与修复

- 早期结构化输出不稳定：加入结构校验与有限重试。
- 人工复核发现语义外推：加入引用支持 judge 与回归规则，限制只能由显示引文直接支持的事实。
- Prompt 无法稳定处理多论文歧义：加入确定性的目标论文缺口门。
- PDF chunk 尾部可能形成截断引文：生成上下文前裁剪不完整尾部，并用确定性校验拒绝截断引用。
- `target_axis` 曾越过允许值：Function Call 结果必须通过白名单验证。
- 多次修复仍无法得到合法引文时：不发布不可信完整答案，允许带明确边界的部分交付。

界面中失败 Run `b24c0b43-3fa2-493a-a0e6-1a7750557cf9` 是 `citation_truncated_quote`，并非进程中断。它使用较早的 Agent Prompt `research-agent-2026-09-17.7`，两次答案都被发布前校验拒绝；最终成功 Run 使用 `research-agent-2026-09-17.8`。失败 Trace 保留，便于说明异常发现、有限重试和后续修复。

## 验证

- `python scripts/product_tests.py M4`：59 tests，0 failures，0 errors，0 skipped。
- `python -m compileall -q product scripts tests`：通过。
- `node --check`：`app.js`、`files.js`、`rag.js`、`context.js` 全部通过。
- 三个 live 任务与人工 claim-to-quote 复核：通过；人工复核不是独立标注或统计评测。

## 边界与下一阶段

当前是本机单用户演示产品证据。M4 不证明生产可靠性；断线事件重放、租约恢复、协作取消、并发与迟到发布保护在 M5，系统化评测与版本对照在 M6，容器、备份恢复和发布冻结在 M7。
