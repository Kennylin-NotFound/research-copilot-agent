# M6 可观察性与质量评测验收

**状态：passed（2026-09-18）**

## 交付内容

- Run 详情补齐父子 Span、ActionRecord、上下文快照、检索命中、反馈和版本信息；导出接口按 owner 隔离并递归脱敏。
- 回答支持赞成/反对反馈，反馈绑定 `message/run/artifact_version`；模型选择进入请求幂等合同。
- 网页可展开步骤元数据、导出 Trace，并显示实际模型、Prompt、时延和 token。
- 固定了 12 个真实任务：开发集 8 个、留出集 4 个；三个预先声明的代表题分别用 `deepseek-v4-pro` 和 `deepseek-v4-flash` 同题运行。

## 实际问题与修复链路

1. **Trace 脱敏误伤指标。** 首轮 68 项回归中，`total_tokens` 被包含 `token` 的宽泛规则替换为 `[REDACTED]`。脱敏现改为精确的凭据字段与后缀匹配；`prompt_tokens/completion_tokens/total_tokens` 保留，API key、访问令牌、密码、cookie、数据库地址和租约令牌继续隐藏。最终 69/69 通过。
2. **真实模型路由偶发未返回有效 Function Call。** 第二轮 live 评测的开发任务 `D-REV-02` 与留出任务 `H-SUR-01` 均在两次 `choose_action` 后以 `invalid_decision_tool_call` 失败。失败 Run 和 Span 未删除。策略层增加了受约束回退：只有来源范围、未读顺序和轴均已由服务端验证时，才读取第一个未读来源；没有未读来源时停止。新鲜第三轮 12/12 完成。
3. **GroundedAnswer 结构输出。** 一次 live 候选在修正轮产生 `schema_error`。生成边界改为强制的 `produce_grounded_answer` 工具合同，继续保留最多一次结构/引用修正，不增加无限重试。

## 验收结果

| 门槛 | 结果 | 证据 |
|---|---|---|
| 已记录回答和失败均可追到 Run/Skill/模型/Prompt/工具/证据 | 通过 | Run detail/export、ActionRecord、Span、ContextSnapshot |
| Trace 足以定位问题且导出脱敏 | 通过 | `test_trace_export_redacts_sensitive_keys_and_is_owner_scoped` 及 token 指标回归 |
| 固定 12 个任务全部实际执行 | 通过 | `live_evaluation_summary.json`：8 个开发、4 个留出，12/12 |
| 两模型三个同题对照 | 通过 | 3/3 完成；只报告单次真实观察，不宣称统计优势 |
| 实际问题有定位、修复、回归链路 | 通过 | 上述脱敏误伤、路由失败和结构输出记录 |
| 无严重无依据结论、越权、数据丢失或状态覆盖 | 通过 | 引文定位与语义验证均通过；69 项隔离数据库测试通过 |

## 数字与边界

- 产品测试：69/69，使用隔离的真实 PostgreSQL 测试库；模型内容使用确定性替身。
- Live 固定集：12/12；模型对照：3/3。
- 真实 token 与 Run 时延保存在逐题 JSON。供应商响应没有账单金额字段，因此费用金额记录为未知。
- 三个模型对照和一个本机项目不能证明模型总体优劣或生产可靠性。`H-SUR-01` 对设计建议保持保守，没有生成明确的产品实施清单；该项记录为有用性边界，不是无依据事实错误。

## 证据

- `artifacts/product/M6/acceptance.json`
- `artifacts/product/M6/live_evaluation_summary.json`
- `artifacts/product/M6/manual_review.json`
- `artifacts/product/M6/product_tests.json`
- `evaluation/product/m6_tasks.json`

M6 通过后允许进入 M7 本机总验收；仍不允许部署服务器。
