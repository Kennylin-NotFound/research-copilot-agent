"""Product settings are isolated from legacy CLI data and dotenv state."""
from dataclasses import dataclass
import os
from pathlib import Path
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    database_url: str
    data_dir: Path
    mode: str
    cookie_secure: bool
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost")
    allow_setup: bool = True
    app_env: str = "development"
    app_version: str = "0.1.0-dev"

    @classmethod
    def load(cls):
        values = {**dotenv_values(ROOT / ".local/dev.env"), **os.environ}
        database_url = values.get("PRODUCT_DATABASE_URL", "")
        if not database_url.startswith(("postgresql://", "postgres://")):
            raise ValueError("PRODUCT_DATABASE_URL must point to the product PostgreSQL database")
        data_dir = Path(values.get("PRODUCT_DATA_DIR", ".local/storage"))
        if not data_dir.is_absolute():
            data_dir = ROOT / data_dir
        data_dir = data_dir.resolve()
        legacy_dir = (ROOT / "data").resolve()
        if data_dir == legacy_dir or legacy_dir in data_dir.parents:
            raise ValueError("Product storage must be separate from legacy data")
        mode = values.get("PRODUCT_MODE", "live")
        if mode not in {"live", "mock"}:
            raise ValueError("PRODUCT_MODE must be live or mock")
        hosts = tuple(h.strip() for h in values.get("PRODUCT_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",") if h.strip())
        app_env = values.get("PRODUCT_APP_ENV", "development").strip().lower()
        if app_env not in {"development", "production"}:
            raise ValueError("PRODUCT_APP_ENV must be development or production")
        cookie_secure = values.get("PRODUCT_COOKIE_SECURE", "true").lower() == "true"
        allow_setup = values.get("PRODUCT_ALLOW_SETUP", "false").lower() == "true"
        if app_env == "production":
            if mode != "live":
                raise ValueError("Production requires PRODUCT_MODE=live")
            if not cookie_secure:
                raise ValueError("Production requires secure cookies")
            if allow_setup:
                raise ValueError("Production setup endpoint must be disabled")
            if not hosts or "*" in hosts:
                raise ValueError("Production requires an explicit host allowlist")
        return cls(database_url, data_dir, mode, cookie_secure, hosts, allow_setup, app_env,
                   values.get("PRODUCT_APP_VERSION", "0.1.0-dev"))
