"""Agent v2 任务合同、显式状态图和持久化的纯离线测试。"""

import json
import os
from pathlib import Path
import sys
import tempfile

from pydantic import ValidationError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_research_brief_contract_and_roundtrip():
    from domain import ResearchBrief, ResearchState, TaskType

    brief = ResearchBrief(
        topic="  LLM   Agent memory  ",
        research_axes=["methods", "Methods", "evaluation"],
    )
    assert brief.topic == "LLM Agent memory"
    assert brief.research_axes == ["methods", "evaluation"]

    state = ResearchState(brief=brief)
    restored = ResearchState.model_validate_json(state.model_dump_json())
    assert restored == state
    assert restored.safe_digest() == state.safe_digest()

    try:
        ResearchBrief(
            task_type=TaskType.EVIDENCE_GAP_CLOSURE,
            topic="unsupported claims",
        )
    except ValidationError as exc:
        assert "requires at least one claim" in str(exc)
    else:
        raise AssertionError("A3 without claims must be rejected")


def test_state_rejects_duplicate_candidate_ids_and_limit_overrun():
    from domain import PaperCandidate, ProgressState, ResearchBrief, ResearchState, RunLimits

    brief = ResearchBrief(
        topic="agent evaluation",
        limits=RunLimits(max_iterations=1),
    )
    try:
        ResearchState(
            brief=brief,
            candidates=[
                PaperCandidate(paper_id="p1", title="A"),
                PaperCandidate(paper_id="p1", title="B"),
            ],
        )
    except ValidationError as exc:
        assert "paper_id values must be unique" in str(exc)
    else:
        raise AssertionError("duplicate paper IDs must be rejected")

    try:
        ResearchState(brief=brief, progress=ProgressState(iterations=2))
    except ValidationError as exc:
        assert "iterations exceed" in str(exc)
    else:
        raise AssertionError("run-limit overrun must be rejected")


def test_tool_result_excludes_raw_content_from_serialization():
    from domain import ToolResult, ToolStatus

    result = ToolResult.from_content(
        action_id="a1",
        tool_name="read_paper",
        status=ToolStatus.SUCCESS,
        content="private full observation",
    )
    dumped = result.model_dump(mode="json")
    assert "content" not in dumped
    assert "private full observation" not in json.dumps(dumped)
    assert len(result.content_sha256) == 64
    assert result.content == "private full observation"


def test_legacy_search_text_adapter():
    from agent.tool_gateway import parse_search_candidates

    text = """检索关键词: \"agent memory\"
找到 2 篇论文：

[1] 标题: Memory Paper A
    作者: A
    年份: 2024 | 引用数: 10
    链接: https://arxiv.org/abs/2401.00001
    摘要: episodic memory

[2] 标题: Memory Paper B
    作者: B
    年份: 2023 | 引用数: 5
    链接: https://example.org/paper-b
    摘要: semantic memory
"""
    candidates = parse_search_candidates(text, "agent memory")
    assert len(candidates) == 2
    assert candidates[0].paper_id == "arxiv:2401.00001"
    assert candidates[0].year == 2024
    assert candidates[1].discovered_by_query == "agent memory"


def test_search_adapter_deduplicates_titles_and_prefers_readable_arxiv_url():
    from agent.tool_gateway import parse_search_candidates

    text = """检索关键词: \"memory evaluation\"
找到 2 篇论文：

[1] 标题: [PDF] MEMTRACK: Evaluating Agent Memory | Semantic Scholar
    链接: https://www.semanticscholar.org/paper/MEMTRACK/abc123
    摘要: metadata result

[2] 标题: [PDF] MEMTRACK: Evaluating Agent Memory | Semantic Scholar
    链接: https://api.semanticscholar.org/arXiv:2510.01353
    摘要: arxiv result
"""
    candidates = parse_search_candidates(text, "memory evaluation")
    assert len(candidates) == 1
    assert candidates[0].title == "MEMTRACK: Evaluating Agent Memory"
    assert candidates[0].paper_id == "arxiv:2510.01353"
    assert candidates[0].url == "https://arxiv.org/abs/2510.01353"


