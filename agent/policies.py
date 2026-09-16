"""Research Orchestrator 的下一动作策略。

`StructuredLLMDecisionPolicy` 是实际 Agent 决策入口；
`DeterministicResearchPolicy` 只用于离线 smoke/B0 对照，不能对外宣称为模型 Agent。
"""

from __future__ import annotations

import json
from typing import Protocol

from domain import (
    ActionType,
    AgentAction,
    PaperStatus,
    ResearchState,
    RunStatus,
)


class DecisionPolicy(Protocol):
    def decide(self, state: ResearchState, last_observation: str = "") -> AgentAction:
        """根据当前业务状态返回一个类型化的下一动作。"""


class DeterministicResearchPolicy:
    """纯离线可复现策略，仅用于测试显式状态图和固定基线。"""

    uses_model = False

    def decide(self, state: ResearchState, last_observation: str = "") -> AgentAction:
        if state.coverage.complete:
            return AgentAction(
                action_type=ActionType.STOP,
                reason_summary="所有研究轴已经达到最低证据要求",
            )

        unread = [
            paper
            for paper in state.candidates
            if paper.status not in {PaperStatus.READ, PaperStatus.EXCLUDED}
            and paper.paper_id not in state.selected_paper_ids
        ]
        if unread:
            paper = unread[0]
            target_axis = state.coverage.gaps[0] if state.coverage.gaps else state.plan.axes[0]
            return AgentAction(
                action_type=ActionType.DEEP_READ,
                tool_name="read_paper",
                arguments={
                    "paper_url": paper.url or "",
                    "paper_id": paper.paper_id,
                    "paper_title": paper.title,
                },
                target_axis=target_axis,
                reason_summary=f"阅读候选论文 {paper.title} 以补充 {target_axis} 证据",
                expected_evidence_gain="high",
            )

        query_index = min(state.progress.searches, max(len(state.plan.queries) - 1, 0))
        query = (
            state.plan.queries[query_index]
            if state.plan.queries
            else state.brief.topic
        )
        return AgentAction(
            action_type=ActionType.SEARCH,
            tool_name="search_papers",
            arguments={"query": query, "max_results": 5},
            target_axis=state.coverage.gaps[0] if state.coverage.gaps else None,
            reason_summary="当前没有可阅读候选论文，继续检索未覆盖方向",
            expected_evidence_gain="medium",
        )


class StructuredLLMDecisionPolicy:
    """使用模型 Structured Output 选择下一研究动作。"""

    uses_model = True

    def __init__(self, llm, structured_method: str = "function_calling"):
        # DeepSeek V4 Chat Completions 当前支持 Tool Calls，但不接受 OpenAI
        # `json_schema` response_format；function_calling 同时兼容现有 provider。
        self._model = llm.with_structured_output(
            AgentAction,
            method=structured_method,
        )

    def decide(self, state: ResearchState, last_observation: str = "") -> AgentAction:
        allowed_actions = [item.value for item in ActionType]
        compact_state = {
            "task_type": state.brief.task_type.value,
            "topic": state.brief.topic,
            "research_axes": state.plan.axes,
            "queries": state.plan.queries,
            "candidate_papers": [
                {
                    "paper_id": item.paper_id,
                    "title": item.title,
                    "url": item.url,
                    "status": item.status.value,
                }
                for item in state.candidates[:30]
            ],
            "selected_paper_ids": state.selected_paper_ids,
            "coverage": state.coverage.model_dump(mode="json"),
            "progress": state.progress.model_dump(mode="json"),
            "run_limits": state.brief.limits.model_dump(mode="json"),
            "latest_human_decision": (
                state.human_decisions[-1].model_dump(mode="json")
                if state.human_decisions
                else None
            ),
            "last_observation": last_observation[:3000],
        }
        prompt = f"""你是 Research Copilot 的研究策略控制器。
只决定下一项研究动作，不生成最终综述，也不执行工具。

允许的 action_type：{allowed_actions}
允许的工具：search_papers、read_paper。

规则：
1. 下一步必须由当前候选论文、证据覆盖或上一次工具观察支持。
2. search_papers 参数使用 query/max_results；read_paper 至少提供 paper_url，并同时回传候选的 paper_id/paper_title 供状态归并。
3. 精读优先选择 arXiv、直链 PDF、ACL Anthology 或 OpenReview；Semantic Scholar `/paper/` 是元数据页，不得作为全文来源。不得选择 status=excluded 的候选。
4. 不得绕过运行上限、来源规则或人工审批。
5. 已覆盖全部研究轴时选择 stop；无法判断关键冲突时选择 request_human。
6. reason_summary 只写可审计的简短决策依据，不输出隐藏思维链。

当前状态（JSON）：
{json.dumps(compact_state, ensure_ascii=False)}
"""
        result = self._model.invoke(prompt)
        if isinstance(result, AgentAction):
            return result
        return AgentAction.model_validate(result)
