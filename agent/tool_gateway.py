"""Research Orchestrator 的受控工具网关与旧工具适配器。"""

from __future__ import annotations

import hashlib
import re
from typing import Iterable

from domain import (
    ActionType,
    AgentAction,
    EvidenceRef,
    EvidenceSupport,
    PaperCandidate,
    ToolResult,
    ToolStatus,
)


_RETRYABLE_PREFIXES = ("[检索超时]", "[检索限流]", "[请求限流]")
_ERROR_PREFIXES = ("[错误]", "[失败]", "[检索失败]", "[提取失败]", "[评估失败]")


def _parse_evidence_support(text: str) -> EvidenceSupport:
    match = re.search(
        r"证据判断\*{0,2}\s*[：:]\s*\*{0,2}\s*"
        r"(supported|partial|contradicted|unknown)\b",
        text,
        flags=re.I,
    )
    if not match:
        return EvidenceSupport.UNKNOWN
    return EvidenceSupport(match.group(1).lower())


def _normalize_candidate_title(title: str) -> str:
    normalized = re.sub(r"^\s*\[PDF\]\s*", "", title, flags=re.I)
    normalized = re.sub(r"\s*\|\s*Semantic Scholar\s*$", "", normalized, flags=re.I)
    return " ".join(normalized.split()).strip()


def _normalize_candidate_url(url: str | None) -> str | None:
    if not url:
        return None
    semantic_arxiv = re.search(
        r"api\.semanticscholar\.org/(?:[^?#]*/)?arXiv:(\d{4}\.\d{4,5}(?:v\d+)?)",
        url,
        flags=re.I,
    )
    if semantic_arxiv:
        return f"https://arxiv.org/abs/{semantic_arxiv.group(1)}"
    return url


def _source_quality(url: str | None) -> int:
    value = (url or "").lower()
    if "arxiv.org/" in value:
        return 4
    if value.endswith(".pdf") or "/pdf/" in value:
        return 3
    if any(host in value for host in ("aclanthology.org", "openreview.net", "proceedings.neurips.cc")):
        return 3
    if "semanticscholar.org/paper/" in value:
        return 0
    return 1


def _canonical_paper_id(title: str, url: str | None) -> str:
    source = _normalize_candidate_url(url) or ""
    match = re.search(r"arxiv\.org/(?:abs|pdf|html)/([^/?#]+)", source, flags=re.I)
    if match:
        arxiv_id = match.group(1).removesuffix(".pdf")
        arxiv_id = re.sub(r"v\d+$", "", arxiv_id, flags=re.I)
        return f"arxiv:{arxiv_id.lower()}"
    title_key = _normalize_candidate_title(title).casefold()
    digest = hashlib.sha256(title_key.encode("utf-8")).hexdigest()
    return f"paper:{digest[:16]}"


def parse_search_candidates(text: str, query: str) -> list[PaperCandidate]:
    """将现有 search tool 的文本输出适配为最小 PaperCandidate。"""

    blocks = re.split(r"(?m)^\[(?:\d+)\]\s+标题:\s*", text)
    by_title: dict[str, PaperCandidate] = {}
    for block in blocks[1:]:
        lines = block.splitlines()
        title = _normalize_candidate_title(lines[0].strip()) if lines else ""
        if not title:
            continue
        url_match = re.search(r"(?m)^\s*链接:\s*(\S+)", block)
        abstract_match = re.search(r"(?m)^\s*摘要:\s*(.+)$", block)
        year_match = re.search(r"年份:\s*(\d{4})", block)
        url = _normalize_candidate_url(url_match.group(1) if url_match else None)
        candidate = PaperCandidate(
            paper_id=_canonical_paper_id(title, url),
            title=title,
            url=url,
            abstract=abstract_match.group(1).strip() if abstract_match else None,
            year=int(year_match.group(1)) if year_match else None,
            discovered_by_query=query,
        )
        title_key = title.casefold()
        current = by_title.get(title_key)
        if current is None or _source_quality(candidate.url) > _source_quality(current.url):
            by_title[title_key] = candidate
    return sorted(by_title.values(), key=lambda item: _source_quality(item.url), reverse=True)


