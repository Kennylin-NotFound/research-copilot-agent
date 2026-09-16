"""Write a probe, restart only this project's db, then invoke with --verify."""
import argparse
from pathlib import Path
import sys
import json
from uuid import uuid4
from datetime import datetime, timezone
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from product.db import connect, migrate

parser = argparse.ArgumentParser()
parser.add_argument("--verify", action="store_true")
args = parser.parse_args()
path = ROOT / "artifacts/product/M0/database_probe.json"
migrate()
if not args.verify:
    identity = str(uuid4())
    with connect() as db:
        db.execute("INSERT INTO installation_probe (id, value, sample) VALUES (%s, %s, %s)", (identity, "M0 persistent row", "[1,2,3]"))
        server_started = db.execute("SELECT pg_postmaster_start_time() AS t").fetchone()["t"].isoformat()
    report = {"id": identity, "write_passed": True, "before_server_started": server_started, "restart_verified": False}
else:
    report = json.loads(path.read_text(encoding="utf-8"))
    with connect() as db:
        row = db.execute("SELECT value, sample <-> '[1,2,3]'::vector AS distance FROM installation_probe WHERE id=%s", (report["id"],)).fetchone()
        started = db.execute("SELECT pg_postmaster_start_time() AS t").fetchone()["t"].isoformat()
        version = db.execute("SELECT extversion FROM pg_extension WHERE extname='vector'").fetchone()["extversion"]
    assert row and row["value"] == "M0 persistent row" and row["distance"] == 0
    assert started != report["before_server_started"], "The database must actually be restarted before verification"
    report.update(restart_verified=True, after_server_started=started, vector_version=version)
report["checked_at"] = datetime.now(timezone.utc).isoformat()
path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
print(json.dumps(report))
