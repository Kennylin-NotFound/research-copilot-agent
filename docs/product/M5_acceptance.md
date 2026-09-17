# M5 验收记录｜执行恢复、事件与取消

时间：2026-09-18。阶段结论：K5 passed。证据位于 `artifacts/product/M5/`。

## 实现结果

- Job 使用 attempt、lease token、worker id、heartbeat、lease expiry、retry_after 与最多两次 attempt；过期 lease 会对账并重新排队，旧 worker 无有效 token 时不能发布。
- RunEvent 按 run 内单调序号持久保存，提供游标查询和 SSE；浏览器在刷新、断线重连或切回会话后可以从事件记录恢复，轮询保留为降级路径。
- Agent 决策写入 ActionRecord，action_id、attempt、请求指纹、结果状态和错误可追溯；重复写保持幂等。
- queued 任务可立即取消，running 任务进入 cancelling；模型或工具返回后再次检查 lease、run 状态、项目 revision 和来源版本，取消或迟到 attempt 不发布回答、引文或成果。
- rate limit、timeout、unavailable 只在 worker 层有限重试；结构/引文错误由内部有界修复处理，仍失败则终止，避免多层重试相乘。

## K5 验证

- 65 项产品测试通过，包含真实 PostgreSQL 租约、过期回收、瞬时超时后单次恢复、最终消息唯一性、有序事件重放、排队取消幂等、运行中取消阻止迟到发布、结构错误不做整任务重试及会话/项目隔离。
- 故障点覆盖调用前过期 lease、调用完成后的取消/项目版本/来源版本检查、提交完成后的重启持久性。外部模型请求在进程被强制终止时无法保证供应商未计费，RunEvent 与 attempt 会明确显示可能重复调用的边界。
- 429/timeout/unavailable → 最多一次整任务重试；schema/invalid citation → 调用边界内有限修复后失败或部分交付；无全文 → 明确需要来源，不重复调用。
- SSE 事件与 JSON 游标读取使用同一持久事件表；客户端只订阅当前 run，切项目/对话会关闭旧 EventSource，避免串台。
- 项目 revision、来源版本、run 状态和 lease token 四重发布检查阻止旧标签页、旧 worker 与迟到结果覆盖当前状态。

## 边界

Checkpoint、数据库事务和外部副作用的保证不同：Checkpoint/ActionRecord 用于恢复决策，事务保证内部唯一写入，外部模型调用在断点恰好落在响应前后时只能记录为状态未知，不能声称 exactly-once 或零重复费用。M6 继续完善 Trace 导出、反馈与固定评测。
