import sys
import unittest
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from product.api import create_app
from product.db import connect
from product.execution import append_event
from product.llm import ModelAnswer
from product.worker import run_once

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from product_prepare_test_db import prepare


HEADERS = {"X-Copilot-Request": "1"}
CREDENTIALS = {"username": "execution_owner", "password": "OnlyForExecutionTests!2026"}


class ProviderTimeoutError(RuntimeError):
    pass


class ExecutionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.settings = prepare()

    def setUp(self):
        with connect(self.settings) as db:
            db.execute("TRUNCATE users, login_attempts CASCADE")
        self.client = TestClient(create_app(self.settings), headers=HEADERS, client=("127.0.0.1", 50100))
        self.client.__enter__()
        self.client.post("/api/setup", json=CREDENTIALS).raise_for_status()
        project = self.client.post("/api/projects", json={"title": "执行可靠性"}).json()
        self.conversation = self.client.post(f"/api/projects/{project['id']}/conversations", json={"title": "故障注入"}).json()

    def tearDown(self):
        self.client.__exit__(None, None, None)

    def send(self, content="测试恢复"):
        response = self.client.post(
            f"/api/conversations/{self.conversation['id']}/messages",
            json={"content": content, "client_message_id": str(uuid4())},
        )
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()

    def events(self, run_id, after=0):
        response = self.client.get(f"/api/runs/{run_id}/events?after={after}")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_transient_timeout_retries_once_and_publishes_one_answer(self):
        run = self.send()
        calls = 0

        def flaky(*args):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise ProviderTimeoutError("private timeout detail")
            return ModelAnswer("恢复后的唯一回答", {"total_tokens": 3})

        self.assertTrue(run_once(self.settings, responder=flaky))
        self.assertEqual(self.client.get(f"/api/runs/{run['run_id']}").json()["status"], "queued")
        with connect(self.settings) as db:
            db.execute("UPDATE jobs SET retry_after=now() WHERE run_id=%s", (run["run_id"],))
        self.assertTrue(run_once(self.settings, responder=flaky))
        detail = self.client.get(f"/api/runs/{run['run_id']}").json()
        self.assertEqual(detail["status"], "completed")
        messages = self.client.get(f"/api/conversations/{self.conversation['id']}/messages").json()
        self.assertEqual([m["role"] for m in messages], ["user", "assistant"])
        self.assertEqual(messages[-1]["content"], "恢复后的唯一回答")
        with connect(self.settings) as db:
            job = db.execute("SELECT attempt,status FROM jobs WHERE run_id=%s", (run["run_id"],)).fetchone()
        self.assertEqual((job["attempt"], job["status"]), (2, "completed"))
        self.assertEqual([e["event_type"] for e in self.events(run["run_id"])],
                         ["queued", "started", "retry_scheduled", "started", "completed"])

    def test_expired_lease_is_reconciled_and_fenced_by_attempt(self):
        run = self.send("模拟 worker 被终止")
        with connect(self.settings) as db:
            db.execute("UPDATE jobs SET status='running',attempt=1,lease_token=%s,lease_until=now()-interval '1 second' WHERE run_id=%s", (uuid4(), run["run_id"]))
            db.execute("UPDATE runs SET status='running' WHERE id=%s", (run["run_id"],))
        self.assertTrue(run_once(self.settings, responder=lambda *args: ModelAnswer("重启恢复完成", None)))
        detail = self.client.get(f"/api/runs/{run['run_id']}").json()
        self.assertEqual(detail["status"], "completed")
        with connect(self.settings) as db:
            job = db.execute("SELECT attempt,status FROM jobs WHERE run_id=%s", (run["run_id"],)).fetchone()
        self.assertEqual((job["attempt"], job["status"]), (2, "completed"))
        self.assertIn("retry_scheduled", [e["event_type"] for e in self.events(run["run_id"])])

    def test_queued_cancel_is_idempotent_and_releases_project(self):
        run = self.send("取消排队任务")
        first = self.client.post(f"/api/runs/{run['run_id']}/cancel")
        self.assertEqual(first.json()["status"], "cancelled")
        second = self.client.post(f"/api/runs/{run['run_id']}/cancel")
        self.assertTrue(second.json()["deduplicated"])
        self.assertEqual(self.send("取消后新任务")["deduplicated"], False)
        self.assertEqual([e["event_type"] for e in self.events(run["run_id"])], ["queued", "cancelled"])

    def test_cancel_during_call_prevents_late_publication(self):
        run = self.send("运行中取消")

        def cancel_then_return(*args):
            with connect(self.settings) as db:
                db.execute("UPDATE runs SET status='cancelling' WHERE id=%s", (run["run_id"],))
                append_event(db, run["run_id"], "cancel_requested", {"source": "test"})
            return ModelAnswer("这条迟到回答不能发布", None)

        self.assertTrue(run_once(self.settings, responder=cancel_then_return))
        detail = self.client.get(f"/api/runs/{run['run_id']}").json()
        self.assertEqual(detail["status"], "cancelled")
        messages = self.client.get(f"/api/conversations/{self.conversation['id']}/messages").json()
        self.assertEqual([m["role"] for m in messages], ["user"])
        self.assertEqual([e["event_type"] for e in self.events(run["run_id"])][-2:], ["cancel_requested", "cancelled"])

    def test_schema_error_uses_inner_bounded_repair_not_worker_retry(self):
        run = self.send("不可修复的结构错误")
        self.assertTrue(run_once(self.settings, responder=lambda *args: (_ for _ in ()).throw(ValueError("bad schema"))))
        detail = self.client.get(f"/api/runs/{run['run_id']}").json()
        self.assertEqual((detail["status"], detail["error_code"]), ("failed", "schema_error"))
        with connect(self.settings) as db:
            job = db.execute("SELECT attempt,status FROM jobs WHERE run_id=%s", (run["run_id"],)).fetchone()
        self.assertEqual((job["attempt"], job["status"]), (1, "failed"))

    def test_event_replay_cursor_and_owner_boundary(self):
        run = self.send("事件重放")
        run_once(self.settings, responder=lambda *args: ModelAnswer("完成", None))
        all_events = self.events(run["run_id"])
        tail = self.events(run["run_id"], after=all_events[0]["seq"])
        self.assertEqual(tail, all_events[1:])
        self.client.post("/api/logout")
        self.assertEqual(self.client.get(f"/api/runs/{run['run_id']}/events").status_code, 401)


if __name__ == "__main__":
    unittest.main()