def test_tool_gateway_rejects_semantic_scholar_metadata_page_before_fetch():
    from agent.tool_gateway import ToolGateway
    from domain import ActionType, AgentAction, ToolStatus

    class NeverInvokedReadTool:
        name = "read_paper"

        def invoke(self, arguments):
            raise AssertionError("metadata page must be blocked before network fetch")

    action = AgentAction(
        action_type=ActionType.DEEP_READ,
        tool_name="read_paper",
        arguments={
            "paper_url": "https://www.semanticscholar.org/paper/example/abc",
            "paper_id": "p1",
        },
        target_axis="evaluation",
        reason_summary="candidate appears relevant but URL is metadata only",
    )
    result = ToolGateway([NeverInvokedReadTool()]).execute(action)
    assert result.status == ToolStatus.ERROR
    assert result.error_code == "metadata_page_not_readable"


def test_tool_gateway_parses_explicit_evidence_support():
    from agent.tool_gateway import ToolGateway
    from domain import ActionType, AgentAction, EvidenceSupport, ToolStatus

    class SupportReadTool:
        name = "read_paper"

        def invoke(self, arguments):
            assert arguments["target_claim"] == "evaluation claim"
            return "**关键发现**：存在相关结果。\n\n**证据判断**：partial\n**判断依据**：只覆盖部分设置。"

    action = AgentAction(
        action_type=ActionType.DEEP_READ,
        tool_name="read_paper",
        arguments={
            "paper_url": "https://arxiv.org/abs/2401.00001",
            "paper_id": "arxiv:2401.00001",
            "paper_title": "Support fixture",
        },
        target_axis="evaluation claim",
        reason_summary="读取论文并判断其对当前主张的支持关系",
    )
    result = ToolGateway([SupportReadTool()]).execute(action)
    assert result.status == ToolStatus.SUCCESS
    assert result.payload["evidence"][0]["support"] == EvidenceSupport.PARTIAL.value


def test_a3_unknown_evidence_does_not_close_claim_gap():
    from agent.state_graph import ResearchOrchestrator
    from domain import (
        EvidenceRef,
        EvidenceSupport,
        ResearchBrief,
        ResearchPlan,
        ResearchState,
        TaskType,
    )

    claim = "external memory improves long-horizon performance"
    state = ResearchState(
        brief=ResearchBrief(
            task_type=TaskType.EVIDENCE_GAP_CLOSURE,
            topic="memory evidence",
            claims=[claim],
        ),
        plan=ResearchPlan(axes=[claim], queries=["memory evidence"]),
        evidence=[
            EvidenceRef(
                paper_id="p1",
                axis=claim,
                summary="topic-related but inconclusive",
                support=EvidenceSupport.UNKNOWN,
            )
        ],
    )
    unknown_coverage = ResearchOrchestrator._build_coverage(state)
    assert unknown_coverage.complete is False
    assert unknown_coverage.gaps == [claim]

    state.evidence[0].support = EvidenceSupport.CONTRADICTED
    resolved_coverage = ResearchOrchestrator._build_coverage(state)
    assert resolved_coverage.complete is True


def test_run_recorder_persists_state_and_decisions():
    from observability import RunRecorder

    with tempfile.TemporaryDirectory() as temp_dir:
        recorder = RunRecorder(temp_dir)
        session = recorder.start_run(
            "agent_survey_v2", "topic", run_id="typed-run"
        )
        session.write_state({"run_id": "typed-run", "status": "completed"})
        session.record_decision(
            {"decision_id": "d1", "reason_summary": "coverage gap"}
        )
        session.finish({"termination_reason": "coverage_satisfied"})

        run_dir = Path(temp_dir) / "typed-run"
        assert json.loads((run_dir / "state.json").read_text(encoding="utf-8"))[
            "status"
        ] == "completed"
        decisions = (run_dir / "decisions.jsonl").read_text(encoding="utf-8")
        assert "coverage gap" in decisions
        assert recorder.health_report()["healthy"] is True


