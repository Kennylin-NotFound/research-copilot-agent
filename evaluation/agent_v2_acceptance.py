"""Research Copilot 单 Agent 的 12-case 确定性验收集。

该套件验证 task contract、观察驱动决策、状态归并、运行护栏、来源规则和
HITL 恢复。它不调用外部 API，也不评估论文事实质量或宣称优于其他系统。
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from agent.checkpointing import create_memory_checkpointer
from agent.state_graph import ResearchOrchestrator
from domain import (
    ActionType,
    AgentAction,
    ApprovalPolicy,
    EvidenceRef,
    EvidenceSupport,
    HumanDecision,
    HumanDecisionType,
    PaperCandidate,
    PaperStatus,
    ResearchBrief,
    RunLimits,
    RunStatus,
    TaskType,
    ToolResult,
    ToolStatus,
)


def _candidate(index: int, title: str | None = None, url: str | None = None) -> PaperCandidate:
    return PaperCandidate(
        paper_id=f"arxiv:2401.{index:05d}",
        title=title or f"Fixture paper {index}",
        url=url or f"https://arxiv.org/abs/2401.{index:05d}",
    )


class ScenarioGateway:
    """为每个验收场景提供可复现的搜索与阅读 observation。"""

    def __init__(
        self,
        *,
        search_batches: list[list[PaperCandidate]],
        evidence_enabled: bool = True,
        read_errors: set[str] | None = None,
    ):
        self.search_batches = search_batches
        self.evidence_enabled = evidence_enabled
        self.read_errors = read_errors or set()
        self.search_calls = 0
        self.read_calls = 0
        self.read_order: list[str] = []

    def execute(self, action: AgentAction) -> ToolResult:
        if action.tool_name == "search_papers":
            index = min(self.search_calls, max(len(self.search_batches) - 1, 0))
            batch = self.search_batches[index] if self.search_batches else []
            self.search_calls += 1
            return ToolResult.from_content(
                action_id=action.action_id,
                tool_name=action.tool_name,
                status=ToolStatus.SUCCESS,
                content=f"fixture search returned {len(batch)} candidate(s)",
                payload={
                    "candidates": [item.model_dump(mode="json") for item in batch]
                },
            )

        self.read_calls += 1
        paper_id = str(action.arguments.get("paper_id", ""))
        self.read_order.append(paper_id)
        if paper_id in self.read_errors:
            return ToolResult.from_content(
                action_id=action.action_id,
                tool_name=action.tool_name or "read_paper",
                status=ToolStatus.ERROR,
                content="fixture source unavailable",
                error_code="source_unavailable",
            )

        evidence: list[EvidenceRef] = []
        if self.evidence_enabled:
            evidence.append(
                EvidenceRef(
                    paper_id=paper_id,
                    axis=action.target_axis or "overview",
                    summary=f"Fixture evidence for {action.target_axis or 'overview'}.",
                    source_locator=str(action.arguments.get("paper_url", "")) or None,
                    support=EvidenceSupport.SUPPORTED,
                )
            )
        return ToolResult.from_content(
            action_id=action.action_id,
            tool_name=action.tool_name or "read_paper",
            status=ToolStatus.SUCCESS,
            content=f"fixture read returned {len(evidence)} evidence item(s)",
            payload={"evidence": [item.model_dump(mode="json") for item in evidence]},
        )


class CoverageDrivenPolicy:
    uses_model = False

    def decide(self, state, last_observation=""):
        unread = [
            item
            for item in state.candidates
            if item.status not in {PaperStatus.READ, PaperStatus.EXCLUDED}
            and item.paper_id not in state.selected_paper_ids
        ]
        if unread:
            paper = unread[0]
            axis = state.coverage.gaps[0]
            return AgentAction(
                action_type=ActionType.DEEP_READ,
                tool_name="read_paper",
                arguments={
                    "paper_id": paper.paper_id,
                    "paper_url": paper.url,
                    "paper_title": paper.title,
                },
                target_axis=axis,
                reason_summary=f"阅读候选论文以补充 {axis} 证据",
            )
        return AgentAction(
            action_type=ActionType.SEARCH,
            tool_name="search_papers",
            arguments={"query": state.brief.topic, "max_results": 5},
            target_axis=state.coverage.gaps[0] if state.coverage.gaps else None,
            reason_summary="当前没有未读候选，检索仍缺失的证据",
        )


class AlwaysSearchPolicy:
    uses_model = False

    def decide(self, state, last_observation=""):
        return AgentAction(
            action_type=ActionType.SEARCH,
            tool_name="search_papers",
            arguments={"query": state.brief.topic, "max_results": 5},
            reason_summary="继续检索当前未覆盖方向",
        )


class EvidenceFeedbackPolicy(CoverageDrivenPolicy):
    """第一篇 observation 到达后跳过静态第二名，选择补缺口的第三篇。"""

    def decide(self, state, last_observation=""):
        if not state.candidates:
            return super().decide(state, last_observation)
        if not state.evidence:
            paper = state.candidates[0]
            axis = state.coverage.gaps[0]
        else:
            paper = state.candidates[2]
            axis = state.coverage.gaps[0]
        return AgentAction(
            action_type=ActionType.DEEP_READ,
            tool_name="read_paper",
            arguments={
                "paper_id": paper.paper_id,
                "paper_url": paper.url,
                "paper_title": paper.title,
            },
            target_axis=axis,
            reason_summary=f"根据已获得证据选择 {paper.paper_id} 补充 {axis}",
        )


def _outcome(
    case_id: str,
    category: str,
    state,
    checks: dict[str, bool],
    gateway: ScenarioGateway | None = None,
) -> dict:
    return {
        "case_id": case_id,
        "category": category,
        "passed": all(checks.values()),
        "checks": checks,
        "status": state.status.value,
        "termination_reason": state.termination_reason,
        "actions": [item.action.action_type.value for item in state.decisions],
        "searches": state.progress.searches,
        "reads": state.progress.reads,
        "candidate_count": len(state.candidates),
        "evidence_count": len(state.evidence),
        "human_decision_count": len(state.human_decisions),
        "read_order": list(gateway.read_order) if gateway else [],
    }


def _case_a1_single_axis() -> dict:
    gateway = ScenarioGateway(search_batches=[[_candidate(1)]])
    state = ResearchOrchestrator(CoverageDrivenPolicy(), gateway).run(
        ResearchBrief(topic="single axis survey", research_axes=["evaluation"])
    )
    return _outcome(
        "C01_a1_single_axis_completion",
        "A1",
        state,
        {
            "completed": state.status == RunStatus.COMPLETED,
            "search_read": (state.progress.searches, state.progress.reads) == (1, 1),
            "coverage": state.coverage.complete,
        },
        gateway,
    )


def _case_a1_multi_axis() -> dict:
    gateway = ScenarioGateway(search_batches=[[_candidate(1), _candidate(2)]])
    state = ResearchOrchestrator(CoverageDrivenPolicy(), gateway).run(
        ResearchBrief(
            topic="multi axis survey",
            research_axes=["methods", "evaluation"],
        )
    )
    return _outcome(
        "C02_a1_multi_axis_completion",
        "A1",
        state,
        {
            "completed": state.status == RunStatus.COMPLETED,
            "two_axes": len(state.coverage.axes) == 2 and state.coverage.complete,
            "two_distinct_reads": len(set(gateway.read_order)) == 2,
        },
        gateway,
    )


def _case_a2_feedback_selection() -> dict:
    papers = [_candidate(1), _candidate(2), _candidate(3)]
    gateway = ScenarioGateway(search_batches=[papers])
    state = ResearchOrchestrator(EvidenceFeedbackPolicy(), gateway).run(
        ResearchBrief(
            task_type=TaskType.ADAPTIVE_PAPER_SELECTION,
            topic="adaptive selection",
            research_axes=["methods", "evaluation"],
        )
    )
    expected_order = [papers[0].paper_id, papers[2].paper_id]
    return _outcome(
        "C03_a2_evidence_feedback_selection",
        "A2",
        state,
        {
            "completed": state.status == RunStatus.COMPLETED,
            "observation_changed_selection": gateway.read_order == expected_order,
            "static_second_skipped": papers[1].paper_id not in gateway.read_order,
        },
        gateway,
    )


def _case_a2_failed_candidate_fallback() -> dict:
    bad, good = _candidate(1), _candidate(2)
    gateway = ScenarioGateway(
        search_batches=[[bad, good]],
        read_errors={bad.paper_id},
    )
    state = ResearchOrchestrator(CoverageDrivenPolicy(), gateway).run(
        ResearchBrief(
            task_type=TaskType.ADAPTIVE_PAPER_SELECTION,
            topic="fallback after unreadable source",
            research_axes=["evaluation"],
        )
    )
    bad_state = next(item for item in state.candidates if item.paper_id == bad.paper_id)
    return _outcome(
        "C04_a2_failed_candidate_fallback",
        "A2",
        state,
        {
            "completed": state.status == RunStatus.COMPLETED,
            "bad_excluded": bad_state.status == PaperStatus.EXCLUDED,
            "fallback_read": gateway.read_order == [bad.paper_id, good.paper_id],
        },
        gateway,
    )


def _case_a3_gap_closed() -> dict:
    gateway = ScenarioGateway(search_batches=[[_candidate(1)]])
    state = ResearchOrchestrator(CoverageDrivenPolicy(), gateway).run(
        ResearchBrief(
            task_type=TaskType.EVIDENCE_GAP_CLOSURE,
            topic="claim evidence closure",
            claims=["reflection improves task success"],
        )
    )
    return _outcome(
        "C05_a3_claim_gap_closed",
        "A3",
        state,
        {
            "completed": state.status == RunStatus.COMPLETED,
            "claim_is_axis": state.coverage.axes[0].axis == state.brief.claims[0],
            "evidence_attached": state.evidence[0].axis == state.brief.claims[0],
        },
        gateway,
    )


def _case_a3_gap_unresolved() -> dict:
    gateway = ScenarioGateway(
        search_batches=[[_candidate(1)]],
        evidence_enabled=False,
    )
    state = ResearchOrchestrator(CoverageDrivenPolicy(), gateway).run(
        ResearchBrief(
            task_type=TaskType.EVIDENCE_GAP_CLOSURE,
            topic="unsupported claim",
            claims=["unsupported result"],
            limits=RunLimits(max_consecutive_no_progress=1),
        )
    )
    return _outcome(
        "C06_a3_unresolved_gap_is_explicit",
        "A3",
        state,
        {
            "partial": state.status == RunStatus.PARTIAL_NO_PROGRESS,
            "reason": state.termination_reason == "no_progress",
            "gap_preserved": state.coverage.gaps == state.brief.claims,
        },
        gateway,
    )


def _case_empty_search_no_progress() -> dict:
    gateway = ScenarioGateway(search_batches=[[]])
    state = ResearchOrchestrator(AlwaysSearchPolicy(), gateway).run(
        ResearchBrief(
            topic="empty search",
            research_axes=["methods"],
            limits=RunLimits(max_consecutive_no_progress=2),
        )
    )
    return _outcome(
        "C07_empty_search_no_progress",
        "guard",
        state,
        {
            "partial": state.status == RunStatus.PARTIAL_NO_PROGRESS,
            "two_attempts": state.progress.searches == 2,
            "reason": state.termination_reason == "no_progress",
        },
        gateway,
    )


def _case_search_limit() -> dict:
    gateway = ScenarioGateway(search_batches=[[_candidate(1)]])
    state = ResearchOrchestrator(AlwaysSearchPolicy(), gateway).run(
        ResearchBrief(
            topic="search limit",
            research_axes=["methods"],
            limits=RunLimits(
                max_searches=1,
                max_iterations=4,
                max_consecutive_no_progress=4,
            ),
        )
    )
    return _outcome(
        "C08_search_limit_guard",
        "guard",
        state,
        {
            "partial_limit": state.status == RunStatus.PARTIAL_LIMIT_REACHED,
            "reason": state.termination_reason == "max_searches_reached",
            "one_external_search": gateway.search_calls == 1,
        },
        gateway,
    )


def _case_read_limit() -> dict:
    gateway = ScenarioGateway(search_batches=[[_candidate(1), _candidate(2)]])
    state = ResearchOrchestrator(CoverageDrivenPolicy(), gateway).run(
        ResearchBrief(
            topic="read limit",
            research_axes=["methods", "evaluation"],
            limits=RunLimits(
                max_reads=1,
                max_iterations=5,
                max_consecutive_no_progress=5,
            ),
        )
    )
    return _outcome(
        "C09_read_limit_guard",
        "guard",
        state,
        {
            "partial_limit": state.status == RunStatus.PARTIAL_LIMIT_REACHED,
            "reason": state.termination_reason == "max_reads_reached",
            "one_external_read": gateway.read_calls == 1,
        },
        gateway,
    )


def _case_source_allowlist() -> dict:
    blocked = _candidate(
        1,
        title="Untrusted fixture",
        url="https://untrusted.example/paper.pdf",
    )
    gateway = ScenarioGateway(search_batches=[[blocked]])
    state = ResearchOrchestrator(CoverageDrivenPolicy(), gateway).run(
        ResearchBrief(topic="source policy", research_axes=["methods"])
    )
    return _outcome(
        "C10_source_allowlist_review",
        "safety",
        state,
        {
            "paused": state.status == RunStatus.AWAITING_HUMAN,
            "reason": state.termination_reason == "source_not_allowed",
            "read_not_executed": gateway.read_calls == 0,
        },
        gateway,
    )


def _case_action_tool_contract() -> dict:
    class WrongToolPolicy:
        uses_model = False

        def decide(self, state, last_observation=""):
            return AgentAction(
                action_type=ActionType.SEARCH,
                tool_name="read_paper",
                arguments={"query": state.brief.topic, "max_results": 3},
                reason_summary="构造动作工具不一致以验证执行前拦截",
            )

    gateway = ScenarioGateway(search_batches=[[]])
    state = ResearchOrchestrator(WrongToolPolicy(), gateway).run(
        ResearchBrief(topic="action tool contract")
    )
    return _outcome(
        "C11_action_tool_contract_review",
        "safety",
        state,
        {
            "paused": state.status == RunStatus.AWAITING_HUMAN,
            "reason": state.termination_reason == "action_tool_mismatch",
            "no_tool_side_effect": gateway.search_calls == 0 and gateway.read_calls == 0,
        },
        gateway,
    )


def _case_strict_hitl_resume() -> dict:
    gateway = ScenarioGateway(search_batches=[[_candidate(1)]])
    orchestrator = ResearchOrchestrator(
        CoverageDrivenPolicy(),
        gateway,
        checkpointer=create_memory_checkpointer(),
    )
    run_id = "acceptance-hitl"
    first = orchestrator.run(
        ResearchBrief(
            topic="strict approval",
            research_axes=["evaluation"],
            approval_policy=ApprovalPolicy.STRICT,
        ),
        run_id=run_id,
    )
    second = orchestrator.resume(
        run_id,
        HumanDecision(decision=HumanDecisionType.APPROVE),
    )
    final = orchestrator.resume(
        run_id,
        HumanDecision(decision=HumanDecisionType.APPROVE),
    )
    return _outcome(
        "C12_strict_hitl_resume_without_duplicate_effects",
        "HITL",
        final,
        {
            "first_paused_before_search": (
                first.status == RunStatus.AWAITING_HUMAN and first.progress.searches == 0
            ),
            "second_paused_before_read": (
                second.status == RunStatus.AWAITING_HUMAN
                and second.progress.searches == 1
                and second.progress.reads == 0
            ),
            "completed": final.status == RunStatus.COMPLETED,
            "no_duplicate_effects": gateway.search_calls == 1 and gateway.read_calls == 1,
            "two_human_decisions": len(final.human_decisions) == 2,
        },
        gateway,
    )


CASES = [
    _case_a1_single_axis,
    _case_a1_multi_axis,
    _case_a2_feedback_selection,
    _case_a2_failed_candidate_fallback,
    _case_a3_gap_closed,
    _case_a3_gap_unresolved,
    _case_empty_search_no_progress,
    _case_search_limit,
    _case_read_limit,
    _case_source_allowlist,
    _case_action_tool_contract,
    _case_strict_hitl_resume,
]


def run_acceptance_suite() -> dict:
    results = []
    for case in CASES:
        try:
            results.append(case())
        except Exception as exc:
            results.append(
                {
                    "case_id": case.__name__,
                    "category": "harness_error",
                    "passed": False,
                    "checks": {"exception_free": False},
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    passed = sum(1 for item in results if item["passed"])
    return {
        "suite_id": "research-copilot-agent-v2-core-acceptance",
        "suite_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "evidence_scope": "deterministic_single_agent_control_plane_no_external_api",
        "case_count": len(results),
        "passed_count": passed,
        "pass_rate": round(passed / len(results), 4),
        "all_passed": passed == len(results),
        "results": results,
    }


def render_markdown(report: dict) -> str:
    lines = [
        "# Research Copilot Agent v2 单 Agent 核心验收",
        "",
        f"- Suite: `{report['suite_id']}` / `{report['suite_version']}`",
        f"- 范围：`{report['evidence_scope']}`",
        f"- 通过：{report['passed_count']}/{report['case_count']} ({report['pass_rate']:.0%})",
        "",
        "> 本报告验证单 Agent 控制面与工作流合同，不评估外部检索事实质量，也不证明优于其他 Agent。",
        "",
        "| Case | Category | Status | Termination | Actions | Result |",
        "|---|---|---|---|---|---|",
    ]
    for item in report["results"]:
        actions = " → ".join(item.get("actions", [])) or "-"
        lines.append(
            f"| {item['case_id']} | {item['category']} | {item.get('status', '-')} | "
            f"{item.get('termination_reason', '-')} | {actions} | "
            f"{'PASS' if item['passed'] else 'FAIL'} |"
        )
    lines.extend(["", "## Check details", ""])
    for item in report["results"]:
        checks = item.get("checks", {})
        rendered = ", ".join(
            f"{name}={'yes' if passed else 'no'}" for name, passed in checks.items()
        )
        lines.append(f"- `{item['case_id']}`: {rendered}")
        if item.get("error"):
            lines.append(f"  - Error: `{item['error']}`")
    lines.extend(
        [
            "",
            "## Acceptance boundary",
            "",
            "12/12 通过表示 A1/A2/A3 的主要状态分支、确定性护栏、来源规则与 HITL 恢复在离线 fixture 上符合合同。真实模型和外部工具仍需单独的 dated live E2E 验收。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent v2 12-case offline acceptance")
    parser.add_argument("--json-out")
    parser.add_argument("--md-out")
    args = parser.parse_args()
    report = run_acceptance_suite()
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
                "case_count": report["case_count"],
                "all_passed": report["all_passed"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    raise SystemExit(0 if report["all_passed"] else 1)


if __name__ == "__main__":
    main()
