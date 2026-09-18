"""Fresh-container smoke: setup, PDF/TXT/MD indexing, three live Skills and Trace export."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from uuid import NAMESPACE_URL, uuid5

import fitz
import httpx


ROOT = Path(__file__).resolve().parents[1]
BASE_URL = os.environ.get("PRODUCT_SMOKE_URL", "http://127.0.0.1:18081")
USERNAME = os.environ.get("PRODUCT_SMOKE_USERNAME", "")
PASSWORD = os.environ.get("PRODUCT_SMOKE_PASSWORD", "")


def wait_file(client, file_id, timeout=300):
    started = time.monotonic()
    while True:
        response = client.get(f"/api/files/{file_id}")
        response.raise_for_status()
        detail = response.json()
        if detail["file"]["status"] in {"ready", "failed"}:
            return detail
        if time.monotonic() - started > timeout:
            raise RuntimeError(f"file_timeout:{file_id}")
        time.sleep(1)


def wait_run(client, run_id, timeout=420):
    started = time.monotonic()
    while True:
        response = client.get(f"/api/runs/{run_id}")
        response.raise_for_status()
        run = response.json()
        if run["status"] not in {"queued", "running", "cancelling"}:
            return run
        if time.monotonic() - started > timeout:
            raise RuntimeError(f"run_timeout:{run_id}")
        time.sleep(1)


def pdf_bytes():
    text = (
        "Alpha Retrieval Study\n\n"
        "Method: Alpha retrieves original passages before producing an answer. "
        "Every factual statement keeps the identifier of its supporting passage.\n\n"
        "Evaluation: On the synthetic TestSet, Alpha reports accuracy 82 while the baseline reports 74. "
        "The comparison uses the same 100 questions.\n\n"
        "Limitation: The reported experiment covers English text only and does not evaluate deployment cost."
    )
    document = fitz.open()
    page = document.new_page()
    page.insert_textbox(fitz.Rect(50, 50, 545, 790), text, fontsize=11)
    body = document.tobytes()
    document.close()
    return body


def main():
    if not USERNAME or not PASSWORD:
        raise SystemExit("Set PRODUCT_SMOKE_USERNAME and PRODUCT_SMOKE_PASSWORD for this isolated run")
    output = ROOT / "artifacts/product/M7"
    output.mkdir(parents=True, exist_ok=True)
    headers = {"X-Copilot-Request": "1"}
    with httpx.Client(base_url=BASE_URL, headers=headers, timeout=60) as client:
        started = time.monotonic()
        while True:
            try:
                health = client.get("/health")
                if health.status_code == 200 and health.json().get("status") == "ok":
                    break
            except httpx.HTTPError:
                pass
            if time.monotonic() - started > 120:
                raise RuntimeError("api_health_timeout")
            time.sleep(1)
        setup = client.get("/api/setup")
        setup.raise_for_status()
        if setup.json()["required"]:
            response = client.post("/api/setup", json={"username": USERNAME, "password": PASSWORD})
        else:
            response = client.post("/api/login", json={"username": USERNAME, "password": PASSWORD})
        response.raise_for_status()
        project_response = client.post("/api/projects", json={"title": "M7 Linux 容器验收"})
        project_response.raise_for_status()
        project = project_response.json()
        conversation_response = client.post(f"/api/projects/{project['id']}/conversations", json={"title": "三个 Skills 容器验收"})
        conversation_response.raise_for_status()
        conversation = conversation_response.json()

        sources = {
            "alpha-study.pdf": pdf_bytes(),
            "beta-memory.txt": (
                "Beta Memory Study\n\nMethod: After each episode, Beta writes a short reflection into a bounded memory. "
                "The next episode reads at most three stored reflections before selecting an action.\n\n"
                "Evaluation: On the synthetic RecoverySet, Beta completed 18 of 20 tasks; the no-memory variant completed 12 of 20.\n\n"
                "Limitation: The study does not report monetary API cost."
            ).encode(),
            "gamma-boundaries.md": (
                "# Gamma Boundaries\n\nGamma validates tool arguments against an allowlist before execution. "
                "A failed tool is retried once only when its error is classified as transient.\n\n"
                "The local test reports 30 accepted calls and 5 rejected out-of-policy calls. "
                "It does not evaluate multiple simultaneous users."
            ).encode(),
        }
        files = {}
        for name, body in sources.items():
            response = client.post(f"/api/projects/{project['id']}/files", params={"name": name, "kind": "original"}, content=body)
            response.raise_for_status()
            added = response.json()
            detail = wait_file(client, added["id"])
            if detail["file"]["status"] != "ready":
                raise RuntimeError(f"index_failed:{name}:{detail['file']['error_code']}")
            files[name] = detail["file"]

        cases = [
            {"id": "container-qa", "skill_id": "evidence-qa", "names": ["alpha-study.pdf"], "question": "Alpha 的方法、实验数字和证据边界是什么？只根据原文回答。", "artifacts": 0},
            {"id": "container-review", "skill_id": "paper-review", "names": ["beta-memory.txt"], "question": "精读 Beta Memory Study，评议方法、实验依据和成本边界。", "artifacts": 3},
            {"id": "container-survey", "skill_id": "evidence-survey", "names": ["alpha-study.pdf", "beta-memory.txt", "gamma-boundaries.md"], "question": "比较三份材料如何处理证据、记忆和工具边界，并说明不可直接推断的生产结论。", "artifacts": 3},
        ]
        results = []
        for case in cases:
            identity = str(uuid5(NAMESPACE_URL, "research-copilot-M7-container-" + case["id"]))
            response = client.post(f"/api/conversations/{conversation['id']}/messages", json={
                "content": case["question"], "client_message_id": identity,
                "skill_id": case["skill_id"], "file_ids": [files[name]["id"] for name in case["names"]], "model_id": "default",
            })
            response.raise_for_status()
            run = wait_run(client, response.json()["run_id"])
            result = run.get("result_json") or {}
            export = client.get(f"/api/runs/{run['id']}/export")
            export.raise_for_status()
            export_text = export.text
            check = {
                "case_id": case["id"], "run_id": run["id"], "status": run["status"],
                "references": len(result.get("references", [])), "artifacts": len(result.get("artifacts", [])),
                "trace_spans": len(run.get("spans", [])), "actions": len(run.get("actions", [])),
                "export_sha256": hashlib.sha256(export.content).hexdigest(),
                "export_has_redaction_marker": "[REDACTED]" in export_text,
            }
            check["passed"] = (
                run["status"] == "completed" and check["references"] > 0
                and check["artifacts"] == case["artifacts"] and check["trace_spans"] > 0
                and "api_key" not in export_text.casefold()
            )
            results.append(check)
            print(json.dumps(check, ensure_ascii=False), flush=True)

        tree = client.get(f"/api/projects/{project['id']}/files")
        tree.raise_for_status()
        snapshot = {
            "checked_at": datetime.now(timezone.utc).isoformat(), "base_url": BASE_URL,
            "project_id": project["id"], "conversation_id": conversation["id"],
            "source_hashes": {name: hashlib.sha256(body).hexdigest() for name, body in sources.items()},
            "file_versions": {row["display_name"]: row["current_version_id"] for row in tree.json()["files"]},
            "cases": results, "passed": all(item["passed"] for item in results),
        }
        (output / "container_smoke.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if not snapshot["passed"]:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