class EvidenceDrivenPolicy:
    uses_model = False

    def decide(self, state, last_observation=""):
        from domain import ActionType, AgentAction

        if not state.candidates:
            return AgentAction(
                action_type=ActionType.SEARCH,
                tool_name="search_papers",
                arguments={"query": state.brief.topic, "max_results": 3},
                target_axis=state.plan.axes[0],
                reason_summary="没有候选论文，先检索",
            )
        if not state.evidence:
            paper = state.candidates[0]
            return AgentAction(
                action_type=ActionType.DEEP_READ,
                tool_name="read_paper",
                arguments={
                    "paper_url": paper.url,
                    "paper_id": paper.paper_id,
                    "paper_title": paper.title,
                },
                target_axis=state.plan.axes[0],
                reason_summary="检索得到候选后阅读论文补充证据",
            )
        return AgentAction(
            action_type=ActionType.STOP,
            reason_summary="证据已经满足任务要求",
        )


class EvidenceGateway:
    def execute(self, action):
        from domain import EvidenceRef, PaperCandidate, ToolResult, ToolStatus

        if action.tool_name == "search_papers":
            candidate = PaperCandidate(
                paper_id="p1",
                title="Evidence-driven agents",
                url="https://arxiv.org/abs/2401.00001",
                discovered_by_query=action.arguments["query"],
            )
            return ToolResult.from_content(
                action_id=action.action_id,
                tool_name=action.tool_name,
                status=ToolStatus.SUCCESS,
                content="one candidate",
                payload={"candidates": [candidate.model_dump(mode="json")]},
            )
        evidence = EvidenceRef(
            paper_id=action.arguments["paper_id"],
            axis=action.target_axis,
            summary="The paper provides direct evaluation evidence.",
            source_locator=action.arguments["paper_url"],
        )
        return ToolResult.from_content(
            action_id=action.action_id,
            tool_name=action.tool_name,
            status=ToolStatus.SUCCESS,
            content="structured paper evidence",
            payload={"evidence": [evidence.model_dump(mode="json")]},
        )


def test_state_graph_replans_after_tool_observation():
    from agent.state_graph import ResearchOrchestrator
    from domain import ActionType, ResearchBrief, RunStatus

    brief = ResearchBrief(
        topic="evidence driven research agents",
        research_axes=["evaluation"],
    )
    orchestrator = ResearchOrchestrator(EvidenceDrivenPolicy(), EvidenceGateway())
    state = orchestrator.run(brief, run_id="observation-run")

    assert state.status == RunStatus.COMPLETED
    assert state.termination_reason == "coverage_satisfied"
    assert state.progress.searches == 1
    assert state.progress.reads == 1
    assert state.progress.decision_calls == 2
    assert state.progress.model_calls == 0
    assert [d.action.action_type for d in state.decisions] == [
        ActionType.SEARCH,
        ActionType.DEEP_READ,
    ]
    assert state.coverage.complete is True


def test_guard_assigns_the_only_open_axis_when_model_omits_target():
    from agent.state_graph import ResearchOrchestrator
    from domain import ActionType, AgentAction, ResearchBrief, RunStatus

    class MissingAxisPolicy(EvidenceDrivenPolicy):
        def decide(self, state, last_observation=""):
            action = super().decide(state, last_observation)
            if action.action_type == ActionType.DEEP_READ:
                return AgentAction.model_validate(
                    {**action.model_dump(mode="json"), "target_axis": None}
                )
            return action

    state = ResearchOrchestrator(MissingAxisPolicy(), EvidenceGateway()).run(
        ResearchBrief(topic="single gap axis", research_axes=["evaluation"])
    )
    assert state.status == RunStatus.COMPLETED
    assert state.evidence[0].axis == "evaluation"
    assert state.decisions[-1].action.target_axis == "evaluation"


