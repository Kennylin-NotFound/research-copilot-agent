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
        return cls(database_url, data_dir, mode, values.get("PRODUCT_COOKIE_SECURE", "true").lower() == "true")