class ToolGateway:
    """只暴露 Research Orchestrator 首版允许的搜索和阅读工具。"""

    def __init__(self, tools: Iterable, allowlist: set[str] | None = None):
        self._tools = {tool.name: tool for tool in tools}
        self.allowlist = allowlist or {"search_papers", "read_paper"}

    def execute(self, action: AgentAction) -> ToolResult:
        if action.action_type not in {
            ActionType.SEARCH,
            ActionType.INSPECT_CANDIDATE,
            ActionType.DEEP_READ,
            ActionType.EXPAND_CITATIONS,
        }:
            return ToolResult.from_content(
                action_id=action.action_id,
                tool_name=action.tool_name or "none",
                status=ToolStatus.ERROR,
                content="action does not execute a tool",
                error_code="action_not_tool_executable",
            )

        tool_name = action.tool_name or ""
        if tool_name not in self.allowlist or tool_name not in self._tools:
            return ToolResult.from_content(
                action_id=action.action_id,
                tool_name=tool_name or "unknown",
                status=ToolStatus.ERROR,
                content="tool is not allowed",
                error_code="tool_not_allowed",
            )

        if tool_name == "read_paper":
            paper_url = str(action.arguments.get("paper_url", ""))
            if "semanticscholar.org/paper/" in paper_url.lower():
                return ToolResult.from_content(
                    action_id=action.action_id,
                    tool_name=tool_name,
                    status=ToolStatus.ERROR,
                    content=(
                        "Semantic Scholar metadata page is not a readable paper source; "
                        "select an arXiv, direct PDF, ACL Anthology, or OpenReview candidate"
                    ),
                    error_code="metadata_page_not_readable",
                    message="candidate must be excluded or replaced with a readable full-text URL",
                )

        try:
            if tool_name == "read_paper":
                tool_arguments = {
                    "paper_url": action.arguments.get("paper_url", ""),
                    "target_claim": action.target_axis or "",
                }
            elif tool_name == "search_papers":
                tool_arguments = {
                    "query": action.arguments.get("query", ""),
                    "max_results": action.arguments.get("max_results", 5),
                }
            else:
                tool_arguments = action.arguments
            raw = str(self._tools[tool_name].invoke(tool_arguments))
        except Exception as exc:
            return ToolResult.from_content(
                action_id=action.action_id,
                tool_name=tool_name,
                status=ToolStatus.ERROR,
                content=str(exc),
                error_code=type(exc).__name__,
                message="tool invocation raised an exception",
            )

        status = ToolStatus.SUCCESS
        error_code = None
        if raw.startswith(_RETRYABLE_PREFIXES):
            status = ToolStatus.RETRYABLE_ERROR
            error_code = "retryable_tool_error"
        elif raw.startswith(_ERROR_PREFIXES):
            status = ToolStatus.ERROR
            error_code = "tool_error"

        payload: dict = {}
        if status == ToolStatus.SUCCESS and tool_name == "search_papers":
            query = str(action.arguments.get("query", ""))
            payload["candidates"] = [
                item.model_dump(mode="json")
                for item in parse_search_candidates(raw, query)
            ]
        elif status == ToolStatus.SUCCESS and tool_name == "read_paper":
            paper_url = str(action.arguments.get("paper_url", ""))
            title = str(action.arguments.get("paper_title", paper_url or "unknown"))
            paper_id = str(
                action.arguments.get("paper_id")
                or _canonical_paper_id(title, paper_url)
            )
            evidence = EvidenceRef(
                paper_id=paper_id,
                axis=action.target_axis or "overview",
                summary=raw[:4000],
                source_locator=paper_url or None,
                support=_parse_evidence_support(raw),
            )
            payload["evidence"] = [evidence.model_dump(mode="json")]

        return ToolResult.from_content(
            action_id=action.action_id,
            tool_name=tool_name,
            status=status,
            content=raw,
            payload=payload,
            error_code=error_code,
        )
