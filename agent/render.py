"""从类型化 ResearchState 确定性渲染首版 evidence package。"""

from __future__ import annotations

from domain import ResearchState


def render_evidence_package(state: ResearchState) -> str:
    lines = [
        f"# Research Evidence Package: {state.brief.topic}",
        "",
        f"- Run ID: `{state.run_id}`",
        f"- Task type: `{state.brief.task_type.value}`",
        f"- Status: `{state.status.value}`",
        f"- Termination: `{state.termination_reason or 'unknown'}`",
        f"- Searches / reads / iterations: {state.progress.searches} / "
        f"{state.progress.reads} / {state.progress.iterations}",
        "",
        "## Coverage",
        "",
    ]
    for item in state.coverage.axes:
        mark = "x" if item.sufficient else " "
        lines.append(f"- [{mark}] {item.axis}: {item.evidence_count} evidence item(s)")

    lines.extend(["", "## Candidate papers", ""])
    if state.candidates:
        for paper in state.candidates:
            source = f" — {paper.url}" if paper.url else ""
            lines.append(f"- `{paper.paper_id}` {paper.title} [{paper.status.value}]{source}")
    else:
        lines.append("- No candidate paper was recorded.")

    lines.extend(["", "## Evidence", ""])
    if state.evidence:
        for evidence in state.evidence:
            lines.extend(
                [
                    f"### {evidence.evidence_id} — {evidence.axis}",
                    "",
                    f"- Paper: `{evidence.paper_id}`",
                    f"- Support: `{evidence.support.value}`",
                    f"- Locator: {evidence.source_locator or 'not available'}",
                    "",
                    evidence.summary,
                    "",
                ]
            )
    else:
        lines.append("- No evidence was recorded.")

    lines.extend(["", "## Auditable decision trace", ""])
    for index, decision in enumerate(state.decisions, 1):
        lines.append(
            f"{index}. `{decision.action.action_type.value}` — "
            f"{decision.reason_summary} "
            f"(result: `{decision.result_status.value if decision.result_status else 'not_executed'}`)"
        )
    if not state.decisions:
        lines.append("- No decision was recorded.")

    if state.errors:
        lines.extend(["", "## Errors", ""])
        lines.extend(f"- {error}" for error in state.errors)

    lines.extend(
        [
            "",
            "## Evidence boundary",
            "",
            "This artifact records Agent state and extracted evidence summaries. "
            "It is not proof that every paper claim or citation is correct until the "
            "planned claim/citation benchmark has been completed.",
            "",
        ]
    )
    return "\n".join(lines)
