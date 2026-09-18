"""Validated business contracts, separate from the legacy LangGraph checkpoint."""
from __future__ import annotations

from enum import StrEnum
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ErrorCode(StrEnum):
    INVALID_INPUT = "invalid_input"
    UNAUTHORIZED = "unauthorized"
    NOT_FOUND = "not_found"
    VERSION_CONFLICT = "version_conflict"
    RATE_LIMIT = "rate_limit"
    QUOTA_EXHAUSTED = "quota_exhausted"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    SCHEMA_ERROR = "schema_error"
    NO_EVIDENCE = "no_evidence"
    BUDGET_EXCEEDED = "budget_exceeded"
    CANCELLED = "cancelled"


SkillId = Literal["paper-search", "evidence-qa", "paper-review", "evidence-survey"]


class RunBudget(Contract):
    max_steps: int = Field(default=12, ge=1, le=20)
    max_searches: int = Field(default=3, ge=0, le=6)
    max_new_reads: int = Field(default=3, ge=0, le=5)
    max_retries: int = Field(default=2, ge=0, le=2)
    timeout_seconds: int = Field(default=180, ge=15, le=900)
    max_output_tokens: int = Field(default=2000, ge=64, le=8000)
    # Prices are provider/version dependent. No made-up currency limit.
    max_total_tokens: int = Field(default=40000, ge=1000, le=100000)


class ProjectState(Contract):
    schema_version: Literal[1] = 1
    objective: str = Field(default="", max_length=4000)
    dimensions: list[str] = Field(default_factory=list, max_length=12)
    selected_file_ids: list[UUID] = Field(default_factory=list, max_length=10)
    allow_network: bool = False


class StatePatch(Contract):
    schema_version: Literal[1] = 1
    expected_revision: int = Field(ge=0)
    objective: str | None = Field(default=None, max_length=4000)
    dimensions: list[str] | None = Field(default=None, max_length=12)
    selected_file_ids: list[UUID] | None = Field(default=None, max_length=10)
    allow_network: bool | None = None

    @model_validator(mode="after")
    def requires_change(self):
        changes = self.model_fields_set - {"schema_version", "expected_revision"}
        if not changes or any(getattr(self, field) is None for field in changes):
            raise ValueError("Supply at least one non-null change; use an empty list to clear a list")
        return self


def apply_state_patch(current: ProjectState, revision: int, patch: StatePatch) -> ProjectState:
    """Pure validation; caller must still perform an atomic database revision check."""
    if patch.expected_revision != revision:
        raise ValueError(ErrorCode.VERSION_CONFLICT)
    changes = patch.model_dump(exclude_unset=True, exclude={"schema_version", "expected_revision"})
    return ProjectState.model_validate(current.model_dump() | changes)


class EvidenceClaim(Contract):
    statement: str = Field(min_length=1, max_length=1000)
    source_id: str = Field(min_length=1, max_length=100)
    page: int = Field(ge=1)
    quote: str = Field(min_length=1, max_length=500)


class EvidenceAnswer(Contract):
    answer: str = Field(min_length=1, max_length=6000)
    claims: list[EvidenceClaim] = Field(min_length=1, max_length=6)
    limitations: list[str] = Field(default_factory=list, max_length=6)


class ProjectRevision(Contract):
    project_id: UUID
    owner_id: UUID
    revision: int = Field(ge=0)
    state: ProjectState
    created_at: datetime
    source_message_id: UUID | None = None


