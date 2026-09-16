"""基于阈值和基线退化的回归门禁。"""

DEFAULT_THRESHOLDS = {
    "task_success": 1.0,
    "direction_coverage": 0.6,
    "paper_hit_rate": 0.5,
    "structure_score": 0.8,
    "tool_coverage": 0.5,
    "tool_success_rate": 0.9,
    "efficiency_score": 1.0,
    "overall_score": 0.72,
}


def evaluate_gate(
    metrics: dict,
    thresholds: dict | None = None,
    baseline_metrics: dict | None = None,
    max_overall_regression: float = 0.05,
) -> dict:
    effective = dict(DEFAULT_THRESHOLDS)
    effective.update(thresholds or {})
    reasons = []
    for name, minimum in effective.items():
        actual = float(metrics.get(name, 0.0))
        if actual < float(minimum):
            reasons.append(f"{name}={actual:.3f} < {float(minimum):.3f}")

    regression = None
    if baseline_metrics is not None:
        current = float(metrics.get("overall_score", 0.0))
        baseline = float(baseline_metrics.get("overall_score", 0.0))
        regression = round(baseline - current, 4)
        if regression > max_overall_regression:
            reasons.append(
                f"overall regression={regression:.3f} > {max_overall_regression:.3f}"
            )

    return {
        "passed": not reasons,
        "reasons": reasons,
        "thresholds": effective,
        "overall_regression": regression,
    }
