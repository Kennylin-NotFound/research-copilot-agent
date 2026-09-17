import sys
import unittest
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from product.api import create_app
from product.auth import password_hash
from product.db import connect
from product.llm import ModelAnswer
from product.worker import run_once

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from product_prepare_test_db import prepare


HEADERS = {"X-Copilot-Request": "1"}
PASSWORD = "OnlyForObservabilityTests!2026"


class ObservabilityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.settings = prepare()

    def setUp(self):
        with connect(self.settings) as db:
            db.execute("TRUNCATE users, login_attempts CASCADE")
        self.client = TestClient(create_app(self.settings), headers=HEADERS, client=("127.0.0.1", 50200))
        self.client.__enter__()
        self.client.post("/api/setup", json={"username": "trace_owner", "password": PASSWORD})
        project = self.client.post("/api/projects", json={"title": "可观察性"}).json()
        self.conversation = self.client.post(f"/api/projects/{project['id']}/conversations", json={"title": "反馈"}).json()

    def tearDown(self):
        self.client.__exit__(None, None, None)

    def completed_run(self):
        response = self.client.post(
            f"/api/conversations/{self.conversation['id']}/messages",
            json={"content": "请回答", "client_message_id": str(uuid4()), "model_id": "default"},
        )
        run = response.json()
        run_once(self.settings, responder=lambda *args: ModelAnswer("可反馈回答", {"total_tokens": 2}))
        messages = self.client.get(f"/api/conversations/{self.conversation['id']}/messages").json()
        return run, messages[-1]

    def test_feedback_is_bound_to_assistant_and_upserts(self):
        run, assistant = self.completed_run()
        first = self.client.post(f"/api/messages/{assistant['id']}/feedback", json={"rating": 1})
        self.assertEqual(first.status_code, 200, first.text)
        second = self.client.post(f"/api/messages/{assistant['id']}/feedback", json={"rating": -1, "note": "引用还可更聚焦"})
        self.assertEqual(second.json()["rating"], -1)
        detail = self.client.get(f"/api/runs/{run['run_id']}").json()
        self.assertEqual(len(detail["feedback"]), 1)
        self.assertEqual(detail["feedback"][0]["note"], "引用还可更聚焦")
        user = self.client.get(f"/api/conversations/{self.conversation['id']}/messages").json()[0]
        self.assertEqual(self.client.post(f"/api/messages/{user['id']}/feedback", json={"rating": 1}).status_code, 404)

    def test_trace_export_redacts_sensitive_keys_and_is_owner_scoped(self):
        run, _ = self.completed_run()
        with connect(self.settings) as db:
            db.execute("UPDATE trace_spans SET metadata=metadata || %s WHERE run_id=%s", (Jsonb({"api_key": "never-export-this", "safe_summary": "kept"}), run["run_id"]))
        response = self.client.get(f"/api/runs/{run['run_id']}/export")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("never-export-this", response.text)
        self.assertIn("[REDACTED]", response.text)
        payload = response.json()
        self.assertEqual(payload["run"]["id"], run["run_id"])
        self.assertTrue(payload["events"])
        self.assertTrue(payload["spans"])

        with connect(self.settings) as db:
            db.execute("INSERT INTO users(id,username,password_hash) VALUES(%s,%s,%s)", (uuid4(), "trace_other", password_hash.hash(PASSWORD)))
        self.client.post("/api/logout")
        self.client.post("/api/login", json={"username": "trace_other", "password": PASSWORD})
        self.assertEqual(self.client.get(f"/api/runs/{run['run_id']}/export").status_code, 404)

    def test_model_profile_is_part_of_idempotency_contract(self):
        identity = str(uuid4())
        body = {"content": "同一问题", "client_message_id": identity, "model_id": "default"}
        first = self.client.post(f"/api/conversations/{self.conversation['id']}/messages", json=body)
        self.assertEqual(first.status_code, 202)
        conflict = self.client.post(f"/api/conversations/{self.conversation['id']}/messages", json=body | {"model_id": "fast"})
        self.assertEqual(conflict.status_code, 409)


if __name__ == "__main__":
    unittest.main()