def test_non_retryable_read_error_excludes_candidate_from_replanning():
    from agent.state_graph import ResearchOrchestrator
    from domain import (
        PaperCandidate,
        PaperStatus,
        ResearchBrief,
        RunLimits,
        ToolResult,
        ToolStatus,
    )

    class FailingReadGateway:
        def execute(self, action):
            if action.tool_name == "search_papers":
                candidate = PaperCandidate(
                    paper_id="p1",
                    title="Metadata-only candidate",
                    url="https://www.semanticscholar.org/paper/example/p1",
                )
                return ToolResult.from_content(
                    action_id=action.action_id,
                    tool_name=action.tool_name,
                    status=ToolStatus.SUCCESS,
                    content="candidate",
                    payload={"candidates": [candidate.model_dump(mode="json")]},
                )
            return ToolResult.from_content(
                action_id=action.action_id,
                tool_name=action.tool_name,
                status=ToolStatus.ERROR,
                content="metadata page is not readable",
                error_code="metadata_page_not_readable",
            )

    state = ResearchOrchestrator(EvidenceDrivenPolicy(), FailingReadGateway()).run(
        ResearchBrief(
            topic="exclude failed candidate",
            research_axes=["evaluation"],
            limits=RunLimits(max_iterations=2, max_searches=1, max_reads=1),
        )
    )
    assert state.candidates[0].status == PaperStatus.EXCLUDED
    assert state.errors == ["read_paper:metadata_page_not_readable"]


class AlwaysSearchPolicy:
    uses_model = False

    def decide(self, state, last_observation=""):
        from domain import ActionType, AgentAction

        return AgentAction(
            action_type=ActionType.SEARCH,
            tool_name="search_papers",
            arguments={"query": state.brief.topic},
            reason_summary="继续搜索尚未覆盖的方向",
        )


class EmptySearchGateway:
    def execute(self, action):
        from domain import ToolResult, ToolStatus

        return ToolResult.from_content(
            action_id=action.action_id,
            tool_name=action.tool_name,
            status=ToolStatus.SUCCESS,
            content="no candidates",
            payload={"candidates": []},
        )


def test_state_graph_stops_after_consecutive_no_progress():
    from agent.state_graph import ResearchOrchestrator
    from domain import ResearchBrief, RunLimits, RunStatus

    brief = ResearchBrief(
        topic="unknown research direction",
        research_axes=["methods"],
        limits=RunLimits(
            max_iterations=8,
            max_searches=8,
            max_reads=2,
            max_consecutive_no_progress=2,
        ),
    )
    state = ResearchOrchestrator(AlwaysSearchPolicy(), EmptySearchGateway()).run(brief)
    assert state.status == RunStatus.PARTIAL_NO_PROGRESS
    assert state.termination_reason == "no_progress"
    assert state.progress.iterations == 2
    assert state.progress.searches == 2


def test_policy_guard_enforces_max_searches():
    from agent.state_graph import ResearchOrchestrator
    from domain import ResearchBrief, RunLimits, RunStatus

    brief = ResearchBrief(
        topic="search limit test",
        research_axes=["methods"],
        limits=RunLimits(
            max_iterations=5,
            max_searches=1,
            max_reads=2,
            max_consecutive_no_progress=5,
        ),
    )
    state = ResearchOrchestrator(AlwaysSearchPolicy(), EmptySearchGateway()).run(brief)
    assert state.status == RunStatus.PARTIAL_LIMIT_REACHED
    assert state.termination_reason == "max_searches_reached"
    assert state.progress.searches == 1
    assert len(state.decisions) == 3  # search, second requested search, guard stop


