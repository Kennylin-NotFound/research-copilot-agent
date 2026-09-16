"""Capture a secret-free, restorable legacy baseline and run its offline suites."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
from datetime import datetime, timezone
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts/product/M0"
SOURCE_DIRS = ("agent", "domain", "evaluation", "observability", "rag", "tests", "tools", "utils")
TOP_FILES = ("config.py", "main.py", "requirements.txt", ".env.example", ".gitignore", "README.md", "WORKSPACE_ROLE.md")


def write_json(path: Path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    # Only known source/fixture extensions; no runtime KB, checkpoints, reports or real .env.
    paths = [ROOT / name for name in TOP_FILES]
    for name in SOURCE_DIRS:
        paths.extend(p for p in (ROOT / name).rglob("*")
                     if p.is_file() and "__pycache__" not in p.parts
                     and p.suffix in {".py", ".json", ".jsonl", ".md", ".txt", ".yaml", ".yml"})
    paths = sorted(set(paths))
    from dotenv import dotenv_values
    secrets = [value for key, value in dotenv_values(ROOT / ".env").items()
               if value and len(value) >= 12 and any(s in key.upper() for s in ("KEY", "TOKEN", "PASSWORD", "SECRET"))]
    violations = []
    for path in paths:
        body = path.read_text(encoding="utf-8", errors="replace")
        if any(secret in body for secret in secrets):
            violations.append(path.relative_to(ROOT).as_posix())
    if violations:
        write_json(OUT / "secret_scan.json", {"passed": False, "files": violations})
        print("Baseline stopped: configured secret found; paths saved without values.")
        return 1
    snapshot = OUT / "legacy_source.zip"
    if not snapshot.exists():
        manifest = []
        with ZipFile(snapshot, "x", ZIP_DEFLATED) as archive:
            for path in paths:
                relative = path.relative_to(ROOT).as_posix()
                data = path.read_bytes()
                archive.writestr(relative, data)
                manifest.append({"path": relative, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
        write_json(OUT / "source_manifest.json", manifest)
    # Verify the existing immutable archive against its original manifest on every invocation.
    with ZipFile(snapshot) as archive:
        manifest = json.loads((OUT / "source_manifest.json").read_text(encoding="utf-8"))
        assert all(hashlib.sha256(archive.read(item["path"])).hexdigest() == item["sha256"] for item in manifest)
    write_json(OUT / "secret_scan.json", {"passed": True, "scope": "known configured secrets in selected source files", "files_checked": len(paths)})
    packages = sorted({(dist.metadata["Name"], dist.version) for dist in importlib.metadata.distributions()})
    stamp = datetime.now(timezone.utc).isoformat()
    if not (OUT / "environment_baseline.json").exists():
        write_json(OUT / "environment_baseline.json", {
            "recorded_at": stamp, "python": platform.python_version(), "platform": platform.platform(),
            "packages": [{"name": name, "version": version} for name, version in packages],
            "archive_sha256": hashlib.sha256(snapshot.read_bytes()).hexdigest(),
        })
        (OUT / "requirements.legacy.lock.txt").write_text(
            "\n".join(f"{name}=={version}" for name, version in packages) + "\n", encoding="utf-8")
    env = os.environ.copy()
    env.update(PYTHONIOENCODING="utf-8", LANGCHAIN_TRACING_V2="false", LANGSMITH_TRACING="false")
    result = subprocess.run([sys.executable, "tests/run_tests.py"], cwd=ROOT,
                            env=env, capture_output=True, encoding="utf-8", errors="replace", timeout=300)
    log = result.stdout + "\n" + result.stderr
    for secret in secrets:
        log = log.replace(secret, "[REDACTED]")
    (OUT / "legacy_tests.log").write_text(log, encoding="utf-8")
    summary = {"recorded_at": stamp, "exit_code": result.returncode,
               "passed": result.returncode == 0, "suite_pass_count": len(re.findall(r"^\s+PASS  ", log, re.M)),
               "test_scope": "7 legacy offline suites; API/Chroma legacy scripts excluded",
               "source_archive_verified": True, "selected_files": len(manifest)}
    write_json(OUT / "legacy_tests.json", summary)
    print(json.dumps(summary, ensure_ascii=False))
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
