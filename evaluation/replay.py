"""版本化 fixture 的零 API 离线回放与回归门禁。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from evaluation.metrics import evaluate_snapshot
from evaluation.regression import evaluate_gate


DEFAULT_CASES = Path(__file__).parent / "fixtures" / "offline_replay_cases.json"


def load_cases(path: str | Path = DEFAULT_CASES) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def run_backtest(path: str | Path = DEFAULT_CASES) -> dict:
    suite = load_cases(path)
    results = []
    for case in suite["cases"]:
        metrics = evaluate_snapshot(case, case["snapshot"])
        gate = evaluate_gate(
            metrics,
            thresholds=case.get("thresholds"),
            baseline_metrics=case.get("baseline_metrics"),
            max_overall_regression=float(case.get("max_overall_regression", 0.05)),
        )
        expected = bool(case.get("expected_gate_pass", True))
        results.append(
            {
                "case_id": case["id"],
                "category": case.get("category", "nominal"),
                "expected_gate_pass": expected,
                "metrics": metrics,
                "gate": gate,
                "expectation_matched": gate["passed"] == expected,
            }
        )

    matched = sum(1 for result in results if result["expectation_matched"])
    passed = sum(1 for result in results if result["gate"]["passed"])
    return {
        "suite_id": suite.get("suite_id", "unknown"),
        "suite_version": suite.get("suite_version", "unknown"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "evidence_scope": "offline_fixture_replay_no_external_api",
        "case_count": len(results),
        "gate_pass_count": passed,
        "gate_reject_count": len(results) - passed,
        "expectation_match_count": matched,
        "expectation_match_rate": round(matched / len(results), 4) if results else 0.0,
        "results": results,
    }


def render_markdown(report: dict) -> str:
    lines = [
        "# Research Copilot 离线回放报告",
        "",
        f"- Suite: `{report['suite_id']}` / `{report['suite_version']}`",
        f"- 证据范围：`{report['evidence_scope']}`",
        f"- Case 数：{report['case_count']}",
        f"- Gate 通过/拒绝：{report['gate_pass_count']} / {report['gate_reject_count']}",
        f"- 预期判定匹配：{report['expectation_match_count']}/{report['case_count']} "
        f"({report['expectation_match_rate']:.0%})",
        "",
        "> 该报告验证回放、分层指标与门禁逻辑，不代表在线检索或模型生成质量。",
        "",
        "| Case | 类别 | Overall | Gate | 预期 | 结论 |",
        "|---|---|---:|---|---|---|",
    ]
    for result in report["results"]:
        gate = "PASS" if result["gate"]["passed"] else "REJECT"
        expected = "PASS" if result["expected_gate_pass"] else "REJECT"
        matched = "匹配" if result["expectation_matched"] else "不匹配"
        lines.append(
            f"| {result['case_id']} | {result['category']} | "
            f"{result['metrics']['overall_score']:.3f} | {gate} | {expected} | {matched} |"
        )
    lines.extend(["", "## 拒绝原因", ""])
    rejected = [result for result in report["results"] if not result["gate"]["passed"]]
    for result in rejected:
        reasons = "; ".join(result["gate"]["reasons"])
        lines.append(f"- `{result['case_id']}`：{reasons}")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description="Research Copilot offline replay")
    parser.add_argument("--cases", default=str(DEFAULT_CASES))
    parser.add_argument("--json-out")
    parser.add_argument("--md-out")
    args = parser.parse_args()

    report = run_backtest(args.cases)
    if args.json_out:
        path = Path(args.json_out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.md_out:
        path = Path(args.md_out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({key: report[key] for key in (
        "suite_id", "suite_version", "case_count", "gate_pass_count",
        "gate_reject_count", "expectation_match_rate"
    )}, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["expectation_match_rate"] == 1.0 else 1)


if __name__ == "__main__":
    main()
