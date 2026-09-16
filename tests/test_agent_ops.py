"""AgentOps：轨迹、回放、门禁与维护的纯离线测试。"""

from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_run_recorder_minimal_trace_and_health():
    from observability import RunRecorder

    with tempfile.TemporaryDirectory() as temp_dir:
        recorder = RunRecorder(temp_dir)
        session = recorder.start_run(
            "survey", "secret research topic", versions={"prompt": "v1"}, run_id="run1"
        )
        session.record_event(
            "graph_step", {"node": "tools", "tool_names": ["search_papers"]}
        )
        session.finish({"termination_reason": "completed", "duration_ms": 12})

        events = (Path(temp_dir) / "run1" / "events.jsonl").read_text(encoding="utf-8")
        assert "search_papers" in events
        assert "tool args" not in events
        assert recorder.health_report()["healthy"] is True
        assert recorder.aggregate()["success_rate"] == 1.0


def test_recorder_does_not_persist_tool_payloads():
    from observability import RunRecorder

    with tempfile.TemporaryDirectory() as temp_dir:
        recorder = RunRecorder(temp_dir)
        session = recorder.start_run("survey", "topic", run_id="run2")
        session.record_event("graph_step", {"tool_names": ["read_paper"]})
        session.finish({"termination_reason": "completed", "output_chars": 100})
        stored = "".join(
            path.read_text(encoding="utf-8")
            for path in (Path(temp_dir) / "run2").glob("*.json*")
        )
        assert "full paper content" not in stored
        assert "api_key" not in stored.lower()


def test_offline_replay_gate_calibration():
    from evaluation.replay import run_backtest

    report = run_backtest()
    assert report["case_count"] == 6
    assert report["gate_pass_count"] == 4
    assert report["gate_reject_count"] == 2
    assert report["expectation_match_rate"] == 1.0


def test_regression_gate_detects_quality_drop():
    from evaluation.regression import evaluate_gate

    metrics = {
        "task_success": 1,
        "direction_coverage": 1,
        "paper_hit_rate": 1,
        "structure_score": 1,
        "tool_coverage": 1,
        "tool_success_rate": 1,
        "efficiency_score": 1,
        "overall_score": 0.80,
    }
    gate = evaluate_gate(
        metrics,
        baseline_metrics={"overall_score": 0.90},
        max_overall_regression=0.05,
    )
    assert gate["passed"] is False
    assert any("regression" in reason for reason in gate["reasons"])


def test_prune_is_dry_run_by_default_and_apply_is_safe():
    from observability import RunRecorder

    with tempfile.TemporaryDirectory() as temp_dir:
        recorder = RunRecorder(temp_dir)
        session = recorder.start_run("survey", "old", run_id="old-run")
        session.finish({"termination_reason": "completed"})
        manifest_path = Path(temp_dir) / "old-run" / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["finished_at"] = (
            datetime.now(timezone.utc) - timedelta(days=60)
        ).isoformat()
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        preview = recorder.prune(older_than_days=30)
        assert preview["candidate_count"] == 1
        assert (Path(temp_dir) / "old-run").exists()
        applied = recorder.prune(older_than_days=30, apply=True)
        assert applied["candidate_count"] == 1
        assert not (Path(temp_dir) / "old-run").exists()


def test_metrics_separate_task_quality_and_efficiency():
    from evaluation.metrics import evaluate_snapshot

    case = {
        "expected_directions": ["a", "b"],
        "key_papers": ["p"],
        "expected_tools": ["search"],
        "min_papers": 1,
        "max_tool_calls": 2,
        "max_duration_ms": 100,
    }
    snapshot = {
        "output": "背景概述 a b 方向分类 方法对比 趋势展望 参考文献",
        "stored_titles": ["p"],
        "intermediate_steps": [{"name": "search", "status": "success"}],
        "trace": {
            "termination_reason": "completed",
            "papers_count": 1,
            "tool_calls": 3,
            "duration_ms": 120,
        },
    }
    metrics = evaluate_snapshot(case, snapshot)
    assert metrics["task_success"] == 1.0
    assert metrics["efficiency_score"] == 0.0


def test_agent_run_writes_sanitized_workflow_events():
    from agent.core import ResearchCopilotAgent
    from langchain_core.messages import AIMessage, ToolMessage
    from observability import RunRecorder

    class FakeGraph:
        def stream(self, *args, **kwargs):
            yield {
                "model": {
                    "messages": [
                        AIMessage(
                            content="",
                            tool_calls=[{
                                "name": "search_papers",
                                "args": {"query": "sensitive-query"},
                                "id": "call-1",
                                "type": "tool_call",
                            }],
                        )
                    ]
                }
            }
            yield {
                "tools": {
                    "messages": [
                        ToolMessage(
                            content="full-paper-content-secret",
                            tool_call_id="call-1",
                        )
                    ]
                }
            }
            yield {"model": {"messages": [AIMessage(content="final survey")]}}

    class FakeKB:
        def get_document_count(self):
            return 1

    with tempfile.TemporaryDirectory() as temp_dir:
        agent = ResearchCopilotAgent.__new__(ResearchCopilotAgent)
        agent.graph = FakeGraph()
        agent.knowledge_base = FakeKB()
        agent._invoke_config = {"recursion_limit": 10}
        agent.recorder = RunRecorder(temp_dir)
        result = agent.run("private topic")

        run_dir = Path(temp_dir) / result["trace"]["run_id"]
        events = (run_dir / "events.jsonl").read_text(encoding="utf-8")
        assert "search_papers" in events
        assert "sensitive-query" not in events
        assert "full-paper-content-secret" not in events
        assert json.loads((run_dir / "result.json").read_text(encoding="utf-8"))[
            "summary"
        ]["termination_reason"] == "completed"


if __name__ == "__main__":
    tests = [
        test_run_recorder_minimal_trace_and_health,
        test_recorder_does_not_persist_tool_payloads,
        test_offline_replay_gate_calibration,
        test_regression_gate_detects_quality_drop,
        test_prune_is_dry_run_by_default_and_apply_is_safe,
        test_metrics_separate_task_quality_and_efficiency,
        test_agent_run_writes_sanitized_workflow_events,
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
