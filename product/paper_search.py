"""Bounded, traceable academic discovery for the explicit paper-search Skill."""
from __future__ import annotations

from urllib.parse import urlparse
from uuid import uuid4

import httpx
from psycopg.types.json import Jsonb

import config
from product.db import connect
from product.execution import record_action
from product.llm import ModelAnswer, classify_error
from product.rag import span


ACADEMIC_DOMAINS = (
    "arxiv.org",
    "semanticscholar.org",
    "aclanthology.org",
    "openreview.net",
    "proceedings.neurips.cc",
)
MAX_RESULTS = 5


def _safe_academic_url(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not any(host == domain or host.endswith("." + domain) for domain in ACADEMIC_DOMAINS):
        return None
    return value


def _tavily(query: str, max_results: int) -> list[dict]:
    if not config.TAVILY_API_KEY:
        raise RuntimeError("tavily_key_not_configured")
    response = httpx.post(
        "https://api.tavily.com/search",
        json={
            "api_key": config.TAVILY_API_KEY,
            "query": f"academic paper research: {query}",
            "search_depth": "advanced",
            "max_results": max_results,
            "include_answer": False,
            "include_domains": list(ACADEMIC_DOMAINS),
        },
        timeout=20,
    )
    response.raise_for_status()
    rows = []
    for item in response.json().get("results", []):
        url = _safe_academic_url(item.get("url"))
        if not url:
            continue
        rows.append({
            "title": str(item.get("title") or "Untitled paper")[:300],
            "url": url,
            "snippet": " ".join(str(item.get("content") or "").split())[:500],
            "provider": "tavily",
            "authors": None,
            "year": None,
        })
    return rows


def _semantic_scholar(query: str, max_results: int) -> list[dict]:
    headers = {"x-api-key": config.SEMANTIC_SCHOLAR_API_KEY} if config.SEMANTIC_SCHOLAR_API_KEY else {}
    response = httpx.get(
        f"{config.SEMANTIC_SCHOLAR_API_URL}/paper/search",
        params={"query": query, "limit": max_results, "fields": "title,authors,year,abstract,url"},
        headers=headers,
        timeout=20,
    )
    response.raise_for_status()
    rows = []
    for item in response.json().get("data", []):
        url = _safe_academic_url(item.get("url"))
        if not url:
            continue
        authors = ", ".join(str(row.get("name") or "") for row in item.get("authors", [])[:4] if row.get("name"))
        rows.append({
            "title": str(item.get("title") or "Untitled paper")[:300],
            "url": url,
            "snippet": " ".join(str(item.get("abstract") or "").split())[:500],
            "provider": "semantic_scholar",
            "authors": authors or None,
            "year": item.get("year"),
        })
    return rows


def search_publications(query: str, max_results: int = MAX_RESULTS) -> tuple[list[dict], dict]:
    """Search the configured provider, with a metadata-index fallback."""
    max_results = max(1, min(max_results, MAX_RESULTS))
    failures = []
    providers = ["tavily", "semantic_scholar"] if config.SEARCH_PROVIDER == "tavily" else ["semantic_scholar"]
    for provider in providers:
        try:
            rows = (_tavily if provider == "tavily" else _semantic_scholar)(query, max_results)
            return rows, {"provider": provider, "fallback_failures": failures}
        except Exception as error:
            failures.append({"provider": provider, "error_code": classify_error(error)})
    raise RuntimeError("paper_search_unavailable")


def mock_search(query: str, max_results: int = MAX_RESULTS) -> tuple[list[dict], dict]:
    return ([{
        "title": "ReAct: Synergizing Reasoning and Acting in Language Models",
        "url": "https://arxiv.org/abs/2210.03629",
        "snippet": f"Mock academic discovery result for: {query}",
        "provider": "mock",
        "authors": "Shunyu Yao et al.",
        "year": 2022,
    }], {"provider": "mock", "fallback_failures": []})


def run_paper_search(settings, run, messages, root_span, attempt=1, searcher=None):
    if not run["request_options"].get("allow_network"):
        raise ValueError("paper_search_requires_network_permission")
    query = messages[-1]["content"].strip()
    action = {"action_id": str(uuid4()), "action_type": "search_papers", "query": query, "max_results": MAX_RESULTS}
    searcher = searcher or (mock_search if settings.mode == "mock" else search_publications)
    try:
        with span(settings, run, root_span, "tool", "search_papers", {"query": query, "max_results": MAX_RESULTS}) as details:
            rows, metadata = searcher(query, MAX_RESULTS)
            details.update(provider=metadata["provider"], result_count=len(rows), fallback_failures=metadata.get("fallback_failures", []))
        with connect(settings) as db:
            record_action(db, run["id"], action, attempt, "ok", result_summary=f"{metadata['provider']}:{len(rows)}")
    except Exception as error:
        with connect(settings) as db:
            record_action(db, run["id"], action, attempt, "error", error_code=classify_error(error))
        raise

    sources = [dict(row, ordinal=index) for index, row in enumerate(rows, 1)]
    snapshot = {
        "schema_version": 1,
        "question": query,
        "message_ids": [str(message["id"]) for message in messages],
        "skill_version": run["skill_snapshot"]["version"],
        "search_provider": metadata["provider"],
        "result_urls": [row["url"] for row in sources],
    }
    with connect(settings) as db:
        db.execute(
            "INSERT INTO context_snapshots(run_id,context) VALUES(%s,%s) "
            "ON CONFLICT(run_id) DO UPDATE SET context=excluded.context",
            (run["id"], Jsonb(snapshot)),
        )

    if not sources:
        content = "没有找到匹配的公开论文候选。可以尝试加入英文方法名、研究领域或更具体的关键词。"
    else:
        lines = [f"找到 {len(sources)} 篇公开论文候选："]
        for row in sources:
            meta = " · ".join(str(value) for value in (row.get("authors"), row.get("year")) if value)
            lines.append(f"[{row['ordinal']}] {row['title']}" + (f"（{meta}）" if meta else ""))
        lines.append("这些是搜索服务返回的元数据与摘要线索，尚未核验论文全文。需要分析结论时，请打开或上传原文后使用资料证据问答、单篇论文评议或多篇证据综述。")
        content = "\n\n".join(lines)
    result = {
        "query": query,
        "web_sources": sources,
        "references": [],
        "insufficient_evidence": True,
        "limitations": ["搜索标题、摘要片段和链接仅用于发现候选论文，不作为已核验的论文结论证据。"],
        "provider": metadata["provider"],
    }
    return ModelAnswer(content, None, result)
