"""Controlled local/operations user management. Passwords are read from stdin."""
from __future__ import annotations

import argparse
import re
import sys
from uuid import uuid4

from product.auth import password_hash
from product.db import connect
from product.settings import Settings


USERNAME = re.compile(r"^[a-zA-Z0-9_.-]{3,50}$")


def upsert_user(settings, username: str, password: str, allow_weak_demo_password: bool = False):
    username = username.strip().lower()
    if not USERNAME.fullmatch(username):
        raise ValueError("invalid_username")
    if not 1 <= len(password) <= 128:
        raise ValueError("invalid_password_length")
    if len(password) < 10 and not allow_weak_demo_password:
        raise ValueError("password_too_short")
    encoded = password_hash.hash(password)
    with connect(settings) as db:
        db.execute("SELECT pg_advisory_xact_lock(8931703)")
        existing = db.execute("SELECT id FROM users WHERE username=%s FOR UPDATE", (username,)).fetchone()
        if existing:
            identity = existing["id"]
            db.execute("UPDATE users SET password_hash=%s WHERE id=%s", (encoded, identity))
            db.execute("DELETE FROM sessions WHERE owner_id=%s", (identity,))
            action = "updated"
        else:
            identity = uuid4()
            db.execute(
                "INSERT INTO users(id,username,password_hash) VALUES(%s,%s,%s)",
                (identity, username, encoded),
            )
            action = "created"
        db.execute("DELETE FROM login_attempts WHERE username=%s", (username,))
    return {"id": identity, "username": username, "action": action}


def main():
    parser = argparse.ArgumentParser(description="Create or rotate a Research Copilot user")
    parser.add_argument("username")
    parser.add_argument("--password-stdin", action="store_true", required=True)
    parser.add_argument("--allow-weak-demo-password", action="store_true")
    args = parser.parse_args()
    password = sys.stdin.readline().rstrip("\r\n")
    result = upsert_user(Settings.load(), args.username, password, args.allow_weak_demo_password)
    print(f"User {result['username']} {result['action']}; active sessions revoked")


if __name__ == "__main__":
    main()
