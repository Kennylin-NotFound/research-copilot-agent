"""Sanitized, owner-scoped trace export helpers."""
from __future__ import annotations

from pathlib import Path
from dotenv import dotenv_values


SENSITIVE_EXACT_KEYS = {
    "api_key",
    "access_token",
    "refresh_token",
    "authorization",
    "cookie",
    "password",
    "secret",
    "database_url",
    "lease_token",
    "token_hash",
}
CONFIG_SECRET_MARKERS = ("key", "token", "password", "secret", "authorization", "cookie", "database_url")


def _is_sensitive_field(key: object) -> bool:
    normalized = str(key).casefold()
    return (
        normalized in SENSITIVE_EXACT_KEYS
        or normalized.endswith("_api_key")
        or normalized.endswith("_password")
        or normalized.endswith("_secret")
    )


def configured_secrets(root: Path):
    values = {**dotenv_values(root / ".env"), **dotenv_values(root / ".local/dev.env")}
    return tuple(str(value) for key, value in values.items() if value and len(str(value)) >= 12
                 and any(part in key.casefold() for part in CONFIG_SECRET_MARKERS))


def redact(value, secrets=()):
    if isinstance(value, dict):
        return {key: "[REDACTED]" if _is_sensitive_field(key)
                else redact(item, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item, secrets) for item in value]
    if isinstance(value, tuple):
        return [redact(item, secrets) for item in value]
    if isinstance(value, str):
        cleaned = value
        for secret in secrets:
            cleaned = cleaned.replace(secret, "[REDACTED]")
        return cleaned
    return value
