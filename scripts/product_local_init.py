"""Create only missing local development secrets. Never prints secret values."""
from pathlib import Path
import secrets

ROOT = Path(__file__).resolve().parents[1]
destination = ROOT / ".local"
destination.mkdir(exist_ok=True)
config = destination / "dev.env"
if not config.exists():
    password = secrets.token_urlsafe(32)
    config.write_text(
        f"PRODUCT_DB_PASSWORD={password}\n"
        f"PRODUCT_DATABASE_URL=postgresql://copilot:{password}@127.0.0.1:15432/copilot_dev\n"
        "PRODUCT_DATA_DIR=.local/storage\n"
        "PRODUCT_MODE=live\n"
        "PRODUCT_COOKIE_SECURE=false\n", encoding="utf-8")
    print("Created .local/dev.env (secret values not displayed)")
else:
    print("Kept existing .local/dev.env")
