"""Local durable checkpoint factory for Agent v2 HITL runs."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver

from domain import (
    ActionType,
    AgentAction,
    ApprovalPolicy,
    CoverageAxis,
    CoverageState,
    DecisionRecord,
    EvidenceRef,
    EvidenceSupport,
    HumanDecision,
    HumanDecisionType,
    PaperCandidate,
    PaperStatus,
    ProgressState,
    ResearchBrief,
    ResearchPlan,
    ResearchState,
    RunLimits,
    RunStatus,
    TaskType,
    ToolResult,
    ToolStatus,
)


_CHECKPOINT_TYPES = [
    ActionType,
    AgentAction,
    ApprovalPolicy,
    CoverageAxis,
    CoverageState,
    DecisionRecord,
    EvidenceRef,
    EvidenceSupport,
    HumanDecision,
    HumanDecisionType,
    PaperCandidate,
    PaperStatus,
    ProgressState,
    ResearchBrief,
    ResearchPlan,
    ResearchState,
    RunLimits,
    RunStatus,
    TaskType,
    ToolResult,
    ToolStatus,
]


def _create_serializer() -> JsonPlusSerializer:
    return JsonPlusSerializer(
        allowed_json_modules=_CHECKPOINT_TYPES,
        allowed_msgpack_modules=_CHECKPOINT_TYPES,
    )


def create_memory_checkpointer() -> InMemorySaver:
    """Safe allowlisted in-memory saver for deterministic tests."""
    return InMemorySaver(serde=_create_serializer())


def create_sqlite_checkpointer(path: str | Path):
    """Return a sync SQLite saver, its connection, and the resolved DB path.

    The serializer only permits this project's typed domain contracts in addition
    to LangGraph's built-in safe types. The checkpoint file must still be treated
    as trusted local application state.
    """

    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(target), check_same_thread=False)
    return SqliteSaver(connection, serde=_create_serializer()), connection, target
