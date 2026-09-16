"""显式 Research Orchestrator StateGraph。

与 `agent.core.ResearchCopilotAgent` 的通用 Tool Calling 循环并存：
- 旧实现保留为 B1 baseline；
- 本模块用业务状态、策略守卫、观察归并和确定性终止构成 B2。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import NotRequired, TypedDict
from urllib.parse import urlparse

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from agent.policies import DecisionPolicy, StructuredLLMDecisionPolicy
from agent.tool_gateway import ToolGateway
from domain import (
    ActionType,
    AgentAction,
    CoverageAxis,
    CoverageState,
    DecisionRecord,
    EvidenceRef,
    EvidenceSupport,
    HumanDecision,
    HumanDecisionType,
    PaperCandidate,
    PaperStatus,
    ResearchBrief,
    ResearchPlan,
    ResearchState,
    RunStatus,
    ToolResult,
    ToolStatus,
    TaskType,
)
from domain.schemas import utc_now


class OrchestratorGraphState(TypedDict):
    research_state: ResearchState
    last_action: NotRequired[AgentAction | None]
    last_result: NotRequired[ToolResult | None]
    approval_granted: NotRequired[bool]
    human_route: NotRequired[str]


def _elapsed_seconds(state: ResearchState) -> float:
    try:
        started = datetime.fromisoformat(state.progress.started_at.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    return max(0.0, (datetime.now(timezone.utc) - started).total_seconds())


class ResearchOrchestrator:
    """单 Agent 研究策略控制器；工具执行和状态更新受确定性节点约束。"""

    def __init__(
        self,
        decision_policy: DecisionPolicy,
        tool_gateway: ToolGateway,
        recorder=None,
        checkpointer=None,
    ):
        self.decision_policy = decision_policy
        self.tool_gateway = tool_gateway
        self.recorder = recorder
        self.hitl_enabled = checkpointer is not None
        self.graph = self._build_graph(checkpointer=checkpointer)

    def _build_graph(self, checkpointer=None):
        graph = StateGraph(OrchestratorGraphState)
        graph.add_node("plan", self._plan)
        graph.add_node("choose_action", self._choose_action)
        graph.add_node("policy_guard", self._policy_guard)
        graph.add_node("execute", self._execute)
        graph.add_node("reduce_observation", self._reduce_observation)
        graph.add_node("evaluate_progress", self._evaluate_progress)
        graph.add_node("await_human", self._await_human)
        graph.add_node("finalize", self._finalize)

        graph.add_edge(START, "plan")
        graph.add_edge("plan", "choose_action")
        graph.add_edge("choose_action", "policy_guard")
        graph.add_conditional_edges(
            "policy_guard",
            self._route_after_guard,
            {
                "execute": "execute",
                "await_human": "await_human",
                "finalize": "finalize",
            },
        )
        graph.add_edge("execute", "reduce_observation")
        graph.add_edge("reduce_observation", "evaluate_progress")
        graph.add_conditional_edges(
            "evaluate_progress",
            self._route_after_evaluation,
            {
                "continue": "choose_action",
                "await_human": "await_human",
                "finalize": "finalize",
            },
        )
        graph.add_conditional_edges(
            "await_human",
            self._route_after_human,
            {
                "guard": "policy_guard",
                "choose": "choose_action",
                "finalize": "finalize",
                "pause": END,
            },
        )
        graph.add_edge("finalize", END)
        return graph.compile(checkpointer=checkpointer)

    @staticmethod
    def _default_axes(brief: ResearchBrief) -> list[str]:
        if brief.research_axes:
            return brief.research_axes
        if brief.claims:
            return brief.claims
        return ["research landscape", "core methods", "evaluation", "limitations"]

    def _plan(self, graph_state: OrchestratorGraphState) -> OrchestratorGraphState:
        state = graph_state["research_state"].bumped()
        axes = self._default_axes(state.brief)
        if not state.plan.axes:
            state.plan = ResearchPlan(
                axes=axes,
                queries=[f"{state.brief.topic} {axis}" for axis in axes],
                approved=state.brief.approval_policy.value == "normal",
            )
        state.coverage = self._build_coverage(state)
        state.status = RunStatus.RESEARCHING
        return {"research_state": state, "last_action": None, "last_result": None}

    def _limit_reason(self, state: ResearchState) -> str | None:
        limits = state.brief.limits
        if state.progress.iterations >= limits.max_iterations:
            return "max_iterations_reached"
        if _elapsed_seconds(state) >= limits.timeout_seconds:
            return "timeout_reached"
        return None

    @staticmethod
    def _stop_action(reason: str) -> AgentAction:
        return AgentAction(
            action_type=ActionType.STOP,
            reason_summary=f"确定性运行护栏触发：{reason}",
        )

    @staticmethod
    def _replace_last_decision_action(state: ResearchState, action: AgentAction) -> None:
        if state.decisions:
            state.decisions[-1].action = action

    @staticmethod
    def _source_is_allowed(url: str | None, allowlist: list[str]) -> bool:
        if not url:
            return False
        host = (urlparse(url).hostname or "").casefold().removeprefix("www.")
        for raw_domain in allowlist:
            domain = raw_domain.casefold().strip().removeprefix("www.")
            if host == domain or host.endswith(f".{domain}"):
                return True
        return False

    @staticmethod
    def _pause_for_review(
        state: ResearchState,
        action: AgentAction,
        reason: str,
    ) -> OrchestratorGraphState:
        state.status = RunStatus.AWAITING_HUMAN
        state.termination_reason = reason
        return {
            "research_state": state,
            "last_action": action,
            "approval_granted": False,
        }

    def _choose_action(self, graph_state: OrchestratorGraphState) -> OrchestratorGraphState:
        state = graph_state["research_state"].bumped()
        limit_reason = self._limit_reason(state)
        last_result = graph_state.get("last_result")
        if limit_reason:
            action = self._stop_action(limit_reason)
            state.status = RunStatus.PARTIAL_LIMIT_REACHED
            state.termination_reason = limit_reason
        else:
            action = self.decision_policy.decide(
                state,
                last_observation=last_result.content if last_result else "",
            )
            state.progress.decision_calls += 1
            if getattr(self.decision_policy, "uses_model", False):
                state.progress.model_calls += 1

        state.decisions.append(
            DecisionRecord(
                state_version=state.state_version,
                action=action,
                observed_gap=state.coverage.gaps[0] if state.coverage.gaps else None,
                reason_summary=action.reason_summary,
            )
        )
        return {
            "research_state": state,
            "last_action": action,
            "approval_granted": False,
        }

    def _policy_guard(self, graph_state: OrchestratorGraphState) -> OrchestratorGraphState:
        state = graph_state["research_state"].bumped()
        action = graph_state.get("last_action")
        if action is None:
            state.status = RunStatus.FAILED
            state.termination_reason = "missing_action"
            return {"research_state": state, "last_action": self._stop_action("missing_action")}

        if action.action_type in {ActionType.INSPECT_CANDIDATE, ActionType.DEEP_READ}:
            if not action.target_axis and len(state.coverage.gaps) == 1:
                action = AgentAction.model_validate(
                    {
                        **action.model_dump(mode="json"),
                        "target_axis": state.coverage.gaps[0],
                    }
                )
                self._replace_last_decision_action(state, action)
            if not action.target_axis or action.target_axis not in state.plan.axes:
                return self._pause_for_review(
                    state, action, "target_axis_review_required"
                )

        action_tool_contract = {
            ActionType.SEARCH: "search_papers",
            ActionType.EXPAND_CITATIONS: "search_papers",
            ActionType.INSPECT_CANDIDATE: "read_paper",
            ActionType.DEEP_READ: "read_paper",
        }
        expected_tool = action_tool_contract.get(action.action_type)
        if expected_tool is not None and action.tool_name != expected_tool:
            return self._pause_for_review(state, action, "action_tool_mismatch")

        if action.action_type in {ActionType.SEARCH, ActionType.EXPAND_CITATIONS}:
            query = str(action.arguments.get("query", "")).strip()
            max_results = action.arguments.get("max_results", 5)
            if not query or not isinstance(max_results, int) or not 1 <= max_results <= 20:
                return self._pause_for_review(
                    state, action, "invalid_search_arguments"
                )
            action = AgentAction.model_validate(
                {
                    **action.model_dump(mode="json"),
                    "arguments": {"query": query, "max_results": max_results},
                }
            )
            self._replace_last_decision_action(state, action)

        if action.action_type in {ActionType.INSPECT_CANDIDATE, ActionType.DEEP_READ}:
            paper_id = str(action.arguments.get("paper_id", "")).strip()
            candidate = next(
                (item for item in state.candidates if item.paper_id == paper_id),
                None,
            )
            if candidate is None:
                return self._pause_for_review(
                    state, action, "candidate_review_required"
                )
            if (
                candidate.status in {PaperStatus.READ, PaperStatus.EXCLUDED}
                or candidate.paper_id in state.selected_paper_ids
            ):
                return self._pause_for_review(
                    state, action, "candidate_already_processed"
                )
            if not self._source_is_allowed(
                candidate.url, state.brief.source_allowlist
            ):
                return self._pause_for_review(
                    state, action, "source_not_allowed"
                )
            action = AgentAction.model_validate(
                {
                    **action.model_dump(mode="json"),
                    "arguments": {
                        "paper_url": candidate.url,
                        "paper_id": candidate.paper_id,
                        "paper_title": candidate.title,
                    },
                }
            )
            self._replace_last_decision_action(state, action)

        reason = None
        if action.action_type in {ActionType.SEARCH, ActionType.EXPAND_CITATIONS}:
            if state.progress.searches >= state.brief.limits.max_searches:
                reason = "max_searches_reached"
        elif action.action_type in {ActionType.INSPECT_CANDIDATE, ActionType.DEEP_READ}:
            if state.progress.reads >= state.brief.limits.max_reads:
                reason = "max_reads_reached"

        if reason:
            guarded = self._stop_action(reason)
            state.status = RunStatus.PARTIAL_LIMIT_REACHED
            state.termination_reason = reason
            state.decisions.append(
                DecisionRecord(
                    state_version=state.state_version,
                    action=guarded,
                    alternatives_considered=[action.action_type],
                    observed_gap=state.coverage.gaps[0] if state.coverage.gaps else None,
                    reason_summary=guarded.reason_summary,
                )
            )
            return {"research_state": state, "last_action": guarded}

        tool_actions = {
            ActionType.SEARCH,
            ActionType.INSPECT_CANDIDATE,
            ActionType.DEEP_READ,
            ActionType.EXPAND_CITATIONS,
        }
        strict_review = (
            state.brief.approval_policy.value == "strict"
            and action.action_type in tool_actions
        )
        if (
            action.needs_approval
            or strict_review
            or action.action_type in {
            ActionType.CLARIFY_BRIEF,
            ActionType.REQUEST_HUMAN,
            }
        ) and not graph_state.get("approval_granted", False):
            state.status = RunStatus.AWAITING_HUMAN
            state.termination_reason = "human_decision_required"
        return {
            "research_state": state,
            "last_action": action,
            "approval_granted": False,
        }

    @staticmethod
    def _route_after_guard(graph_state: OrchestratorGraphState) -> str:
        state = graph_state["research_state"]
        action = graph_state.get("last_action")
        if state.status == RunStatus.AWAITING_HUMAN:
            return "await_human"
        if state.status == RunStatus.FAILED or action is None or action.action_type == ActionType.STOP:
            return "finalize"
        return "execute"

    def _execute(self, graph_state: OrchestratorGraphState) -> OrchestratorGraphState:
        action = graph_state["last_action"]
        result = self.tool_gateway.execute(action)
        return {"last_result": result}

    @staticmethod
    def _merge_candidates(
        existing: list[PaperCandidate], incoming: list[PaperCandidate]
    ) -> tuple[list[PaperCandidate], int]:
        by_id = {item.paper_id: item.model_copy(deep=True) for item in existing}
        before = len(by_id)
        for candidate in incoming:
            if candidate.paper_id not in by_id:
                by_id[candidate.paper_id] = candidate
        return list(by_id.values()), len(by_id) - before

    @staticmethod
    def _merge_evidence(
        existing: list[EvidenceRef], incoming: list[EvidenceRef]
    ) -> tuple[list[EvidenceRef], int]:
        by_key = {
            (item.paper_id.casefold(), item.axis.casefold()): item.model_copy(deep=True)
            for item in existing
        }
        before = len(by_key)
        for evidence in incoming:
            key = (evidence.paper_id.casefold(), evidence.axis.casefold())
            if key not in by_key:
                by_key[key] = evidence
        return list(by_key.values()), len(by_key) - before

    def _reduce_observation(self, graph_state: OrchestratorGraphState) -> OrchestratorGraphState:
        state = graph_state["research_state"].bumped()
        action = graph_state["last_action"]
        result = graph_state["last_result"]
        state.status = RunStatus.EVALUATING
        state.progress.iterations += 1

        if action.action_type in {ActionType.SEARCH, ActionType.EXPAND_CITATIONS}:
            state.progress.searches += 1
        elif action.action_type in {ActionType.INSPECT_CANDIDATE, ActionType.DEEP_READ}:
            state.progress.reads += 1

        new_items = 0
        if result.status == ToolStatus.SUCCESS:
            candidates = [
                PaperCandidate.model_validate(item)
                for item in result.payload.get("candidates", [])
            ]
            evidence = [
                EvidenceRef.model_validate(item)
                for item in result.payload.get("evidence", [])
            ]
            state.candidates, candidate_delta = self._merge_candidates(
                state.candidates, candidates
            )
            state.evidence, evidence_delta = self._merge_evidence(state.evidence, evidence)
            new_items = candidate_delta + evidence_delta

            if evidence:
                paper_ids = {item.paper_id for item in evidence}
                for paper_id in paper_ids:
                    if paper_id not in state.selected_paper_ids:
                        state.selected_paper_ids.append(paper_id)
                for candidate in state.candidates:
                    if candidate.paper_id in paper_ids:
                        candidate.status = PaperStatus.READ
        else:
            state.errors.append(
                f"{result.tool_name}:{result.error_code or result.status.value}"
            )
            if (
                action.action_type in {ActionType.INSPECT_CANDIDATE, ActionType.DEEP_READ}
                and result.status == ToolStatus.ERROR
            ):
                failed_paper_id = str(action.arguments.get("paper_id", ""))
                for candidate in state.candidates:
                    if candidate.paper_id == failed_paper_id:
                        candidate.status = PaperStatus.EXCLUDED

        if new_items > 0:
            state.progress.consecutive_no_progress = 0
        else:
            state.progress.consecutive_no_progress += 1

        if state.decisions:
            state.decisions[-1].result_status = result.status
        return {"research_state": state}

    @staticmethod
    def _build_coverage(state: ResearchState) -> CoverageState:
        required = state.brief.required_evidence_per_axis
        axes: list[CoverageAxis] = []
        gaps: list[str] = []
        for axis in state.plan.axes:
            axis_evidence = [
                item
                for item in state.evidence
                if item.axis.casefold() == axis.casefold()
            ]
            if state.brief.task_type == TaskType.EVIDENCE_GAP_CLOSURE:
                axis_evidence = [
                    item
                    for item in axis_evidence
                    if item.support != EvidenceSupport.UNKNOWN
                ]
            count = len(axis_evidence)
            sufficient = count >= required
            axes.append(CoverageAxis(axis=axis, evidence_count=count, sufficient=sufficient))
            if not sufficient:
                gaps.append(axis)
        return CoverageState(axes=axes, gaps=gaps, updated_at=utc_now())

    def _evaluate_progress(self, graph_state: OrchestratorGraphState) -> OrchestratorGraphState:
        state = graph_state["research_state"].bumped()
        state.coverage = self._build_coverage(state)
        limits = state.brief.limits

        if state.coverage.complete:
            state.status = RunStatus.COMPLETED
            state.termination_reason = "coverage_satisfied"
        elif state.progress.consecutive_no_progress >= limits.max_consecutive_no_progress:
            state.status = RunStatus.PARTIAL_NO_PROGRESS
            state.termination_reason = "no_progress"
        elif state.progress.iterations >= limits.max_iterations:
            state.status = RunStatus.PARTIAL_LIMIT_REACHED
            state.termination_reason = "max_iterations_reached"
        elif _elapsed_seconds(state) >= limits.timeout_seconds:
            state.status = RunStatus.PARTIAL_LIMIT_REACHED
            state.termination_reason = "timeout_reached"
        else:
            state.status = RunStatus.RESEARCHING
        return {"research_state": state}

    @staticmethod
    def _route_after_evaluation(graph_state: OrchestratorGraphState) -> str:
        status = graph_state["research_state"].status
        if status == RunStatus.RESEARCHING:
            return "continue"
        if status == RunStatus.AWAITING_HUMAN:
            return "await_human"
        return "finalize"

    @staticmethod
    def _edit_action(action: AgentAction, edits: dict) -> AgentAction:
        allowed = {
            "tool_name",
            "arguments",
            "target_axis",
            "reason_summary",
            "expected_evidence_gain",
        }
        unknown = set(edits) - allowed
        if unknown:
            raise ValueError(f"unsupported action edit fields: {sorted(unknown)}")
        payload = action.model_dump(mode="json")
        payload.update(edits)
        payload["needs_approval"] = False
        return AgentAction.model_validate(payload)

    def _await_human(self, graph_state: OrchestratorGraphState) -> OrchestratorGraphState:
        state = graph_state["research_state"].bumped()
        state.status = RunStatus.AWAITING_HUMAN
        state.termination_reason = state.termination_reason or "human_decision_required"
        action = graph_state.get("last_action")
        if not self.hitl_enabled:
            return {"research_state": state, "human_route": "pause"}

        payload = {
            "type": "research_action_review",
            "run_id": state.run_id,
            "state_version": state.state_version,
            "reason": state.termination_reason,
            "action": action.model_dump(mode="json") if action else None,
            "coverage_gaps": list(state.coverage.gaps),
            "allowed_decisions": [item.value for item in HumanDecisionType],
        }
        response = interrupt(payload)
        decision = HumanDecision.model_validate(response)
        state.human_decisions.append(decision)

        if decision.decision == HumanDecisionType.REJECT:
            state.status = RunStatus.PARTIAL_NO_PROGRESS
            state.termination_reason = "human_rejected_action"
            return {"research_state": state, "human_route": "finalize"}

        if decision.decision == HumanDecisionType.REQUEST_MORE_EVIDENCE:
            unknown_edits = set(decision.edits) - {"research_axes", "queries"}
            if unknown_edits:
                raise ValueError(
                    f"unsupported request_more_evidence fields: {sorted(unknown_edits)}"
                )
            axes = decision.edits.get("research_axes")
            queries = decision.edits.get("queries")
            if axes is not None:
                if not isinstance(axes, list) or not all(isinstance(item, str) for item in axes):
                    raise ValueError("research_axes edit must be a list of strings")
                normalized_axes = list(
                    dict.fromkeys(item.strip() for item in axes if item.strip())
                )
                if not normalized_axes:
                    raise ValueError("research_axes edit must contain at least one axis")
                state.plan.axes = normalized_axes
                state.coverage = self._build_coverage(state)
            if queries is not None:
                if not isinstance(queries, list) or not all(isinstance(item, str) for item in queries):
                    raise ValueError("queries edit must be a list of strings")
                state.plan.queries = list(
                    dict.fromkeys(item.strip() for item in queries if item.strip())
                )
            state.status = RunStatus.RESEARCHING
            state.termination_reason = None
            return {
                "research_state": state,
                "last_action": None,
                "approval_granted": False,
                "human_route": "choose",
            }

        if action is None:
            state.status = RunStatus.FAILED
            state.termination_reason = "human_review_missing_action"
            return {"research_state": state, "human_route": "finalize"}

        if decision.decision == HumanDecisionType.EDIT:
            action = self._edit_action(action, decision.edits)
        else:
            action = AgentAction.model_validate(
                {**action.model_dump(mode="json"), "needs_approval": False}
            )

        state.status = RunStatus.RESEARCHING
        state.termination_reason = None
        if action.action_type in {ActionType.CLARIFY_BRIEF, ActionType.REQUEST_HUMAN}:
            return {
                "research_state": state,
                "last_action": None,
                "approval_granted": False,
                "human_route": "choose",
            }
        return {
            "research_state": state,
            "last_action": action,
            "approval_granted": True,
            "human_route": "guard",
        }

    @staticmethod
    def _route_after_human(graph_state: OrchestratorGraphState) -> str:
        return graph_state.get("human_route", "pause")

    @staticmethod
    def _finalize(graph_state: OrchestratorGraphState) -> OrchestratorGraphState:
        state = graph_state["research_state"].bumped()
        if state.status in {RunStatus.RESEARCHING, RunStatus.EVALUATING}:
            if state.coverage.complete:
                state.status = RunStatus.COMPLETED
                state.termination_reason = "coverage_satisfied"
            else:
                state.status = RunStatus.PARTIAL_NO_PROGRESS
                state.termination_reason = state.termination_reason or "policy_stop"
        return {"research_state": state}

    @staticmethod
    def _interrupt_payload(result: dict) -> dict | None:
        interrupts = result.get("__interrupt__", ())
        if not interrupts:
            return None
        value = getattr(interrupts[0], "value", None)
        return value if isinstance(value, dict) else {"reason": str(value)}

    def _graph_config(self, thread_id: str, recursion_limit: int = 200) -> dict:
        config: dict = {"recursion_limit": recursion_limit}
        if self.hitl_enabled:
            config["configurable"] = {"thread_id": thread_id}
        return config

    def _state_from_result(self, result: dict, config: dict) -> ResearchState:
        state = result.get("research_state")
        if isinstance(state, ResearchState):
            return state
        if self.hitl_enabled:
            snapshot = self.graph.get_state(config)
            snapshot_state = snapshot.values.get("research_state")
            if isinstance(snapshot_state, ResearchState):
                return snapshot_state
            if snapshot_state is not None:
                return ResearchState.model_validate(snapshot_state)
        raise RuntimeError("graph result does not contain ResearchState")

    @staticmethod
    def _summary(state: ResearchState) -> dict:
        return {
            "termination_reason": state.termination_reason,
            "status": state.status.value,
            "iterations": state.progress.iterations,
            "searches": state.progress.searches,
            "reads": state.progress.reads,
            "decision_calls": state.progress.decision_calls,
            "model_calls": state.progress.model_calls,
            "candidate_count": len(state.candidates),
            "evidence_count": len(state.evidence),
            "human_decision_count": len(state.human_decisions),
        }

    def _persist_result(self, session, state: ResearchState, interrupt_payload=None) -> None:
        if session is None:
            return
        serialized = state.model_dump(mode="json")
        session.write_state(serialized)
        session.write_decisions(
            [item.model_dump(mode="json") for item in state.decisions]
        )
        if interrupt_payload is not None:
            session.pause(serialized, interrupt_payload)
            return
        session.finish(
            self._summary(state),
            status=(
                "completed"
                if state.status == RunStatus.COMPLETED
                else state.status.value
            ),
        )

    def run(self, brief: ResearchBrief, run_id: str | None = None) -> ResearchState:
        state = ResearchState(run_id=run_id or ResearchState(brief=brief).run_id, brief=brief)
        session = None
        if self.recorder is not None:
            session = self.recorder.start_run(
                "agent_survey_v2",
                brief.topic,
                versions={"state_schema": state.schema_version},
                run_id=state.run_id,
            )
        config = self._graph_config(
            state.run_id,
            recursion_limit=max(30, brief.limits.max_iterations * 8 + 10),
        )
        try:
            result = self.graph.invoke(
                {
                    "research_state": state,
                    "last_action": None,
                    "last_result": None,
                    "approval_granted": False,
                },
                config=config,
            )
            final_state = self._state_from_result(result, config)
            self._persist_result(
                session,
                final_state,
                interrupt_payload=self._interrupt_payload(result),
            )
            return final_state
        except Exception as exc:
            if session is not None:
                session.fail(type(exc).__name__, str(exc))
            raise

    def resume(self, run_id: str, decision: HumanDecision) -> ResearchState:
        """Resume a persisted interrupt with an explicit human decision."""
        if not self.hitl_enabled:
            raise RuntimeError("resume requires a configured checkpointer")
        session = self.recorder.resume_run(run_id) if self.recorder is not None else None
        config = self._graph_config(run_id)
        try:
            if session is not None:
                session.resume()
            result = self.graph.invoke(
                Command(resume=decision.model_dump(mode="json")),
                config=config,
            )
            final_state = self._state_from_result(result, config)
            self._persist_result(
                session,
                final_state,
                interrupt_payload=self._interrupt_payload(result),
            )
            return final_state
        except Exception as exc:
            if session is not None:
                session.fail(type(exc).__name__, str(exc))
            raise


def create_default_orchestrator(recorder=None, checkpointer=None) -> ResearchOrchestrator:
    """构建使用当前模型和现有搜索/阅读工具的 Agent v2。"""

    from tools.search_papers import create_search_papers_tool
    from tools.read_paper import create_read_paper_tool
    from utils.llm import make_chat_llm

    # v2 首版只暴露策略必需的搜索与阅读工具；状态合并和覆盖判断不作为工具。
    tools = [create_search_papers_tool(), create_read_paper_tool()]
    return ResearchOrchestrator(
        decision_policy=StructuredLLMDecisionPolicy(make_chat_llm()),
        tool_gateway=ToolGateway(tools),
        recorder=recorder,
        checkpointer=checkpointer,
    )
