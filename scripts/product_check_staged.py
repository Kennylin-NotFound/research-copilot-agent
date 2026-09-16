"""Reject runtime paths and configured secrets in staged Git blobs, without printing values."""
from pathlib import Path
import subprocess
import sys
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
names = subprocess.check_output(["git", "diff", "--cached", "--name-only", "-z"], cwd=ROOT).decode().split("\0")
values = {**dotenv_values(ROOT / ".env"), **dotenv_values(ROOT / ".local/dev.env")}
secrets = [value.encode() for key, value in values.items() if value and len(value) >= 12
           and any(token in key.upper() for token in ("KEY", "TOKEN", "PASSWORD", "SECRET", "DATABASE_URL"))]
errors = []
for name in filter(None, names):
    if (name.startswith((".env", ".local/", "data/", "artifacts/", "reports/")) and name != ".env.example"):
        errors.append({"path": name, "reason": "private runtime path"})
        continue
    body = subprocess.check_output(["git", "show", ":" + name], cwd=ROOT)
    if any(secret in body for secret in secrets):
        errors.append({"path": name, "reason": "configured secret"})
if errors:
    print(errors)
    sys.exit(1)
print(f"PASS: {sum(bool(name) for name in names)} staged paths contain no configured secrets or private runtime paths")
