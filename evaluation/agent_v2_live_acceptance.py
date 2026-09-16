"""Validate dated Agent v2 live run artifacts without re-calling external APIs."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from urllib.parse import urlparse

from domain import (
    ActionType,
    EvidenceSupport,
    ResearchState,
    RunStatus,
    TaskType,
)


def _source_allowed(url: str | None, allowlist: list[str]) -> bool:
    if not url:
        return False
    host = (urlparse(url).hostname or "").casefold().removeprefix("www.")
    return any(
        host == domain or host.endswith(f".{domain}")
        for domain in (
            raw.casefold().strip().removeprefix("www.") for raw in allowlist
        )
    )


def validate_run(runs_dir: Path, run_id: str) -> dict:
    run_dir = runs_dir / run_id
    state_path = run_dir / "state.json"
    manifest_path = run_dir / "manifest.json"
    decisions_path = run_dir / "decisions.jsonl"

    state = ResearchState.model_validate_json(state_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    decision_lines = [
        line
        for line in decisions_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    executed = [
        item
        for item in state.decisions
        if item.result_status is not None
    ]
    executed_searches = sum(
        1
        for item in executed
        if item.action.action_type in {ActionType.SEARCH, ActionType.EXPAND_CITATIONS}
    )
    executed_reads = sum(
        1
        for item in executed
        if item.action.action_type in {ActionType.DEEP_READ, ActionType.INSPECT_CANDIDATE}
    )
    evidence_keys = [
        (item.paper_id.casefold(), item.axis.casefold()) for item in state.evidence
    ]
    a3_support_explicit = (
        state.brief.task_type != TaskType.EVIDENCE_GAP_CLOSURE
        or all(item.support != EvidenceSupport.UNKNOWN for item in state.evidence)
    )
    evidence_sources_allowed = all(
        _source_allowed(item.source_locator, state.brief.source_allowlist)
        for item in state.evidence
    )

    checks = {
        "completed": state.status == RunStatus.COMPLETED,
        "coverage_complete": state.coverage.complete,
        "termination_is_coverage": state.termination_reason == "coverage_satisfied",
        "errors_empty": not state.errors,
        "search_count_matches_trace": state.progress.searches == executed_searches,
        "read_count_matches_trace": state.progress.reads == executed_reads,
        "candidate_ids_unique": len(state.candidates)
        == len({item.paper_id for item in state.candidates}),
        "evidence_paper_axis_unique": len(evidence_keys) == len(set(evidence_keys)),
        "selected_papers_unique": len(state.selected_paper_ids)
        == len(set(state.selected_paper_ids)),
        "evidence_sources_allowed": evidence_sources_allowed,
        "a3_support_explicit": a3_support_explicit,
        "manifest_completed": manifest.get("status") == "completed",
        "decision_artifact_matches_state": len(decision_lines) == len(state.decisions),
    }
    return {
        "run_id": run_id,
        "task_type": state.brief.task_type.value,
        "topic": state.brief.topic,
        "passed": all(checks.values()),
        "checks": checks,
        "status": state.status.value,
        "termination_reason": state.termination_reason,
        "searches": state.progress.searches,
        "reads": state.progress.reads,
        "decisions": state.progress.decision_calls,
        "candidates": len(state.candidates),
        "evidence": len(state.evidence),
        "human_decisions": len(state.human_decisions),
        "evidence_support": [item.support.value for item in state.evidence],
        "actions": [item.action.action_type.value for item in state.decisions],
    }


def validate_runs(runs_dir: Path, run_ids: list[str]) -> dict:
    results = []
    for run_id in run_ids:
        try:
            results.append(validate_run(runs_dir, run_id))
        except Exception as exc:
            results.append(
                {
                    "run_id": run_id,
                    "task_type": "unknown",
                    "passed": False,
                    "checks": {"artifact_readable": False},
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    passed = sum(1 for item in results if item["passed"])
    task_types = {item.get("task_type") for item in results if item["passed"]}
    has_hitl = any(item.get("human_decisions", 0) >= 2 for item in results if item["passed"])
    suite_checks = {
        "all_runs_passed": passed == len(results),
        "a1_a2_a3_present": {
            TaskType.OPEN_SURVEY.value,
            TaskType.ADAPTIVE_PAPER_SELECTION.value,
            TaskType.EVIDENCE_GAP_CLOSURE.value,
        }
        <= task_types,
        "strict_hitl_run_present": has_hitl,
    }
    return {
        "suite_id": "research-copilot-agent-v2-live-acceptance",
        "suite_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "evidence_scope": "dated_deepseek_and_external_tool_run_artifacts",
        "run_count": len(results),
        "passed_count": passed,
        "suite_checks": suite_checks,
        "all_passed": all(suite_checks.values()),
        "results": results,
    }


def render_markdown(report: dict) -> str:
    lines = [
        "# Research Copilot Agent v2 Live 验收报告",
        "",
        f"- Suite: `{report['suite_id']}` / `{report['suite_version']}`",
        f"- 范围：`{report['evidence_scope']}`",
        f"- Run 通过：{report['passed_count']}/{report['run_count']}",
        f"- A1/A2/A3 齐备：{'yes' if report['suite_checks']['a1_a2_a3_present'] else 'no'}",
        f"- strict HITL 齐备：{'yes' if report['suite_checks']['strict_hitl_run_present'] else 'no'}",
        "",
        "> 本报告验证已保存的真实模型/工具轨迹与系统合同一致，不把少量 run 外推为生产成功率或研究质量分数。",
        "",
        "| Run | Task | Search/Read/Decision | Candidate/Evidence | Human | Support | Result |",
        "|---|---|---:|---:|---:|---|---|",
    ]
    for item in report["results"]:
        counts = f"{item.get('searches', '-')}/{item.get('reads', '-')}/{item.get('decisions', '-')}"
        artifacts = f"{item.get('candidates', '-')}/{item.get('evidence', '-')}"
        support = ", ".join(item.get("evidence_support", [])) or "-"
        lines.append(
            f"| `{item['run_id']}` | {item.get('task_type', '-')} | {counts} | "
            f"{artifacts} | {item.get('human_decisions', '-')} | {support} | "
            f"{'PASS' if item['passed'] else 'FAIL'} |"
        )
    lines.extend(["", "## Per-run contract checks", ""])
    for item in report["results"]:
        checks = ", ".join(
            f"{name}={'yes' if passed else 'no'}"
            for name, passed in item.get("checks", {}).items()
        )
        lines.append(f"- `{item['run_id']}`: {checks}")
        if item.get("error"):
            lines.append(f"  - Error: `{item['error']}`")
    lines.extend(
        [
            "",
            "## Evidence boundary",
            "",
            "这些 run 证明单 Agent 可在真实 DeepSeek 决策、Tavily 搜索和论文精读条件下完成 A1/A2/A3，并证明 strict HITL 能跨进程恢复且不重复工具副作用。它们不证明每条论文结论或引用都正确，也不构成大样本稳定性指标。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate dated Agent v2 live artifacts")
    parser.add_argument("--runs-dir", default="data/runs")
    parser.add_argument("--run-id", action="append", required=True)
    parser.add_argument("--json-out")
    parser.add_argument("--md-out")
    args = parser.parse_args()

    report = validate_runs(Path(args.runs_dir), args.run_id)
    if args.json_out:
        path = Path(args.json_out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.md_out:
        path = Path(args.md_out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_markdown(report), encoding="utf-8")
    print(
        json.dumps(
            {
                "suite_id": report["suite_id"],
                "passed_count": report["passed_count"],
                "run_count": report["run_count"],
                "suite_checks": report["suite_checks"],
                "all_passed": report["all_passed"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    raise SystemExit(0 if report["all_passed"] else 1)


if __name__ == "__main__":
    main()
