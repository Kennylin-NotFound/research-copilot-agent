import unittest
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
from pathlib import Path
import sys

from fastapi.testclient import TestClient
from psycopg.conninfo import conninfo_to_dict
from product.api import create_app
from product.auth import password_hash, token_hash
from product.db import connect
from product.worker import run_once
from product.llm import ModelAnswer

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from product_prepare_test_db import prepare

HEADERS = {"X-Copilot-Request": "1"}
CREDENTIALS = {"username": "test_owner", "password": "OnlyForLocalTests!2026"}


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.settings = prepare()

    def setUp(self):
        cfg = conninfo_to_dict(self.settings.database_url)
        assert cfg["dbname"] == "copilot_test" and cfg["host"] == "127.0.0.1" and cfg["port"] == "15432"
        with connect(self.settings) as db:
            db.execute("TRUNCATE users, login_attempts CASCADE")
        self.client = TestClient(create_app(self.settings), headers=HEADERS, client=("127.0.0.1", 50000))
        self.client.__enter__()
        response = self.client.post("/api/setup", json=CREDENTIALS)
        self.assertEqual(response.status_code, 201, response.text)
        self.owner = response.json()

    def tearDown(self):
        self.client.__exit__(None, None, None)

    def conversation(self):
        project = self.client.post("/api/projects", json={"title": "ReAct 研究"}).json()
        conversation = self.client.post(f"/api/projects/{project['id']}/conversations", json={"title": "机制讨论"}).json()
        return project, conversation

    def send(self, conversation, content="请帮我澄清研究目标", message_id=None):
        return self.client.post(f"/api/conversations/{conversation['id']}/messages", json={"content": content, "client_message_id": message_id or str(uuid4())})

    def test_setup_is_single_use_and_sessions_are_revocable(self):
        self.assertEqual(self.client.post("/api/setup", json=CREDENTIALS).status_code, 409)
        cookie = self.client.cookies.get("copilot_session")
        with connect(self.settings) as db:
            user = db.execute("SELECT * FROM users").fetchone()
            session = db.execute("SELECT * FROM sessions").fetchone()
        self.assertTrue(user["password_hash"].startswith("$argon2"))
        self.assertNotEqual(user["password_hash"], CREDENTIALS["password"])
        self.assertEqual(session["token_hash"], token_hash(cookie))
        self.assertEqual(self.client.post("/api/logout").status_code, 200)
        self.client.cookies.set("copilot_session", cookie)
        self.assertEqual(self.client.get("/api/me").status_code, 401)

    def test_conversation_rename_archive_restore_and_project_switch(self):
        project, conv = self.conversation()
        path = f"/api/conversations/{conv['id']}"
        self.assertEqual(self.client.patch(path, json={"title": "新的标题", "archived": True}).json()["title"], "新的标题")
        self.assertEqual(self.client.get(f"/api/projects/{project['id']}/conversations").json(), [])
        self.assertEqual(len(self.client.get(f"/api/projects/{project['id']}/conversations?archived=true").json()), 1)
        self.assertEqual(self.send(conv).status_code, 409)
        self.assertEqual(self.client.patch(path, json={"archived": False}).status_code, 200)
        self.assertEqual(self.send(conv).status_code, 202)
        other = self.client.post("/api/projects", json={"title": "另一项目"}).json()
        self.assertEqual(self.client.get(f"/api/projects/{other['id']}/conversations").json(), [])

    def test_duplicate_message_creates_one_logical_job_and_rejects_changed_body(self):
        _, conv = self.conversation()
        message_id = str(uuid4())
        first = self.send(conv, message_id=message_id).json()
        second = self.send(conv, message_id=message_id).json()
        self.assertEqual(first["run_id"], second["run_id"])
        self.assertTrue(second["deduplicated"])
        self.assertEqual(self.send(conv, content="different", message_id=message_id).status_code, 409)
        with connect(self.settings) as db:
            for table in ("messages", "runs", "jobs"):
                self.assertEqual(db.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"], 1)

    def test_simultaneous_duplicate_submissions_create_one_job(self):
        _, conv = self.conversation()
        message_id = str(uuid4())
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.send(conv, message_id=message_id).json(), range(2)))
        self.assertEqual(results[0]["run_id"], results[1]["run_id"])
        with connect(self.settings) as db:
            self.assertEqual(db.execute("SELECT count(*) AS n FROM jobs").fetchone()["n"], 1)

    def test_worker_persists_response_and_links_trace(self):
        _, conv = self.conversation()
        run = self.send(conv).json()
        self.assertTrue(run_once(self.settings))
        self.assertFalse(run_once(self.settings))
        messages = self.client.get(f"/api/conversations/{conv['id']}/messages").json()
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[1]["run_id"], run["run_id"])
        self.assertTrue(messages[1]["content"].startswith("[开发替身]"))
        detail = self.client.get(f"/api/runs/{run['run_id']}").json()
        self.assertEqual(detail["status"], "completed")
        self.assertEqual(detail["model"], "mock-conversation")
        self.assertEqual(len(detail["spans"]), 2)
        self.assertEqual({span["status"] for span in detail["spans"]}, {"ok"})

    def test_cross_owner_requests_rejected_on_all_domain_routes(self):
        project, conv = self.conversation()
        run = self.send(conv).json()
        with connect(self.settings) as db:
            db.execute("INSERT INTO users(id,username,password_hash) VALUES(%s,%s,%s)", (uuid4(), "second_owner", password_hash.hash(CREDENTIALS["password"])))
        self.client.post("/api/logout")
        self.assertEqual(self.client.post("/api/login", json=CREDENTIALS | {"username": "second_owner"}).status_code, 200)
        self.assertEqual(self.client.get("/api/projects").json(), [])
        for method, url, body in (("get", f"/api/projects/{project['id']}/conversations", None),
                                  ("post", f"/api/projects/{project['id']}/conversations", {"title": "bad"}),
                                  ("patch", f"/api/projects/{project['id']}", {"title": "bad"}),
                                  ("get", f"/api/conversations/{conv['id']}/messages", None),
                                  ("get", f"/api/conversations/{conv['id']}/runs", None),
                                  ("patch", f"/api/conversations/{conv['id']}", {"archived": True}),
                                  ("get", f"/api/runs/{run['run_id']}", None)):
            with self.subTest(url=url, method=method):
                self.assertEqual(self.client.request(method, url, json=body).status_code, 404)
        self.assertEqual(self.send(conv).status_code, 404)

    def test_job_insert_failure_rolls_back_message_and_run(self):
        _, conv = self.conversation()
        with connect(self.settings) as db:
            db.execute("CREATE OR REPLACE FUNCTION test_reject_job() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'controlled test failure'; END; $$")
            db.execute("CREATE TRIGGER test_reject_job BEFORE INSERT ON jobs FOR EACH ROW EXECUTE FUNCTION test_reject_job()")
        try:
            with self.assertRaises(Exception):
                self.send(conv)
        finally:
            with connect(self.settings) as db:
                db.execute("DROP TRIGGER test_reject_job ON jobs")
                db.execute("DROP FUNCTION test_reject_job()")
        with connect(self.settings) as db:
            for table in ("messages", "runs", "jobs"):
                self.assertEqual(db.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"], 0)

    def test_restart_keeps_session_messages_and_runs(self):
        _, conv = self.conversation()
        run = self.send(conv).json()
        run_once(self.settings)
        cookie = self.client.cookies.get("copilot_session")
        with TestClient(create_app(self.settings), headers=HEADERS, client=("127.0.0.1", 50001)) as restarted:
            restarted.cookies.set("copilot_session", cookie)
            self.assertEqual(restarted.get("/api/me").status_code, 200)
            self.assertEqual(len(restarted.get(f"/api/conversations/{conv['id']}/messages").json()), 2)
            self.assertEqual(restarted.get(f"/api/runs/{run['run_id']}").json()["status"], "completed")

    def test_failed_model_has_sanitized_trace_and_releases_project(self):
        _, conv = self.conversation()
        run = self.send(conv).json()
        def fail(*args):
            raise RuntimeError("fake-private-key-never-log")
        run_once(self.settings, responder=fail)
        response = self.client.get(f"/api/runs/{run['run_id']}")
        self.assertEqual(response.json()["status"], "failed")
        self.assertNotIn("fake-private-key", response.text)
        self.assertEqual(self.send(conv).status_code, 202)

    def test_csrf_host_validation_and_expired_sessions(self):
        self.assertEqual(self.client.post("/api/projects", json={"title": "bad"}, headers={"Origin": "https://evil.example"}).status_code, 403)
        self.assertEqual(self.client.post("/api/projects", json={"title": "bad"}, headers={"X-Copilot-Request": ""}).status_code, 403)
        self.assertEqual(self.client.get("/api/me", headers={"Host": "evil.example"}).status_code, 400)
        with connect(self.settings) as db:
            db.execute("UPDATE sessions SET expires_at=now()-interval '1 minute'")
        self.assertEqual(self.client.get("/api/me").status_code, 401)

    def test_model_receives_prior_conversation_in_order(self):
        _, conv = self.conversation()
        self.send(conv, "研究目标是可靠性")
        run_once(self.settings)
        self.send(conv, "请回顾上一个目标")
        seen = []
        def capture(messages, mode, model):
            seen.extend(messages)
            return ModelAnswer("已回顾", None)
        run_once(self.settings, responder=capture)
        self.assertEqual([m["role"] for m in seen], ["system", "user", "assistant", "user"])
        self.assertEqual(seen[1]["content"], "研究目标是可靠性")


if __name__ == "__main__":
    unittest.main()
