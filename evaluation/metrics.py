"""Agent 离线回放的确定性分层指标。"""

from __future__ import annotations


STRUCTURE_MARKERS = [
    ["概述", "背景", "overview"],
    ["方向", "分类", "category"],
    ["对比", "比较", "comparison"],
    ["趋势", "展望", "future"],
    ["参考", "引用", "reference"],
]


def _ratio(hits: int, total: int, empty_value: float = 1.0) -> float:
    return hits / total if total else empty_value


def _normalize_steps(steps: list) -> list[dict]:
    normalized = []
    for step in steps or []:
        if isinstance(step, dict):
            normalized.append(step)
        elif isinstance(step, (tuple, list)) and len(step) >= 3:
            output = str(step[2])
            normalized.append(
                {
                    "name": str(step[0]),
                    "status": "error"
                    if output.startswith(("[错误]", "[失败]"))
                    else "success",
                }
            )
    return normalized


def evaluate_snapshot(case: dict, snapshot: dict) -> dict:
    output = str(snapshot.get("output", ""))
    output_lower = output.lower()
    stored_titles = [str(title) for title in snapshot.get("stored_titles", [])]
    stored_text = " ".join(stored_titles).lower()
    trace = snapshot.get("trace", {}) or {}
    steps = _normalize_steps(snapshot.get("intermediate_steps", []))

    directions = case.get("expected_directions", [])
    direction_hits = sum(1 for value in directions if value.lower() in output_lower)
    direction_coverage = _ratio(direction_hits, len(directions))

    papers = case.get("key_papers", [])
    paper_hits = sum(1 for value in papers if value.lower() in stored_text)
    paper_hit_rate = _ratio(paper_hits, len(papers))

    structure_hits = sum(
        1 for group in STRUCTURE_MARKERS if any(marker in output_lower for marker in group)
    )
    structure_score = _ratio(structure_hits, len(STRUCTURE_MARKERS), 0.0)

    expected_tools = set(case.get("expected_tools", []))
    used_tools = {str(step.get("name", "")) for step in steps}
    tool_coverage = _ratio(len(expected_tools & used_tools), len(expected_tools))
    successful_tools = sum(1 for step in steps if step.get("status", "success") == "success")
    tool_success_rate = _ratio(successful_tools, len(steps))

    min_papers = int(case.get("min_papers", 0))
    papers_count = int(trace.get("papers_count", len(stored_titles)) or 0)
    termination = trace.get("termination_reason", "unknown")
    allowed_terminations = case.get("allowed_termination_reasons", ["completed"])
    has_output = bool(output.strip()) and not output.startswith(("[错误]", "（Agent 未"))
    task_success = float(
        has_output and papers_count >= min_papers and termination in allowed_terminations
    )

    max_tool_calls = int(case.get("max_tool_calls", 100))
    max_duration_ms = int(case.get("max_duration_ms", 300000))
    tool_calls = int(trace.get("tool_calls", len(steps)) or 0)
    duration_ms = int(trace.get("duration_ms", 0) or 0)
    efficiency_score = (
        float(tool_calls <= max_tool_calls and duration_ms <= max_duration_ms)
        if duration_ms >= 0
        else 0.0
    )

    metrics = {
        "task_success": round(task_success, 4),
        "direction_coverage": round(direction_coverage, 4),
        "paper_hit_rate": round(paper_hit_rate, 4),
        "structure_score": round(structure_score, 4),
        "tool_coverage": round(tool_coverage, 4),
        "tool_success_rate": round(tool_success_rate, 4),
        "efficiency_score": round(efficiency_score, 4),
    }
    weights = {
        "task_success": 0.22,
        "direction_coverage": 0.18,
        "paper_hit_rate": 0.14,
        "structure_score": 0.16,
        "tool_coverage": 0.10,
        "tool_success_rate": 0.12,
        "efficiency_score": 0.08,
    }
    metrics["overall_score"] = round(
        sum(metrics[name] * weight for name, weight in weights.items()), 4
    )
    metrics["details"] = {
        "direction_hits": direction_hits,
        "direction_total": len(directions),
        "paper_hits": paper_hits,
        "paper_total": len(papers),
        "structure_hits": structure_hits,
        "used_tools": sorted(used_tools),
        "termination_reason": termination,
        "papers_count": papers_count,
        "tool_calls": tool_calls,
        "duration_ms": duration_ms,
    }
    return metrics
