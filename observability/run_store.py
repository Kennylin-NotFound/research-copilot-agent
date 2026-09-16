"""文件系统运行轨迹、健康检查与保留策略。

每次执行只保存最小必要元数据：输入指纹、版本、阶段、工具名、状态、耗时和
聚合结果。不默认保存论文正文、完整 Prompt、工具参数或工具返回内容。
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
import shutil
import uuid


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    os.replace(temp_path, path)


def _write_jsonl_atomic(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(temp_path, path)


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class RunSession:
    """单次运行的最小轨迹记录器。"""

    def __init__(
        self,
        root_dir: Path,
        run_type: str,
        input_text: str,
        versions: dict | None = None,
        run_id: str | None = None,
    ):
        self.run_id = run_id or uuid.uuid4().hex
        self.run_dir = root_dir / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=False)
        self.events_path = self.run_dir / "events.jsonl"
        self.manifest_path = self.run_dir / "manifest.json"
        self.result_path = self.run_dir / "result.json"
        self.state_path = self.run_dir / "state.json"
        self.decisions_path = self.run_dir / "decisions.jsonl"
        self._seq = 0
        self._closed = False
        self.manifest = {
            "schema_version": "1.0",
            "run_id": self.run_id,
            "run_type": run_type,
            "status": "running",
            "started_at": _utc_now(),
            "finished_at": None,
            "input_sha256": hashlib.sha256(input_text.encode("utf-8")).hexdigest(),
            "input_preview": input_text.strip().replace("\n", " ")[:120],
            "versions": versions or {},
        }
        _write_json_atomic(self.manifest_path, self.manifest)
        self.record_event("workflow_started", {"run_type": run_type})

    @classmethod
    def open_existing(cls, root_dir: Path, run_id: str) -> "RunSession":
        """Reopen an unfinished run after a process-level HITL pause."""
        target = (root_dir / run_id).resolve()
        if root_dir not in target.parents or target == root_dir:
            raise ValueError(f"unsafe run path: {target}")
        manifest_path = target / "manifest.json"
        if not manifest_path.exists():
            raise FileNotFoundError(f"run manifest not found: {run_id}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("run_id") != run_id:
            raise ValueError("run_id does not match manifest")
        if manifest.get("status") in {"completed", "failed"}:
            raise RuntimeError(f"run is already closed: {manifest.get('status')}")

        session = cls.__new__(cls)
        session.run_id = run_id
        session.run_dir = target
        session.events_path = target / "events.jsonl"
        session.manifest_path = manifest_path
        session.result_path = target / "result.json"
        session.state_path = target / "state.json"
        session.decisions_path = target / "decisions.jsonl"
        session._seq = 0
        if session.events_path.exists():
            session._seq = sum(
                1 for line in session.events_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            )
        session._closed = False
        session.manifest = manifest
        return session

    def record_event(self, event_type: str, data: dict | None = None) -> None:
        if self._closed:
            raise RuntimeError("run session is already closed")
        self._seq += 1
        event = {
            "seq": self._seq,
            "timestamp": _utc_now(),
            "event_type": event_type,
            "data": data or {},
        }
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    def write_state(self, state: dict) -> None:
        """原子写入最新业务状态，供离线检查与恢复设计使用。"""
        if self._closed:
            raise RuntimeError("run session is already closed")
        _write_json_atomic(self.state_path, state)

    def record_decision(self, decision: dict) -> None:
        """追加可审计决策摘要；调用方不得传入隐藏思维链或工具原文。"""
        if self._closed:
            raise RuntimeError("run session is already closed")
        with self.decisions_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(decision, ensure_ascii=False) + "\n")

    def write_decisions(self, decisions: list[dict]) -> None:
        """Write the complete decision trace without duplicating rows after resume."""
        if self._closed:
            raise RuntimeError("run session is already closed")
        _write_jsonl_atomic(self.decisions_path, decisions)

    def pause(self, state: dict, interrupt_payload: dict) -> None:
        """Persist a resumable human-review boundary without closing the run."""
        if self._closed:
            raise RuntimeError("run session is already closed")
        self.write_state(state)
        self.record_event(
            "workflow_interrupted",
            {
                "reason": interrupt_payload.get("reason", "human_decision_required"),
                "action_id": interrupt_payload.get("action", {}).get("action_id"),
            },
        )
        self.manifest["status"] = "awaiting_human"
        self.manifest["finished_at"] = None
        _write_json_atomic(self.manifest_path, self.manifest)

    def resume(self) -> None:
        if self._closed:
            raise RuntimeError("run session is already closed")
        self.record_event("workflow_resumed", {})
        self.manifest["status"] = "running"
        self.manifest["finished_at"] = None
        _write_json_atomic(self.manifest_path, self.manifest)

    def finish(self, summary: dict, status: str = "completed") -> dict:
        if self._closed:
            return summary
        self.record_event("workflow_finished", {"status": status})
        result = {
            "schema_version": "1.0",
            "run_id": self.run_id,
            "status": status,
            "summary": summary,
        }
        _write_json_atomic(self.result_path, result)
        self.manifest["status"] = status
        self.manifest["finished_at"] = _utc_now()
        _write_json_atomic(self.manifest_path, self.manifest)
        self._closed = True
        return result

    def fail(self, error_type: str, message: str) -> dict:
        safe_message = str(message).replace("\n", " ")[:240]
        self.record_event(
            "workflow_failed",
            {"error_type": error_type, "message": safe_message},
        )
        return self.finish(
            {"termination_reason": "exception", "error_type": error_type},
            status="failed",
        )


class RunRecorder:
    """运行目录管理器，同时提供健康检查、聚合和安全清理。"""

    def __init__(self, root_dir: str | os.PathLike):
        self.root_dir = Path(root_dir).expanduser().resolve()
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def start_run(
        self,
        run_type: str,
        input_text: str,
        versions: dict | None = None,
        run_id: str | None = None,
    ) -> RunSession:
        return RunSession(
            self.root_dir,
            run_type=run_type,
            input_text=input_text,
            versions=versions,
            run_id=run_id,
        )

    def resume_run(self, run_id: str) -> RunSession:
        return RunSession.open_existing(self.root_dir, run_id)

    def list_runs(self) -> list[dict]:
        runs = []
        for child in sorted(self.root_dir.iterdir(), reverse=True):
            if not child.is_dir():
                continue
            manifest_path = child / "manifest.json"
            if not manifest_path.exists():
                continue
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                manifest = {"run_id": child.name, "status": "corrupt"}
            result_path = child / "result.json"
            if result_path.exists():
                try:
                    manifest["result"] = json.loads(
                        result_path.read_text(encoding="utf-8")
                    ).get("summary", {})
                except (OSError, json.JSONDecodeError):
                    manifest["result_error"] = "invalid result.json"
            runs.append(manifest)
        return runs

    def health_report(self) -> dict:
        issues = []
        runs = self.list_runs()
        for run in runs:
            run_id = run.get("run_id", "unknown")
            run_dir = self.root_dir / run_id
            if run.get("status") == "corrupt":
                issues.append({"run_id": run_id, "issue": "invalid manifest"})
                continue
            for name in ("manifest.json", "events.jsonl"):
                if not (run_dir / name).exists():
                    issues.append({"run_id": run_id, "issue": f"missing {name}"})
            if run.get("status") in {"completed", "failed"} and not (
                run_dir / "result.json"
            ).exists():
                issues.append({"run_id": run_id, "issue": "missing result.json"})
            if run.get("run_type") == "agent_survey_v2" and run.get("status") != "running":
                for name in ("state.json", "decisions.jsonl"):
                    if not (run_dir / name).exists():
                        issues.append({"run_id": run_id, "issue": f"missing {name}"})
        return {
            "root_dir": str(self.root_dir),
            "run_count": len(runs),
            "healthy": not issues,
            "issues": issues,
        }

    def aggregate(self) -> dict:
        runs = self.list_runs()
        completed = [r for r in runs if r.get("status") == "completed"]
        failed = [r for r in runs if r.get("status") == "failed"]
        durations = [
            r.get("result", {}).get("duration_ms")
            for r in completed
            if isinstance(r.get("result", {}).get("duration_ms"), (int, float))
        ]
        fallback = sum(
            1
            for r in completed
            if r.get("result", {}).get("termination_reason")
            in {"recursion_limit", "fallback_no_final_output"}
        )
        return {
            "run_count": len(runs),
            "completed": len(completed),
            "failed": len(failed),
            "success_rate": round(len(completed) / len(runs), 4) if runs else 0.0,
            "fallback_rate": round(fallback / len(completed), 4) if completed else 0.0,
            "average_duration_ms": round(sum(durations) / len(durations), 2)
            if durations
            else None,
        }

    def prune(self, older_than_days: int, apply: bool = False) -> dict:
        if older_than_days < 1:
            raise ValueError("older_than_days must be >= 1")
        cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
        candidates = []
        for run in self.list_runs():
            finished_at = _parse_time(run.get("finished_at"))
            if finished_at is None or finished_at >= cutoff:
                continue
            target = (self.root_dir / run["run_id"]).resolve()
            if self.root_dir not in target.parents or target == self.root_dir:
                raise ValueError(f"unsafe run path: {target}")
            candidates.append(target)

        if apply:
            for target in candidates:
                shutil.rmtree(target)
        return {
            "apply": apply,
            "older_than_days": older_than_days,
            "candidate_count": len(candidates),
            "paths": [str(path) for path in candidates],
        }
