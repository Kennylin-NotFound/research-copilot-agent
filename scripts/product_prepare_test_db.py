"""Create only the isolated local copilot_test database; never truncates application data."""
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from product.settings import Settings
from product.db import migrate


def prepare():
    settings = Settings.load()
    config = conninfo_to_dict(settings.database_url)
    if config.get("host") != "127.0.0.1" or config.get("port") != "15432" or config.get("dbname") != "copilot_dev":
        raise RuntimeError("Test creation only permits this project's loopback dev database")
    admin = make_conninfo(settings.database_url, dbname="postgres")
    with psycopg.connect(admin, autocommit=True, connect_timeout=5) as db:
        exists = db.execute("SELECT 1 FROM pg_database WHERE datname='copilot_test'").fetchone()
        if not exists:
            db.execute("CREATE DATABASE copilot_test OWNER copilot")
    test_settings = Settings(make_conninfo(settings.database_url, dbname="copilot_test"), ROOT / ".local/test-storage", "mock", False, ("testserver",))
    migrate(test_settings)
    return test_settings


if __name__ == "__main__":
    prepare()
    print("Isolated copilot_test database prepared")
