"""Agent v2 显式状态图的零 API 离线回放。

该回放只验证 observation-dependent 分支、状态归并和终止守卫，
不代表在线模型、论文检索、PDF 抽取或学术事实质量。
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from agent.policies import DeterministicResearchPolicy
from agent.state_graph import ResearchOrchestrator
from domain import (
    EvidenceRef,
    PaperCandidate,
    ResearchBrief,
    RunLimits,
    ToolResult,
    ToolStatus,
)


class FixtureGateway:
    def __init__(self, *, candidates: list[PaperCandidate], evidence_enabled: bool):
        self.candidates = candidates
        self.evidence_enabled = evidence_enabled

    def execute(self, action):
        if action.tool_name == "search_papers":
            return ToolResult.from_content(
                action_id=action.action_id,
                tool_name=action.tool_name,
                status=ToolStatus.SUCCESS,
                content=f"fixture returned {len(self.candidates)} candidate(s)",
                payload={
                    "candidates": [item.model_dump(mode="json") for item in self.candidates]
                },
            )
        evidence = []
        if self.evidence_enabled:
            evidence.append(
                EvidenceRef(
                    paper_id=action.arguments.get("paper_id", "fixture-paper"),
                    axis=action.target_axis or "methods",
                    summary="Fixture evidence produced after reading the selected paper.",
                    source_locator=action.arguments.get("paper_url"),
                )
            )
        return ToolResult.from_content(
            action_id=action.action_id,
            tool_name=action.tool_name,
            status=ToolStatus.SUCCESS,
            content=f"fixture returned {len(evidence)} evidence item(s)",
            payload={"evidence": [item.model_dump(mode="json") for item in evidence]},
        )


def _cases() -> list[dict]:
    candidate = PaperCandidate(
        paper_id="fixture:p1",
        title="Evidence-driven Research Agents",
        url="https://arxiv.org/abs/2401.00001",
    )
    return [
        {
            "id": "adaptive_observation_success",
            "expected_status": "completed",
            "brief": ResearchBrief(
                topic="evidence driven research agents",
                research_axes=["evaluation"],
                limits=RunLimits(max_consecutive_no_progress=2),
            ),
            "gateway": FixtureGateway(candidates=[candidate], evidence_enabled=True),
        },
        {
            "id": "consecutive_no_progress",
            "expected_status": "partial_no_progress",
            "brief": ResearchBrief(
                topic="unknown research direction",
                research_axes=["methods"],
                limits=RunLimits(
                    max_iterations=8,
                    max_searches=8,
                    max_reads=2,
                    max_consecutive_no_progress=2,
                ),
            ),
            "gateway": FixtureGateway(candidates=[], evidence_enabled=False),
        },
        {
            "id": "max_search_guard",
            "expected_status": "partial_limit_reached",
            "brief": ResearchBrief(
                topic="search guard",
                research_axes=["methods"],
                limits=RunLimits(
                    max_iterations=5,
                    max_searches=1,
                    max_reads=2,
                    max_consecutive_no_progress=5,
                ),
            ),
            "gateway": FixtureGateway(candidates=[], evidence_enabled=False),
        },
    ]


def run_agent_v2_backtest() -> dict:
    results = []
    for case in _cases():
        state = ResearchOrchestrator(
            DeterministicResearchPolicy(), case["gateway"]
        ).run(case["brief"], run_id=f"fixture-{case['id']}")
        actual_status = state.status.value
        results.append(
            {
                "case_id": case["id"],
                "expected_status": case["expected_status"],
                "actual_status": actual_status,
                "termination_reason": state.termination_reason,
                "expectation_matched": actual_status == case["expected_status"],
                "actions": [item.action.action_type.value for item in state.decisions],
                "searches": state.progress.searches,
                "reads": state.progress.reads,
                "iterations": state.progress.iterations,
                "candidate_count": len(state.candidates),
                "evidence_count": len(state.evidence),
            }
        )
    matched = sum(1 for item in results if item["expectation_matched"])
    return {
        "suite_id": "agent-v2-stategraph-offline",
        "suite_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "evidence_scope": "deterministic_stategraph_replay_no_external_api",
        "case_count": len(results),
        "expectation_match_count": matched,
        "expectation_match_rate": round(matched / len(results), 4),
        "results": results,
    }


def render_markdown(report: dict) -> str:
    lines = [
        "# Research Copilot Agent v2 离线状态图回放",
        "",
        f"- Suite: `{report['suite_id']}` / `{report['suite_version']}`",
        f"- 范围：`{report['evidence_scope']}`",
        f"- 预期状态匹配：{report['expectation_match_count']}/{report['case_count']} "
        f"({report['expectation_match_rate']:.0%})",
        "",
        "> 该报告只证明显式状态、观察驱动分支和终止守卫可离线复现；不证明在线模型或文献质量。",
        "",
        "| Case | Expected | Actual | Termination | Actions | Match |",
        "|---|---|---|---|---|---|",
    ]
    for item in report["results"]:
        actions = " → ".join(item["actions"])
        lines.append(
            f"| {item['case_id']} | {item['expected_status']} | {item['actual_status']} | "
            f"{item['termination_reason']} | {actions} | "
            f"{'yes' if item['expectation_matched'] else 'no'} |"
        )
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description="Agent v2 offline StateGraph replay")
    parser.add_argument("--json-out")
    parser.add_argument("--md-out")
    args = parser.parse_args()
    report = run_agent_v2_backtest()
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
                "case_count": report["case_count"],
                "expectation_match_rate": report["expectation_match_rate"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    raise SystemExit(0 if report["expectation_match_rate"] == 1.0 else 1)


if __name__ == "__main__":
    main()
