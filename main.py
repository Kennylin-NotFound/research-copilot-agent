"""
Research Copilot Agent 入口

使用方式：
    # 场景 S1：主题调研
    python main.py "RAG 系统中的检索策略优化方法"
    python main.py --interactive
    python main.py --reset  # 清空知识库

    # 场景 S2：单篇精读
    python main.py --mode deep-dive --url https://arxiv.org/abs/2210.03629
    python main.py --mode deep-dive --file ./papers/attention.pdf
"""

import argparse
import io
import json
import logging
import os
import sys

# Windows: force UTF-8 stdout to handle Chinese + emoji in tool outputs
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import config
from agent.core import ResearchCopilotAgent
from observability import RunRecorder


def setup_logging():
    """配置日志格式。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # 降低第三方库的日志级别
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)


def print_banner(mode: str, label: str):
    print(f"\n{'='*60}")
    print(f"  Research Copilot Agent")
    print(f"  Model: {config.LLM_MODEL}")
    if mode == "survey":
        print(f"  Search: {config.SEARCH_PROVIDER}")
    if config.LANGSMITH_TRACING:
        print(f"  LangSmith: ON (project: {config.LANGSMITH_PROJECT})")
    print(f"{'='*60}")
    mode_label = {
        "survey": "调研主题",
        "agent-v2": "Agent v2 任务",
        "deep-dive": "精读来源",
    }.get(mode, mode)
    print(f"  {mode_label}: {label}")
    print(f"{'='*60}\n")


def run_research(topic: str):
    """执行一次完整的文献调研（场景 S1）"""
    print_banner("survey", topic)

    agent = ResearchCopilotAgent(recorder=RunRecorder(config.RUNS_DIR))
    result = agent.run(topic)

    # 保存输出
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    safe_name = "".join(c if c.isalnum() or c in " _-" else "_" for c in topic[:40])
    output_file = os.path.join(config.OUTPUT_DIR, f"survey_{safe_name}.md")

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(f"# 文献调研: {topic}\n\n")
        f.write(result["output"])

    # 仅持久化不含论文正文/工具参数的运行摘要，便于回归与故障定位。
    trace_file = output_file.removesuffix(".md") + ".trace.json"
    with open(trace_file, "w", encoding="utf-8") as f:
        json.dump(result["trace"], f, ensure_ascii=False, indent=2)

    print(f"\n{'='*60}")
    print(f"  调研完成!")
    print(f"  涉及论文: {result['papers_count']} 篇")
    print(f"  工具调用: {len(result['intermediate_steps'])} 次")
    print(f"  结束原因: {result['trace']['termination_reason']}")
    print(f"  综述已保存: {output_file}")
    print(f"  运行摘要: {trace_file}")
    if config.LANGSMITH_TRACING:
        print(f"  LangSmith Trace: https://smith.langchain.com/project/{config.LANGSMITH_PROJECT}")
    print(f"{'='*60}")

    return agent


def run_deep_dive(source: str, is_local: bool = False):
    """执行单篇精读（场景 S2）"""
    print_banner("deep-dive", source)

    agent = ResearchCopilotAgent(recorder=RunRecorder(config.RUNS_DIR))
    result = agent.run_deep_dive(source, is_local=is_local)

    output = result["output"]
    title = result.get("title", "unknown")

    if output.startswith("[错误]"):
        print(f"\n{output}")
        return

    # 保存输出
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    safe_name = "".join(c if c.isalnum() or c in " _-" else "_" for c in title[:50])
    output_file = os.path.join(config.OUTPUT_DIR, f"deepdive_{safe_name}.md")

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(f"# 精读报告: {title}\n\n")
        f.write(f"> 来源: {source}\n\n")
        f.write(output)

    print(f"\n{'='*60}")
    print(f"  精读完成!")
    print(f"  论文标题: {title}")
    print(f"  报告已保存: {output_file}")
    print(f"  (已存入知识库，可用于后续 S3 对比分析)")
    print(f"{'='*60}")


def run_agent_v2(
    topic: str,
    task_type: str = "open_survey",
    axes: list[str] | None = None,
    claims: list[str] | None = None,
    approval_policy: str = "normal",
):
    """执行显式状态图 Agent v2；当前产物是 evidence package 而非完整综述。"""
    from agent.checkpointing import create_sqlite_checkpointer
    from agent.render import render_evidence_package
    from agent.state_graph import create_default_orchestrator
    from domain import ApprovalPolicy, ResearchBrief, RunLimits, TaskType

    print_banner("agent-v2", topic)
    brief = ResearchBrief(
        task_type=TaskType(task_type),
        topic=topic,
        research_axes=axes or [],
        claims=claims or [],
        approval_policy=ApprovalPolicy(approval_policy),
        limits=RunLimits(
            max_iterations=config.AGENT_V2_MAX_ITERATIONS,
            max_searches=config.AGENT_V2_MAX_SEARCHES,
            max_reads=config.AGENT_V2_MAX_READS,
            timeout_seconds=config.AGENT_V2_TIMEOUT_SECONDS,
            max_consecutive_no_progress=config.AGENT_V2_MAX_NO_PROGRESS,
        ),
    )
    checkpointer, connection, checkpoint_path = create_sqlite_checkpointer(
        config.AGENT_V2_CHECKPOINT_DB
    )
    try:
        orchestrator = create_default_orchestrator(
            recorder=RunRecorder(config.RUNS_DIR),
            checkpointer=checkpointer,
        )
        state = orchestrator.run(brief)
    finally:
        connection.close()

    _write_agent_v2_outputs(state, checkpoint_path)
    return state


def resume_agent_v2(
    run_id: str,
    decision: str,
    reason: str | None = None,
    edits: dict | None = None,
):
    """从持久 checkpoint 恢复一个等待人工决策的 Agent v2 run。"""
    from agent.checkpointing import create_sqlite_checkpointer
    from agent.state_graph import create_default_orchestrator
    from domain import HumanDecision, HumanDecisionType

    print_banner("agent-v2", f"resume:{run_id}")
    human_decision = HumanDecision(
        decision=HumanDecisionType(decision),
        reason=reason,
        edits=edits or {},
    )
    checkpointer, connection, checkpoint_path = create_sqlite_checkpointer(
        config.AGENT_V2_CHECKPOINT_DB
    )
    try:
        orchestrator = create_default_orchestrator(
            recorder=RunRecorder(config.RUNS_DIR),
            checkpointer=checkpointer,
        )
        state = orchestrator.resume(run_id, human_decision)
    finally:
        connection.close()

    _write_agent_v2_outputs(state, checkpoint_path)
    return state


def _write_agent_v2_outputs(state, checkpoint_path):
    from agent.render import render_evidence_package

    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    topic = state.brief.topic
    safe_name = "".join(c if c.isalnum() or c in " _-" else "_" for c in topic[:40])
    output_file = os.path.join(config.OUTPUT_DIR, f"agentv2_{safe_name}.md")
    state_file = os.path.join(config.OUTPUT_DIR, f"agentv2_{safe_name}.state.json")
    with open(output_file, "w", encoding="utf-8") as handle:
        handle.write(render_evidence_package(state))
    with open(state_file, "w", encoding="utf-8") as handle:
        json.dump(state.model_dump(mode="json"), handle, ensure_ascii=False, indent=2)

    print(f"\n{'='*60}")
    print("  Agent v2 运行结束")
    print(f"  状态: {state.status.value}")
    print(f"  结束原因: {state.termination_reason}")
    print(f"  候选论文 / 证据: {len(state.candidates)} / {len(state.evidence)}")
    print(f"  Evidence package: {output_file}")
    print(f"  State snapshot: {state_file}")
    print(f"  Run artifacts: {os.path.join(config.RUNS_DIR, state.run_id)}")
    print(f"  Checkpoint DB: {checkpoint_path}")
    if state.status.value == "awaiting_human":
        print("  下一步: 使用同一 run_id 提交 approve/reject/edit/request_more_evidence")
    print(f"{'='*60}")


def interactive_mode():
    """交互模式：调研 + 追问"""
    topic = input("\n请输入调研主题: ").strip()
    if not topic:
        print("主题不能为空")
        return

    agent = run_research(topic)

    print("\n调研完成。你可以继续追问（输入 quit 退出）:\n")
    while True:
        try:
            question = input("追问> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if question.lower() in ("quit", "exit", "q"):
            break
        if not question:
            continue

        answer = agent.chat(question)
        print(f"\n{answer}\n")


def main():
    setup_logging()

    parser = argparse.ArgumentParser(
        description="Research Copilot Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例:
  # 场景 S1：主题调研
  python main.py "RAG 系统检索优化"
  python main.py --interactive

  # 场景 S2：单篇精读
  python main.py --mode deep-dive --url https://arxiv.org/abs/2210.03629
  python main.py --mode deep-dive --file ./papers/attention.pdf

  # 其他
  python main.py --reset   # 清空知识库
""",
    )
    parser.add_argument("topic", nargs="?", help="调研主题（S1 模式）")
    parser.add_argument("-i", "--interactive", action="store_true", help="交互模式")
    parser.add_argument("--reset", action="store_true", help="清空知识库并退出")
    parser.add_argument(
        "--mode",
        choices=["survey", "agent-v2", "deep-dive"],
        default="survey",
        help="运行模式：survey（旧 Agent）、agent-v2（显式状态图）或 deep-dive（固定精读）",
    )
    parser.add_argument(
        "--task-type",
        choices=["open_survey", "adaptive_paper_selection", "evidence_gap_closure"],
        default="open_survey",
        help="agent-v2 任务类型",
    )
    parser.add_argument("--axis", action="append", default=[], help="研究轴，可重复传入")
    parser.add_argument("--claim", action="append", default=[], help="A3 待补证主张，可重复传入")
    parser.add_argument(
        "--approval-policy",
        choices=["normal", "strict"],
        default="normal",
        help="agent-v2 人工审批策略；strict 会在外部工具执行前暂停",
    )
    parser.add_argument("--resume-run-id", help="从持久 checkpoint 恢复 agent-v2 run")
    parser.add_argument(
        "--human-decision",
        choices=["approve", "reject", "edit", "request_more_evidence"],
        help="恢复时提交的人工决策",
    )
    parser.add_argument("--human-reason", help="人工决策原因")
    parser.add_argument(
        "--edit-json",
        help="edit/request_more_evidence 的 JSON 对象，例如动作 arguments 或 research_axes/queries",
    )
    parser.add_argument("--url", help="论文 URL（--mode deep-dive 时使用）")
    parser.add_argument("--file", dest="file_path", help="本地 PDF 路径（--mode deep-dive 时使用）")
    parser.add_argument(
        "--ops",
        choices=["list", "check", "report", "prune"],
        help="运行轨迹维护：列表、检查、聚合或按保留期清理",
    )
    parser.add_argument("--runs-dir", default=config.RUNS_DIR, help="运行轨迹根目录")
    parser.add_argument("--days", type=int, default=config.RUN_RETENTION_DAYS, help="prune 保留天数")
    parser.add_argument("--apply", action="store_true", help="实际执行 prune；默认仅 dry-run")

    args = parser.parse_args()

    if args.ops:
        recorder = RunRecorder(args.runs_dir)
        if args.ops == "list":
            payload = recorder.list_runs()
        elif args.ops == "check":
            payload = recorder.health_report()
        elif args.ops == "report":
            payload = recorder.aggregate()
        else:
            payload = recorder.prune(args.days, apply=args.apply)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    if args.reset:
        from rag.knowledge_base import KnowledgeBase
        kb = KnowledgeBase()
        kb.clear()
        print("知识库已清空。")
        return

    if args.mode == "deep-dive":
        if args.url:
            run_deep_dive(args.url, is_local=False)
        elif args.file_path:
            run_deep_dive(args.file_path, is_local=True)
        else:
            parser.error("--mode deep-dive 需要指定 --url <url> 或 --file <path>")
        return

    if args.mode == "agent-v2":
        if args.resume_run_id:
            if not args.human_decision:
                parser.error("--resume-run-id 需要同时指定 --human-decision")
            try:
                edits = json.loads(args.edit_json) if args.edit_json else {}
            except json.JSONDecodeError as exc:
                parser.error(f"--edit-json 不是合法 JSON: {exc}")
            if not isinstance(edits, dict):
                parser.error("--edit-json 必须是 JSON object")
            resume_agent_v2(
                args.resume_run_id,
                decision=args.human_decision,
                reason=args.human_reason,
                edits=edits,
            )
            return
        if not args.topic:
            parser.error("--mode agent-v2 需要提供调研主题")
        run_agent_v2(
            args.topic,
            task_type=args.task_type,
            axes=args.axis,
            claims=args.claim,
            approval_policy=args.approval_policy,
        )
        return

    # survey 模式（默认）
    if args.interactive:
        interactive_mode()
    elif args.topic:
        run_research(args.topic)
    else:
        interactive_mode()


if __name__ == "__main__":
    main()
