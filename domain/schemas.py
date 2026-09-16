"""Research Copilot Agent v2 的类型化任务合同与业务状态。

这些对象是可审计的业务事实源。LangChain/LangGraph 的消息历史只用于模型上下文，
不能替代任务、候选论文、证据覆盖、运行进度和终止原因。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ContractModel(BaseModel):
    """所有跨阶段合同的共同配置。"""

    model_config = ConfigDict(extra="forbid", use_enum_values=False)


class TaskType(str, Enum):
    OPEN_SURVEY = "open_survey"
    ADAPTIVE_PAPER_SELECTION = "adaptive_paper_selection"
    EVIDENCE_GAP_CLOSURE = "evidence_gap_closure"


class ApprovalPolicy(str, Enum):
    NORMAL = "normal"
    STRICT = "strict"


class RunStatus(str, Enum):
    PLANNING = "planning"
    RESEARCHING = "researching"
    EVALUATING = "evaluating"
    AWAITING_HUMAN = "awaiting_human"
    COMPLETED = "completed"
    PARTIAL_LIMIT_REACHED = "partial_limit_reached"
    PARTIAL_NO_PROGRESS = "partial_no_progress"
    PARTIAL_SOURCE_UNAVAILABLE = "partial_source_unavailable"
    FAILED = "failed"


class ActionType(str, Enum):
    CLARIFY_BRIEF = "clarify_brief"
    SEARCH = "search"
    INSPECT_CANDIDATE = "inspect_candidate"
    DEEP_READ = "deep_read"
    EXPAND_CITATIONS = "expand_citations"
    REQUEST_HUMAN = "request_human"
    STOP = "stop"


class ToolStatus(str, Enum):
    SUCCESS = "success"
    RETRYABLE_ERROR = "retryable_error"
    ERROR = "error"
    NEEDS_HUMAN = "needs_human"


class PaperStatus(str, Enum):
    CANDIDATE = "candidate"
    SELECTED = "selected"
    READ = "read"
    EXCLUDED = "excluded"


class EvidenceSupport(str, Enum):
    SUPPORTED = "supported"
    PARTIAL = "partial"
    CONTRADICTED = "contradicted"
    UNKNOWN = "unknown"


class HumanDecisionType(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    EDIT = "edit"
    REQUEST_MORE_EVIDENCE = "request_more_evidence"


class RunLimits(ContractModel):
    """防止循环失控的运行护栏，不作为产品的“成本预算”卖点。"""

    max_iterations: int = Field(default=12, ge=1, le=100)
    max_searches: int = Field(default=6, ge=1, le=50)
    max_reads: int = Field(default=5, ge=1, le=50)
    timeout_seconds: int = Field(default=900, ge=10, le=86_400)
    max_consecutive_no_progress: int = Field(default=2, ge=1, le=10)


class ResearchBrief(ContractModel):
    schema_version: str = "1.0"
    task_type: TaskType = TaskType.OPEN_SURVEY
    topic: str = Field(min_length=3, max_length=500)
    research_axes: list[str] = Field(default_factory=list, max_length=12)
    claims: list[str] = Field(default_factory=list, max_length=30)
    source_allowlist: list[str] = Field(
        default_factory=lambda: [
            "arxiv.org",
            "semanticscholar.org",
            "aclanthology.org",
            "openreview.net",
        ]
    )
    required_evidence_per_axis: int = Field(default=1, ge=1, le=10)
    limits: RunLimits = Field(default_factory=RunLimits)
    approval_policy: ApprovalPolicy = ApprovalPolicy.NORMAL

    @field_validator("topic")
    @classmethod
    def normalize_topic(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if len(normalized) < 3:
            raise ValueError("topic must contain at least 3 non-whitespace characters")
        return normalized

    @field_validator("research_axes", "claims", "source_allowlist")
    @classmethod
    def normalize_unique_list(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for raw in values:
            value = " ".join(str(raw).split()).strip()
            if not value:
                continue
            key = value.casefold()
            if key not in seen:
                normalized.append(value)
                seen.add(key)
        return normalized

    @model_validator(mode="after")
    def validate_task_contract(self) -> "ResearchBrief":
        if self.task_type == TaskType.EVIDENCE_GAP_CLOSURE and not self.claims:
            raise ValueError("evidence_gap_closure requires at least one claim")
        return self


class ResearchPlan(ContractModel):
    version: int = Field(default=1, ge=1)
    axes: list[str] = Field(default_factory=list)
    queries: list[str] = Field(default_factory=list)
    approved: bool = False


class PaperCandidate(ContractModel):
    paper_id: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=1000)
    url: str | None = None
    abstract: str | None = None
    year: int | None = Field(default=None, ge=1800, le=2200)
    status: PaperStatus = PaperStatus.CANDIDATE
    discovered_by_query: str | None = None


class EvidenceRef(ContractModel):
    evidence_id: str = Field(default_factory=lambda: f"ev-{uuid.uuid4().hex[:12]}")
    paper_id: str = Field(min_length=1, max_length=160)
    axis: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=4000)
    source_locator: str | None = None
    support: EvidenceSupport = EvidenceSupport.UNKNOWN


class CoverageAxis(ContractModel):
    axis: str
    evidence_count: int = Field(default=0, ge=0)
    sufficient: bool = False


class CoverageState(ContractModel):
    axes: list[CoverageAxis] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    updated_at: str = Field(default_factory=utc_now)

    @property
    def complete(self) -> bool:
        return bool(self.axes) and all(item.sufficient for item in self.axes)


class ProgressState(ContractModel):
    iterations: int = Field(default=0, ge=0)
    searches: int = Field(default=0, ge=0)
    reads: int = Field(default=0, ge=0)
    decision_calls: int = Field(default=0, ge=0)
    model_calls: int = Field(default=0, ge=0)
    consecutive_no_progress: int = Field(default=0, ge=0)
    started_at: str = Field(default_factory=utc_now)


class AgentAction(ContractModel):
    action_id: str = Field(default_factory=lambda: f"act-{uuid.uuid4().hex[:12]}")
    action_type: ActionType
    tool_name: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    target_axis: str | None = None
    reason_summary: str = Field(min_length=3, max_length=1000)
    expected_evidence_gain: str = Field(default="unknown", pattern="^(low|medium|high|unknown)$")
    needs_approval: bool = False

    @model_validator(mode="after")
    def validate_action_contract(self) -> "AgentAction":
        tool_actions = {
            ActionType.SEARCH,
            ActionType.INSPECT_CANDIDATE,
            ActionType.DEEP_READ,
            ActionType.EXPAND_CITATIONS,
        }
        if self.action_type in tool_actions and not self.tool_name:
            raise ValueError(f"{self.action_type.value} requires tool_name")
        if self.action_type in {ActionType.STOP, ActionType.REQUEST_HUMAN} and self.tool_name:
            raise ValueError(f"{self.action_type.value} cannot specify tool_name")
        return self


class ToolResult(ContractModel):
    action_id: str
    tool_name: str
    status: ToolStatus
    preview: str = Field(default="", max_length=1000)
    content_sha256: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    message: str | None = None
    # Agent 当轮可以读取原始 observation，但持久化 model_dump/json 时默认排除。
    content: str = Field(default="", exclude=True, repr=False)

    @classmethod
    def from_content(
        cls,
        *,
        action_id: str,
        tool_name: str,
        status: ToolStatus,
        content: str,
        payload: dict[str, Any] | None = None,
        error_code: str | None = None,
        message: str | None = None,
    ) -> "ToolResult":
        normalized = str(content)
        content_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        return cls(
            action_id=action_id,
            tool_name=tool_name,
            status=status,
            preview=f"{len(normalized)} chars; sha256={content_hash[:16]}",
            content_sha256=content_hash,
            payload=payload or {},
            error_code=error_code,
            message=message,
            content=normalized,
        )


class DecisionRecord(ContractModel):
    decision_id: str = Field(default_factory=lambda: f"dec-{uuid.uuid4().hex[:12]}")
    state_version: int = Field(ge=0)
    action: AgentAction
    alternatives_considered: list[ActionType] = Field(default_factory=list)
    observed_gap: str | None = None
    reason_summary: str
    result_status: ToolStatus | None = None
    created_at: str = Field(default_factory=utc_now)


class HumanDecision(ContractModel):
    decision: HumanDecisionType
    actor: str = "user"
    reason: str | None = None
    edits: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now)


class ResearchState(ContractModel):
    schema_version: str = "1.0"
    run_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    state_version: int = Field(default=0, ge=0)
    brief: ResearchBrief
    plan: ResearchPlan = Field(default_factory=ResearchPlan)
    candidates: list[PaperCandidate] = Field(default_factory=list)
    selected_paper_ids: list[str] = Field(default_factory=list)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    coverage: CoverageState = Field(default_factory=CoverageState)
    progress: ProgressState = Field(default_factory=ProgressState)
    decisions: list[DecisionRecord] = Field(default_factory=list)
    human_decisions: list[HumanDecision] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    status: RunStatus = RunStatus.PLANNING
    termination_reason: str | None = None
    updated_at: str = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_invariants(self) -> "ResearchState":
        candidate_ids = [item.paper_id for item in self.candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("candidate paper_id values must be unique")
        if len(self.selected_paper_ids) != len(set(self.selected_paper_ids)):
            raise ValueError("selected_paper_ids must be unique")
        unknown_selected = set(self.selected_paper_ids) - set(candidate_ids)
        if unknown_selected:
            raise ValueError(
                f"selected_paper_ids must reference candidates: {sorted(unknown_selected)}"
            )
        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("evidence_id values must be unique")
        limits = self.brief.limits
        if self.progress.iterations > limits.max_iterations:
            raise ValueError("iterations exceed configured run limit")
        if self.progress.searches > limits.max_searches:
            raise ValueError("searches exceed configured run limit")
        if self.progress.reads > limits.max_reads:
            raise ValueError("reads exceed configured run limit")
        return self

    def safe_digest(self) -> str:
        payload = self.model_dump(mode="json")
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def bumped(self) -> "ResearchState":
        clone = self.model_copy(deep=True)
        clone.state_version += 1
        clone.updated_at = utc_now()
        return clone