class MessageIntent(Contract):
    schema_version: Literal[1] = 1
    kind: Literal["ask", "revise", "execute", "cancel", "clarify"]
    patch: StatePatch | None = None
    question: str | None = Field(default=None, min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_intent(self):
        if (self.kind == "revise") != (self.patch is not None):
            raise ValueError("Only revise requires and accepts a state patch")
        if (self.kind == "clarify") != (self.question is not None):
            raise ValueError("Only clarify requires and accepts a question")
        return self


class FileVersion(Contract):
    file_version_id: UUID
    file_id: UUID
    project_id: UUID
    owner_id: UUID
    version: int = Field(ge=1)
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(gt=0, le=20 * 1024 * 1024)
    kind: Literal["original", "user_note", "generated"]
    media_type: Literal["application/pdf", "text/plain", "text/markdown", "text/csv", "application/json"]
    status: Literal["uploaded", "parsing", "pending_index", "ready", "failed", "revoked", "published"]
    parser_version: str | None = None
    embedding_model: str | None = None
    embedding_dimension: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def ready_requires_index(self):
        if self.status == "ready" and (self.kind == "generated" or not all((self.parser_version, self.embedding_model, self.embedding_dimension))):
            raise ValueError("Search-ready requires parsed original/note content and a completed embedding index")
        if self.status == "published" and self.kind != "generated":
            raise ValueError("Only generated artifacts can be published")
        return self


class SkillRuntime(Contract):
    schema_version: Literal[1] = 1
    skill_id: SkillId
    version: str = Field(min_length=1, max_length=80)
    body_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    tools: list[Literal["retrieve_evidence", "read_chunks", "search_papers", "import_paper", "write_artifact"]] = Field(min_length=1, max_length=5)
    output_type: Literal["search", "answer", "review", "survey"]
    budget: RunBudget = Field(default_factory=RunBudget)


class EvidenceRef(Contract):
    evidence_id: UUID
    project_id: UUID
    file_version_id: UUID
    chunk_id: UUID
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    page: int | None = Field(default=None, ge=1, le=100)
    paragraph: int = Field(ge=1)
    quote: str = Field(min_length=1, max_length=1000)
    source_kind: Literal["original", "user_note"]


class ToolObservation(Contract):
    action_id: UUID
    run_id: UUID
    attempt: int = Field(ge=1)
    tool: str = Field(min_length=1, max_length=80)
    status: Literal["ok", "error", "partial"]
    evidence: list[EvidenceRef] = Field(default_factory=list, max_length=20)
    error_code: ErrorCode | None = None
    summary: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def error_is_explicit(self):
        if self.status == "error" and self.error_code is None:
            raise ValueError("Failed tools must carry a classified error")
        if self.status == "ok" and self.error_code is not None:
            raise ValueError("Successful tools cannot carry an error")
        return self


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_USER = "waiting_user"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"


RUN_TRANSITIONS = {
    RunStatus.QUEUED: {RunStatus.RUNNING, RunStatus.CANCELLED, RunStatus.FAILED},
    RunStatus.RUNNING: {RunStatus.QUEUED, RunStatus.WAITING_USER, RunStatus.CANCELLING, RunStatus.COMPLETED, RunStatus.FAILED},
    RunStatus.WAITING_USER: {RunStatus.QUEUED, RunStatus.CANCELLED, RunStatus.FAILED},
    RunStatus.CANCELLING: {RunStatus.CANCELLED, RunStatus.FAILED},
    RunStatus.CANCELLED: set(), RunStatus.COMPLETED: set(), RunStatus.FAILED: set(),
}


def validate_transition(previous: RunStatus, target: RunStatus):
    if target not in RUN_TRANSITIONS[previous]:
        raise ValueError(f"Invalid run transition: {previous} -> {target}")


class TraceSpan(Contract):
    trace_id: UUID
    span_id: UUID
    parent_span_id: UUID | None = None
    run_id: UUID
    project_id: UUID
    owner_id: UUID
    kind: Literal["run", "model", "tool", "retrieval", "state", "artifact"]
    name: str = Field(min_length=1, max_length=120)
    started_at: datetime
    duration_ms: int | None = Field(default=None, ge=0)
    status: Literal["running", "ok", "error", "cancelled"]
    input_ref: UUID | None = None
    output_ref: UUID | None = None
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    model: str | None = None
    prompt_version: str | None = None
    error_code: ErrorCode | None = None
