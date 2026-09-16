"""Argon2 password hashes and revocable server-side sessions."""
import hashlib
import secrets
from pwdlib import PasswordHash

password_hash = PasswordHash.recommended()
DUMMY_HASH = password_hash.hash(secrets.token_urlsafe(24))


def token_hash(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


def issue_session(db, owner_id):
    raw = secrets.token_urlsafe(32)
    db.execute("INSERT INTO sessions (token_hash, owner_id, expires_at) VALUES (%s, %s, now() + interval '7 days')",
               (token_hash(raw), owner_id))
    return raw