def test_persistent_hitl_approve_resume_across_sqlite_connections():
    from agent.checkpointing import create_sqlite_checkpointer
    from agent.state_graph import ResearchOrchestrator
    from domain import (
        ApprovalPolicy,
        HumanDecision,
        HumanDecisionType,
        ResearchBrief,
        RunStatus,
    )
    from observability import RunRecorder

    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "checkpoints" / "agent.sqlite"
        recorder = RunRecorder(Path(temp_dir) / "runs")
        brief = ResearchBrief(
            topic="persistent human review",
            research_axes=["evaluation"],
            approval_policy=ApprovalPolicy.STRICT,
        )

        saver, connection, _ = create_sqlite_checkpointer(db_path)
        try:
            first = ResearchOrchestrator(
                EvidenceDrivenPolicy(), EvidenceGateway(), recorder, saver
            ).run(brief, run_id="hitl-persistent")
        finally:
            connection.close()
        assert first.status == RunStatus.AWAITING_HUMAN
        assert first.progress.searches == 0

        saver, connection, _ = create_sqlite_checkpointer(db_path)
        try:
            second = ResearchOrchestrator(
                EvidenceDrivenPolicy(), EvidenceGateway(), recorder, saver
            ).resume(
                "hitl-persistent",
                HumanDecision(decision=HumanDecisionType.APPROVE),
            )
        finally:
            connection.close()
        assert second.status == RunStatus.AWAITING_HUMAN
        assert second.progress.searches == 1
        assert second.progress.reads == 0
        assert len(second.human_decisions) == 1

        saver, connection, _ = create_sqlite_checkpointer(db_path)
        try:
            final = ResearchOrchestrator(
                EvidenceDrivenPolicy(), EvidenceGateway(), recorder, saver
            ).resume(
                "hitl-persistent",
                HumanDecision(decision=HumanDecisionType.APPROVE),
            )
        finally:
            connection.close()
        assert final.status == RunStatus.COMPLETED
        assert final.progress.searches == 1
        assert final.progress.reads == 1
        assert len(final.human_decisions) == 2
        assert len(final.decisions) == 2

        run_dir = Path(temp_dir) / "runs" / "hitl-persistent"
        decision_lines = [
            line
            for line in (run_dir / "decisions.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert len(decision_lines) == 2
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["status"] == "completed"
        assert recorder.health_report()["healthy"] is True


def test_hitl_reject_prevents_tool_execution():
    from agent.checkpointing import create_memory_checkpointer
    from agent.state_graph import ResearchOrchestrator
    from domain import (
        ApprovalPolicy,
        HumanDecision,
        HumanDecisionType,
        ResearchBrief,
        RunStatus,
    )

    orchestrator = ResearchOrchestrator(
        EvidenceDrivenPolicy(), EvidenceGateway(), checkpointer=create_memory_checkpointer()
    )
    paused = orchestrator.run(
        ResearchBrief(
            topic="reject unsafe action",
            research_axes=["evaluation"],
            approval_policy=ApprovalPolicy.STRICT,
        ),
        run_id="hitl-reject",
    )
    assert paused.status == RunStatus.AWAITING_HUMAN
    final = orchestrator.resume(
        "hitl-reject",
        HumanDecision(
            decision=HumanDecisionType.REJECT,
            reason="source scope is not approved",
        ),
    )
    assert final.status == RunStatus.PARTIAL_NO_PROGRESS
    assert final.termination_reason == "human_rejected_action"
    assert final.progress.searches == 0
    assert final.progress.reads == 0


def test_hitl_edit_revalidates_and_executes_edited_action():
    from agent.checkpointing import create_memory_checkpointer
    from agent.state_graph import ResearchOrchestrator
    from domain import (
        ApprovalPolicy,
        HumanDecision,
        HumanDecisionType,
        ResearchBrief,
        RunStatus,
    )

    orchestrator = ResearchOrchestrator(
        EvidenceDrivenPolicy(), EvidenceGateway(), checkpointer=create_memory_checkpointer()
    )
    paused = orchestrator.run(
        ResearchBrief(
            topic="original query",
            research_axes=["evaluation"],
            approval_policy=ApprovalPolicy.STRICT,
        ),
        run_id="hitl-edit",
    )
    assert paused.status == RunStatus.AWAITING_HUMAN
    edited = orchestrator.resume(
        "hitl-edit",
        HumanDecision(
            decision=HumanDecisionType.EDIT,
            reason="narrow the search scope",
            edits={"arguments": {"query": "edited query", "max_results": 2}},
        ),
    )
    assert edited.status == RunStatus.AWAITING_HUMAN
    assert edited.progress.searches == 1
    assert edited.candidates[0].discovered_by_query == "edited query"
    assert edited.decisions[0].action.arguments["query"] == "edited query"
    assert edited.human_decisions[0].edits["arguments"]["query"] == "edited query"


def test_hitl_request_more_evidence_updates_plan_before_replanning():
    from agent.checkpointing import create_memory_checkpointer
    from agent.state_graph import ResearchOrchestrator
    from domain import (
        ApprovalPolicy,
        HumanDecision,
        HumanDecisionType,
        ResearchBrief,
        RunStatus,
    )

    orchestrator = ResearchOrchestrator(
        EvidenceDrivenPolicy(), EvidenceGateway(), checkpointer=create_memory_checkpointer()
    )
    orchestrator.run(
        ResearchBrief(
            topic="broaden evidence",
            research_axes=["evaluation"],
            approval_policy=ApprovalPolicy.STRICT,
        ),
        run_id="hitl-more-evidence",
    )
    replanned = orchestrator.resume(
        "hitl-more-evidence",
        HumanDecision(
            decision=HumanDecisionType.REQUEST_MORE_EVIDENCE,
            reason="add robustness evidence",
            edits={
                "research_axes": ["evaluation", "robustness"],
                "queries": ["agent evaluation", "agent robustness"],
            },
        ),
    )
    assert replanned.status == RunStatus.AWAITING_HUMAN
    assert replanned.plan.axes == ["evaluation", "robustness"]
    assert replanned.plan.queries == ["agent evaluation", "agent robustness"]
    assert replanned.coverage.gaps == ["evaluation", "robustness"]
    assert replanned.progress.searches == 0


def test_structured_llm_policy_returns_typed_action():
    from agent.policies import StructuredLLMDecisionPolicy
    from domain import ActionType, AgentAction, ResearchBrief, ResearchState

    class FakeStructuredModel:
        def invoke(self, prompt):
            assert "不输出隐藏思维链" in prompt
            return AgentAction(
                action_type=ActionType.SEARCH,
                tool_name="search_papers",
                arguments={"query": "agent memory", "max_results": 3},
                reason_summary="当前没有候选论文，需要先检索",
            )

    class FakeLLM:
        def with_structured_output(self, schema, **kwargs):
            assert schema is AgentAction
            assert kwargs["method"] == "function_calling"
            return FakeStructuredModel()

    policy = StructuredLLMDecisionPolicy(FakeLLM())
    action = policy.decide(ResearchState(brief=ResearchBrief(topic="agent memory")))
    assert action.action_type == ActionType.SEARCH
    assert policy.uses_model is True


def test_evidence_package_renderer_uses_state_not_freeform_generation():
    from agent.render import render_evidence_package
    from agent.state_graph import ResearchOrchestrator
    from domain import ResearchBrief

    state = ResearchOrchestrator(EvidenceDrivenPolicy(), EvidenceGateway()).run(
        ResearchBrief(topic="renderer test", research_axes=["evaluation"])
    )
    report = render_evidence_package(state)
    assert "# Research Evidence Package: renderer test" in report
    assert "coverage_satisfied" in report
    assert "Auditable decision trace" in report
    assert "search" in report and "deep_read" in report


def test_agent_v2_offline_replay_matches_expected_states():
    from evaluation.agent_v2_replay import run_agent_v2_backtest

    report = run_agent_v2_backtest()
    assert report["case_count"] == 3
    assert report["expectation_match_rate"] == 1.0
    assert [item["termination_reason"] for item in report["results"]] == [
        "coverage_satisfied",
        "no_progress",
        "max_searches_reached",
    ]


def test_guard_blocks_action_tool_mismatch_before_execution():
    from agent.state_graph import ResearchOrchestrator
    from domain import ActionType, AgentAction, ResearchBrief, RunStatus

    class MismatchedPolicy:
        uses_model = False

        def decide(self, state, last_observation=""):
            return AgentAction(
                action_type=ActionType.SEARCH,
                tool_name="read_paper",
                arguments={"query": state.brief.topic, "max_results": 3},
                reason_summary="故意构造动作与工具不一致的测试输入",
            )

    class NeverExecuteGateway:
        def execute(self, action):
            raise AssertionError("mismatched action must not reach the tool gateway")

    state = ResearchOrchestrator(MismatchedPolicy(), NeverExecuteGateway()).run(
        ResearchBrief(topic="action tool contract")
    )
    assert state.status == RunStatus.AWAITING_HUMAN
    assert state.termination_reason == "action_tool_mismatch"
    assert state.progress.iterations == 0


def test_guard_enforces_source_allowlist_before_read():
    from agent.state_graph import ResearchOrchestrator
    from domain import PaperCandidate, ResearchBrief, RunStatus, ToolResult, ToolStatus

    class DisallowedGateway:
        def __init__(self):
            self.read_called = False

        def execute(self, action):
            if action.tool_name == "search_papers":
                candidate = PaperCandidate(
                    paper_id="blocked:p1",
                    title="Blocked source",
                    url="https://untrusted.example/paper.pdf",
                )
                return ToolResult.from_content(
                    action_id=action.action_id,
                    tool_name=action.tool_name,
                    status=ToolStatus.SUCCESS,
                    content="candidate",
                    payload={"candidates": [candidate.model_dump(mode="json")]},
                )
            self.read_called = True
            raise AssertionError("disallowed source must be blocked before read")

    gateway = DisallowedGateway()
    state = ResearchOrchestrator(EvidenceDrivenPolicy(), gateway).run(
        ResearchBrief(topic="source allowlist", research_axes=["evaluation"])
    )
    assert state.status == RunStatus.AWAITING_HUMAN
    assert state.termination_reason == "source_not_allowed"
    assert state.progress.searches == 1
    assert state.progress.reads == 0
    assert gateway.read_called is False


def test_guard_binds_read_to_canonical_candidate_source():
    from agent.state_graph import ResearchOrchestrator
    from domain import ActionType, AgentAction, EvidenceRef, PaperCandidate, ResearchBrief, ToolResult, ToolStatus

    class TamperedUrlPolicy(EvidenceDrivenPolicy):
        def decide(self, state, last_observation=""):
            action = super().decide(state, last_observation)
            if action.action_type == ActionType.DEEP_READ:
                payload = action.model_dump(mode="json")
                payload["arguments"]["paper_url"] = "https://untrusted.example/override.pdf"
                return AgentAction.model_validate(payload)
            return action

    class BindingGateway:
        def __init__(self):
            self.read_url = None

        def execute(self, action):
            if action.tool_name == "search_papers":
                candidate = PaperCandidate(
                    paper_id="arxiv:2401.00001",
                    title="Canonical candidate",
                    url="https://arxiv.org/abs/2401.00001",
                )
                return ToolResult.from_content(
                    action_id=action.action_id,
                    tool_name=action.tool_name,
                    status=ToolStatus.SUCCESS,
                    content="candidate",
                    payload={"candidates": [candidate.model_dump(mode="json")]},
                )
            self.read_url = action.arguments["paper_url"]
            evidence = EvidenceRef(
                paper_id=action.arguments["paper_id"],
                axis=action.target_axis,
                summary="canonical source evidence",
                source_locator=self.read_url,
            )
            return ToolResult.from_content(
                action_id=action.action_id,
                tool_name=action.tool_name,
                status=ToolStatus.SUCCESS,
                content="evidence",
                payload={"evidence": [evidence.model_dump(mode="json")]},
            )

    gateway = BindingGateway()
    state = ResearchOrchestrator(TamperedUrlPolicy(), gateway).run(
        ResearchBrief(topic="candidate binding", research_axes=["evaluation"])
    )
    assert state.status.value == "completed"
    assert gateway.read_url == "https://arxiv.org/abs/2401.00001"
    assert state.decisions[-1].action.arguments["paper_url"] == gateway.read_url


def test_evidence_merge_deduplicates_same_paper_and_axis():
    from agent.state_graph import ResearchOrchestrator
    from domain import EvidenceRef

    first = EvidenceRef(paper_id="p1", axis="evaluation", summary="first")
    duplicate = EvidenceRef(paper_id="p1", axis="Evaluation", summary="duplicate")
    distinct_axis = EvidenceRef(paper_id="p1", axis="limitations", summary="second axis")
    merged, delta = ResearchOrchestrator._merge_evidence(
        [first], [duplicate, distinct_axis]
    )
    assert len(merged) == 2
    assert delta == 1


def test_expand_citations_consumes_search_limit():
    from agent.state_graph import ResearchOrchestrator
    from domain import ActionType, AgentAction, ResearchBrief, RunLimits, RunStatus

    class ExpandPolicy:
        uses_model = False

        def decide(self, state, last_observation=""):
            return AgentAction(
                action_type=ActionType.EXPAND_CITATIONS,
                tool_name="search_papers",
                arguments={"query": "citation expansion", "max_results": 3},
                reason_summary="扩展候选论文的引用线索",
            )

    state = ResearchOrchestrator(ExpandPolicy(), EmptySearchGateway()).run(
        ResearchBrief(
            topic="citation expansion",
            research_axes=["methods"],
            limits=RunLimits(
                max_iterations=4,
                max_searches=1,
                max_reads=2,
                max_consecutive_no_progress=4,
            ),
        )
    )
    assert state.status == RunStatus.PARTIAL_LIMIT_REACHED
    assert state.termination_reason == "max_searches_reached"
    assert state.progress.searches == 1


if __name__ == "__main__":
    tests = [
        test_research_brief_contract_and_roundtrip,
        test_state_rejects_duplicate_candidate_ids_and_limit_overrun,
        test_tool_result_excludes_raw_content_from_serialization,
        test_legacy_search_text_adapter,
        test_search_adapter_deduplicates_titles_and_prefers_readable_arxiv_url,
        test_tool_gateway_rejects_semantic_scholar_metadata_page_before_fetch,
        test_tool_gateway_parses_explicit_evidence_support,
        test_a3_unknown_evidence_does_not_close_claim_gap,
        test_run_recorder_persists_state_and_decisions,
        test_state_graph_replans_after_tool_observation,
        test_guard_assigns_the_only_open_axis_when_model_omits_target,
        test_non_retryable_read_error_excludes_candidate_from_replanning,
        test_state_graph_stops_after_consecutive_no_progress,
        test_policy_guard_enforces_max_searches,
        test_persistent_hitl_approve_resume_across_sqlite_connections,
        test_hitl_reject_prevents_tool_execution,
        test_hitl_edit_revalidates_and_executes_edited_action,
        test_hitl_request_more_evidence_updates_plan_before_replanning,
        test_structured_llm_policy_returns_typed_action,
        test_evidence_package_renderer_uses_state_not_freeform_generation,
        test_agent_v2_offline_replay_matches_expected_states,
        test_guard_blocks_action_tool_mismatch_before_execution,
        test_guard_enforces_source_allowlist_before_read,
        test_guard_binds_read_to_canonical_candidate_source,
        test_evidence_merge_deduplicates_same_paper_and_axis,
        test_expand_citations_consumes_search_limit,
    ]
    failures = []
    for test in tests:
        try:
            test()
            print(f"  [PASS] {test.__name__}")
        except Exception as exc:
            failures.append(test.__name__)
            print(f"  [FAIL] {test.__name__}: {type(exc).__name__}: {exc}")
    if failures:
        print(f"失败: {failures}")
        raise SystemExit(1)
