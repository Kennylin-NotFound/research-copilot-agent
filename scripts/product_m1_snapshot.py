"""Capture and compare persisted real browser-run records before/after process restart."""
from pathlib import Path
import hashlib
import json
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from product.db import connect

with connect() as db:
    messages = db.execute("SELECT m.id,m.role,m.content,m.run_id,m.mode FROM messages m JOIN users u ON u.id=m.owner_id WHERE u.username='local_demo' ORDER BY m.created_at,m.id").fetchall()
    runs = db.execute("SELECT r.id,r.trace_id,r.status,r.mode,r.model,r.prompt_version,r.usage FROM runs r JOIN users u ON u.id=r.owner_id WHERE u.username='local_demo' ORDER BY r.created_at,r.id").fetchall()
    spans = db.execute("SELECT s.id,s.run_id,s.parent_span_id,s.kind,s.status,s.duration_ms,s.metadata FROM trace_spans s JOIN runs r ON r.id=s.run_id JOIN users u ON u.id=r.owner_id WHERE u.username='local_demo' ORDER BY s.started_at,s.id").fetchall()
assert len(runs) >= 2 and all(run["status"] == "completed" and run["mode"] == "live" for run in runs)
assert all(message["run_id"] for message in messages)
snapshot = json.dumps({"messages": messages, "runs": runs, "spans": spans}, default=str, ensure_ascii=False, indent=2)
out = ROOT / "artifacts/product/M1"
out.mkdir(exist_ok=True, parents=True)
before = out / "before_restart.json"
if "--verify" in sys.argv:
    original = json.loads(before.read_text(encoding="utf-8"))
    assert json.loads(snapshot) == original, "Persisted records changed across restart"
    (out / "restart_verified.json").write_text(json.dumps({"passed": True, "message_count": len(messages), "run_count": len(runs), "span_count": len(spans), "snapshot_sha256": hashlib.sha256(snapshot.encode()).hexdigest()}, indent=2) + "\n", encoding="utf-8")
    print("PASS: live messages, run/trace links and spans unchanged after process restart")
else:
    if before.exists():
        raise SystemExit("Before-restart snapshot already exists; preserve it")
    before.write_text(snapshot + "\n", encoding="utf-8")
    print(f"Captured {len(messages)} messages, {len(runs)} live runs, {len(spans)} spans")
