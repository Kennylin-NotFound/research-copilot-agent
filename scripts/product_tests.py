"""Run meaningful product tests with a durable report; isolated DB enforced by fixtures."""
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timezone
import importlib.metadata
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
stage = sys.argv[1] if len(sys.argv) > 1 else "M1"
if stage not in {f"M{i}" for i in range(9)}:
    raise SystemExit("Use M0..M8")
out = ROOT / "artifacts/product" / stage
out.mkdir(parents=True, exist_ok=True)
with (out / "product_tests.log").open("w", encoding="utf-8") as stream, redirect_stdout(stream), redirect_stderr(stream):
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests/product"))
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
report = {"stage": stage, "checked_at": datetime.now(timezone.utc).isoformat(), "tests_run": result.testsRun,
          "failures": len(result.failures), "errors": len(result.errors), "skipped": len(result.skipped),
          "passed": result.wasSuccessful(), "mode": "deterministic/mock; actual PostgreSQL test database"}
(out / "product_tests.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
packages = sorted({(d.metadata["Name"], d.version) for d in importlib.metadata.distributions()})
(ROOT / "requirements.product.lock.txt").write_text("\n".join(f"{name}=={version}" for name, version in packages) + "\n", encoding="utf-8")
print(json.dumps(report))
raise SystemExit(0 if result.wasSuccessful() else 1)
