"""Small explicit SQL migration runner. Connections commit or roll back as a unit."""
from pathlib import Path
import hashlib
import psycopg
from psycopg.rows import dict_row
from product.settings import Settings


def connect(settings: Settings | None = None):
    settings = settings or Settings.load()
    return psycopg.connect(settings.database_url, connect_timeout=5, row_factory=dict_row)


def migrate(settings: Settings | None = None):
    with connect(settings) as db:
        db.execute("SELECT pg_advisory_xact_lock(8931701)")
        db.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name text PRIMARY KEY, sha256 text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())")
        for file in sorted((Path(__file__).parent / "migrations").glob("*.sql")):
            body = file.read_text(encoding="utf-8")
            digest = hashlib.sha256(body.encode()).hexdigest()
            existing = db.execute("SELECT sha256 FROM schema_migrations WHERE name = %s", (file.name,)).fetchone()
            if existing:
                if existing["sha256"] != digest:
                    raise RuntimeError(f"Applied migration changed: {file.name}")
                continue
            db.execute(body)
            db.execute("INSERT INTO schema_migrations (name, sha256) VALUES (%s, %s)", (file.name, digest))


if __name__ == "__main__":
    migrate()
    print("Product migrations applied")
