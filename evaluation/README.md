# Evaluation 与 Replay 模块

> 目标：把“Agent 看起来能工作”升级为“每次修改都能离线回放、分层计分、经过门禁并追溯到版本”。

## 两层验证

1. **Offline replay**：读取版本化 fixture，不调用模型、检索或 Embedding，适合快速回归和故障注入。
2. **Live evaluator**：调用真实 Agent 和隔离知识库，验证真实检索/模型质量；会受网络、限流和配额影响。

两层共用 `evaluation.metrics.evaluate_snapshot()` 与 `evaluation.regression.evaluate_gate()`，避免线上/离线指标口径漂移。

## 七维指标

| 维度 | 含义 | 当前实现 |
|---|---|---|
| **任务成功** | 有有效输出、论文数达标、结束原因在允许集合 | 确定性检查 |
| **方向覆盖** | 预期研究方向命中比例 | 关键词回放信号 |
| **关键论文命中** | 知识库标题命中比例 | 标题包含匹配 |
| **结构完整** | 背景/方向/对比/趋势/参考五类要素 | 标记组检查 |
| **工具覆盖** | 实际工具是否覆盖 case 预期工具 | 轨迹集合匹配 |
| **工具成功** | 工具结果中非错误调用占比 | 状态或错误前缀 |
| **效率** | 工具调用数和总耗时是否在预算内 | case 级上限 |

综合分只用于回归排序；`RegressionGate` 同时检查逐项阈值，避免高分掩盖关键失败。

## 版本化 case 契约

每个 replay case 包含：

- `id`、`category` 和 suite version。
- `expected_directions`、`key_papers`、`expected_tools`。
- `min_papers`、允许的 termination reason、调用/耗时预算。
- 固定 `snapshot`：输出、标题、最小工具轨迹和 run trace。
- `expected_gate_pass`：用于校准门禁能否放行正常/恢复场景并拒绝故障场景。

## 当前离线回放结果

- Suite：`research-copilot-offline-replay` / `1.0.0`。
- 6 个 case：2 个正常 Survey、1 个固定 Deep-Dive、1 个递归降级恢复、2 个故障注入。
- Gate：4 个通过、2 个拒绝。
- 判定与预期：6/6 匹配。
- 报告：`reports/backtest_2026-08-21.json` 与 `.md`。

这证明 replay、指标和 gate 逻辑按设计工作，不证明在线检索质量、事实正确性或生产可用性。

## 使用方式

```bash
# 无 API 回放
python -m evaluation.replay \
  --json-out reports/backtest.json \
  --md-out reports/backtest.md

# 真实 Agent 评估，会使用外部服务
python -m evaluation.evaluator
```

## 后续成熟路径

- 将 fixture 扩展为 30-50 个经人工标注的主题/失败 case。
- 为检索层加入 Recall@k、MRR、nDCG 和 context precision。
- 为生成层加入事实正确性、faithfulness、citation accuracy，并用人工标注校准可选 LLM-as-Judge。
- 保存版本化 baseline；Prompt、模型、Embedding 或工具 schema 变化后自动对比退化。
- 在真实部署中接入 OpenTelemetry/Prometheus 等平台；当前文件系统 RunStore 是本地可审计实现。
