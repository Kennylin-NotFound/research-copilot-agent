"""Validate all M0 evidence and record the gate honestly. Does not call any API."""
import importlib.metadata
from pathlib import Path
import os
import subprocess
import sys
import json
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/product/M0"


def load(name):
    return json.loads((OUT / name).read_text(encoding="utf-8"))


def main():
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "LANGCHAIN_TRACING_V2": "false"}
    runs = []
    for label, args in (("legacy_regression", ["tests/run_tests.py"]),
                        ("contracts", ["-m", "unittest", "discover", "-s", "tests/product", "-v"]),
                        ("dependency_check", ["-m", "pip", "check"])):
        result = subprocess.run([sys.executable, *args], cwd=ROOT, env=env, capture_output=True,
                                encoding="utf-8", errors="replace", timeout=240)
        log = result.stdout + result.stderr
        (OUT / f"{label}.log").write_text(log, encoding="utf-8")
        runs.append({"name": label, "passed": result.returncode == 0, "exit_code": result.returncode})
    packages = sorted({(d.metadata["Name"], d.version) for d in importlib.metadata.distributions()})
    (ROOT / "requirements.product.lock.txt").write_text("\n".join(f"{n}=={v}" for n, v in packages) + "\n", encoding="utf-8")
    live = load("live_probe.json")
    embedding = load("embedding_retry.json")
    database = load("database_probe.json")
    baseline = load("legacy_tests.json")
    checks = [
        {"id": "K0-source", "passed": baseline["source_archive_verified"] and load("secret_scan.json")["passed"]},
        {"id": "K0-tests", "passed": all(r["passed"] for r in runs)},
        {"id": "K0-database", "passed": database["restart_verified"]},
        {"id": "K0-tasks", "passed": len(load("source_provenance.json")) == 2 and
         load("M0-QA-01-deepseek-v4-pro.json")["all_quotes_found"] and (OUT / "task_review.md").exists()},
        {"id": "K0-apis", "passed": embedding["passed"] and all(r["passed"] for r in live["checks"] if r["name"] != "embedding")},
        {"id": "K0-isolation", "passed": "data/" in (ROOT / ".gitignore").read_text() and
         (ROOT / ".local/dev.env").exists(), "note": "Independent dev DB and product directory configured; owner endpoint tests start in M1"},
    ]
    passed = all(check["passed"] for check in checks)
    report = {"stage": "M0", "status": "passed" if passed else "in_progress", "checked_at": datetime.now(timezone.utc).isoformat(),
              "checks": checks, "commands": runs, "next_stage_allowed": passed,
              "source_revision": "legacy_source.zip + source_manifest.json; initial Git commit recorded separately",
              "live_results": "4 chat calls, 1 search, embedding restored after account recharge (original failure retained in live_probe.json)",
              "known_limits": ["M0 is not a GUI or Agent Skill acceptance", "QA Pro task passed source review; other outputs retain quality failures", "No production or server performance claim"],
              "artifacts": ["source_manifest.json", "environment_baseline.json", "legacy_tests.json", "database_probe.json", "source_provenance.json", "live_probe.json", "embedding_retry.json", "task_review.md"]}
    (OUT / "acceptance.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
