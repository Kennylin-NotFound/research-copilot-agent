import unittest

from product.api import create_app
from product.db import connect
from product.manage_user import upsert_user
from tests.product import test_api as base


class ManageUserTest(unittest.TestCase):
    setUpClass = classmethod(base.ApiTest.setUpClass.__func__)

    def setUp(self):
        with connect(self.settings) as db:
            db.execute("TRUNCATE users, login_attempts CASCADE")

    def test_weak_password_requires_explicit_demo_override_and_login_accepts_existing_hash(self):
        with self.assertRaisesRegex(ValueError, "password_too_short"):
            upsert_user(self.settings, "demo_user", "weak6!")
        created = upsert_user(self.settings, "demo_user", "weak6!", allow_weak_demo_password=True)
        self.assertEqual(created["action"], "created")
        from fastapi.testclient import TestClient
        with TestClient(create_app(self.settings), headers=base.HEADERS, client=("127.0.0.1", 50020)) as client:
            login = client.post("/api/login", json={"username": "demo_user", "password": "weak6!"})
            self.assertEqual(login.status_code, 200, login.text)

    def test_rotation_preserves_identity_and_revokes_sessions(self):
        first = upsert_user(self.settings, "demo_user", "LongPassword-1")
        with connect(self.settings) as db:
            db.execute(
                "INSERT INTO sessions(token_hash,owner_id,expires_at) VALUES('a',%s,now()+interval '1 hour')",
                (first["id"],),
            )
        second = upsert_user(self.settings, "demo_user", "LongPassword-2")
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(second["action"], "updated")
        with connect(self.settings) as db:
            self.assertEqual(db.execute("SELECT count(*) n FROM sessions WHERE owner_id=%s", (first["id"],)).fetchone()["n"], 0)


if __name__ == "__main__":
    unittest.main()
