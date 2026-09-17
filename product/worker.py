"""Independent durable worker with lease recovery and publication fencing."""
import argparse
import os
import time
from uuid import uuid4
from psycopg.types.json import Jsonb

from product.db import connect
from product.settings import Settings
from product.llm import respond, classify_error
from product.file_worker import parse_once
from product.index_worker import index_once
from product.rag import run_rag, sources_current
from product.project_context import context_prompt
from product.request_intent import classify_message, apply_revision
from product.research_agent import run_research
from product.artifacts import publish_artifacts
from product.execution import append_event, LeaseHeartbeat, RETRYABLE_ERRORS


def run_once(settings=None, responder=respond):
    settings = settings or Settings.load()
    with connect(settings) as db:
        abandoned = db.execute("SELECT j.*,r.status run_status FROM jobs j JOIN runs r ON r.id=j.run_id WHERE j.status='running' AND j.lease_until<now() FOR UPDATE OF j SKIP LOCKED").fetchall()
        for job in abandoned:
            if job["run_status"] == "cancelling":
                db.execute("UPDATE jobs SET status='cancelled',lease_token=NULL,lease_until=NULL,worker_id=NULL WHERE id=%s", (job["id"],))
                db.execute("UPDATE runs SET status='cancelled',error_code='cancelled',completed_at=now() WHERE id=%s", (job["run_id"],))
                db.execute("UPDATE trace_spans SET status='cancelled',error_code='cancelled' WHERE run_id=%s AND status='running'", (job["run_id"],))
                append_event(db, job["run_id"], "cancelled", {"reason": "lease_expired_after_cancel"})
            elif job["attempt"] < job["max_attempts"]:
                db.execute("UPDATE jobs SET status='queued',lease_token=NULL,lease_until=NULL,worker_id=NULL,retry_after=now(),last_error_code='worker_interrupted' WHERE id=%s", (job["id"],))
                db.execute("UPDATE runs SET status='queued',error_code=NULL WHERE id=%s AND status='running'", (job["run_id"],))
                db.execute("UPDATE trace_spans SET status='error',error_code='worker_interrupted' WHERE run_id=%s AND status='running'", (job["run_id"],))
                append_event(db, job["run_id"], "retry_scheduled", {"reason": "worker_interrupted", "next_attempt": job["attempt"] + 1})
            else:
                db.execute("UPDATE jobs SET status='failed',lease_token=NULL,lease_until=NULL,worker_id=NULL,last_error_code='worker_interrupted' WHERE id=%s", (job["id"],))
                db.execute("UPDATE runs SET status='failed',error_code='worker_interrupted',completed_at=now() WHERE id=%s AND status='running'", (job["run_id"],))
                db.execute("UPDATE trace_spans SET status='error',error_code='worker_interrupted' WHERE run_id=%s AND status='running'", (job["run_id"],))
                append_event(db, job["run_id"], "failed", {"error_code": "worker_interrupted"})
        job = db.execute("SELECT * FROM jobs WHERE status='queued' AND retry_after<=now() ORDER BY retry_after,created_at,id LIMIT 1 FOR UPDATE SKIP LOCKED").fetchone()
        if not job:
            return False
        token, root_span, model_span = uuid4(), uuid4(), uuid4()
        worker_id = f"{os.getpid()}:{token}"
        attempt = job["attempt"] + 1
        db.execute("UPDATE jobs SET status='running',attempt=%s,lease_token=%s,lease_until=now()+interval '180 seconds',heartbeat_at=now(),worker_id=%s WHERE id=%s", (attempt, token, worker_id, job["id"]))
        run = db.execute("UPDATE runs SET status='running' WHERE id=%s AND status='queued' RETURNING *", (job["run_id"],)).fetchone()
        if not run:
            db.execute("UPDATE jobs SET status='failed' WHERE id=%s", (job["id"],))
            return True
        rows = db.execute("SELECT id,role,content FROM messages WHERE conversation_id=%s AND owner_id=%s ORDER BY created_at DESC,id DESC LIMIT 12", (run["conversation_id"], run["owner_id"])).fetchall()
        rows.reverse()
        # Retain the latest message intact; remove complete older messages as a bounded M1 policy.
        while len(rows) > 1 and sum(len(row["content"]) for row in rows) > 30000:
            rows.pop(0)
        db.execute("INSERT INTO trace_spans(id,run_id,kind,name,status,metadata) VALUES(%s,%s,'run','conversation','running',%s)", (root_span, run["id"], Jsonb({"mode": run["mode"], "attempt": attempt})))
        append_event(db, run["id"], "started", {"attempt": attempt})
        if not run['skill_id']:
            db.execute("INSERT INTO trace_spans(id,run_id,parent_span_id,kind,name,status,metadata) VALUES(%s,%s,%s,'model',%s,'running',%s)", (model_span, run["id"], root_span, run["model"], Jsonb({"model": run["model"], "prompt_version": run["prompt_version"], "message_ids": [str(row["id"]) for row in rows], "context_chars": sum(len(row["content"]) for row in rows)})))
    started = time.monotonic()
    try:
        with LeaseHeartbeat(settings, job["id"], token):
            intent,intent_usage=classify_message(settings,run,rows[-1]['content'],root_span)
            run=apply_revision(settings,run,intent,root_span)
            conversation_messages=[{"role": row["role"], "content": row["content"]} for row in rows]
            if run.get('project_snapshot'):
                conversation_messages.insert(0,{'role':'system','content':context_prompt(run['project_snapshot'])})
            if run['skill_id'] in {'paper-review','evidence-survey'}:
                answer=run_research(settings,run,rows,root_span,attempt=attempt)
            else:
                answer = run_rag(settings,run,rows,root_span) if run['skill_id'] else responder(conversation_messages, run["mode"], run["model"])
            if intent_usage:
                answer.usage=answer.usage or {'prompt_tokens':0,'completion_tokens':0,'total_tokens':0}
                for key in ('prompt_tokens','completion_tokens','total_tokens'):
                    answer.usage[key]=answer.usage.get(key,0)+intent_usage.get(key,0)
        duration = int((time.monotonic()-started)*1000)
        with connect(settings) as db:
            project=db.execute('SELECT id,revision FROM projects WHERE id=%s FOR UPDATE',(run['project_id'],)).fetchone()
            active = db.execute("SELECT * FROM jobs WHERE id=%s AND lease_token=%s AND status='running' AND lease_until>now() FOR UPDATE", (job["id"], token)).fetchone()
            if not active:
                return True
            current = db.execute("SELECT status FROM runs WHERE id=%s FOR UPDATE", (run["id"],)).fetchone()
            if current["status"] == "cancelling":
                db.execute("UPDATE runs SET status='cancelled',error_code='cancelled',completed_at=now() WHERE id=%s", (run["id"],))
                db.execute("UPDATE jobs SET status='cancelled',lease_token=NULL,lease_until=NULL,worker_id=NULL WHERE id=%s AND lease_token=%s", (job["id"], token))
                db.execute("UPDATE trace_spans SET status='cancelled',duration_ms=%s,error_code='cancelled' WHERE run_id=%s AND status='running'", (duration, run["id"]))
                append_event(db, run["id"], "cancelled", {"attempt": attempt})
                return True
            if current["status"] != "running":
                return True
            if project['revision']!=run['project_revision']:
                raise ValueError('project_changed_before_publication')
            if answer.result and not sources_current(db,answer.result,run['owner_id'],run['project_id']):
                raise ValueError('source_changed_before_publication')
            artifacts=publish_artifacts(db,settings,run,answer)
            if artifacts:
                answer.result['artifacts']=artifacts
                db.execute("INSERT INTO trace_spans(id,run_id,parent_span_id,kind,name,status,metadata) VALUES(%s,%s,%s,'artifact','write_artifact','ok',%s)",(uuid4(),run['id'],root_span,Jsonb({'artifacts':artifacts})))
            message_id = uuid4()
            db.execute("INSERT INTO messages(id,conversation_id,project_id,owner_id,role,content,run_id,mode) VALUES(%s,%s,%s,%s,'assistant',%s,%s,%s)", (message_id, run["conversation_id"], run["project_id"], run["owner_id"], answer.content, run["id"], run["mode"]))
            db.execute("UPDATE runs SET status='completed',final_message_id=%s,usage=%s,result_json=%s,completed_at=now() WHERE id=%s", (message_id, Jsonb(answer.usage), Jsonb(answer.result) if answer.result else None, run["id"]))
            for reference in (answer.result or {}).get('references',[]):
                db.execute('INSERT INTO run_citations(run_id,ordinal,chunk_id,quote) VALUES(%s,%s,%s,%s)',(run['id'],reference['ordinal'],reference['chunk_id'],reference['quote']))
            db.execute("UPDATE jobs SET status='completed',lease_token=NULL,lease_until=NULL,worker_id=NULL WHERE id=%s AND lease_token=%s", (job["id"], token))
            db.execute("UPDATE trace_spans SET status='ok',duration_ms=%s WHERE run_id=%s AND status='running'", (duration, run["id"]))
            db.execute("UPDATE conversations SET updated_at=now() WHERE id=%s", (run["conversation_id"],))
            append_event(db, run["id"], "completed", {"attempt": attempt, "message_id": str(message_id)})
        print(f"completed run={run['id']} mode={run['mode']} duration_ms={duration}", flush=True)
    except Exception as error:
        code = classify_error(error)
        duration = int((time.monotonic()-started)*1000)
        with connect(settings) as db:
            active = db.execute("SELECT * FROM jobs WHERE id=%s AND lease_token=%s AND status='running' FOR UPDATE", (job["id"], token)).fetchone()
            current = db.execute("SELECT status FROM runs WHERE id=%s FOR UPDATE", (run["id"],)).fetchone()
            if active and current["status"] == "cancelling":
                db.execute("UPDATE jobs SET status='cancelled',lease_token=NULL,lease_until=NULL,worker_id=NULL,last_error_code=%s WHERE id=%s", (code, job["id"]))
                db.execute("UPDATE runs SET status='cancelled',error_code='cancelled',completed_at=now() WHERE id=%s", (run["id"],))
                db.execute("UPDATE trace_spans SET status='cancelled',duration_ms=%s,error_code='cancelled' WHERE run_id=%s AND status='running'", (duration, run["id"]))
                append_event(db, run["id"], "cancelled", {"attempt": attempt})
            elif active and code in RETRYABLE_ERRORS and attempt < active["max_attempts"]:
                db.execute("UPDATE jobs SET status='queued',lease_token=NULL,lease_until=NULL,worker_id=NULL,last_error_code=%s,retry_after=now()+(%s * interval '1 second') WHERE id=%s", (code, attempt, job["id"]))
                db.execute("UPDATE runs SET status='queued',error_code=NULL WHERE id=%s", (run["id"],))
                db.execute("UPDATE trace_spans SET status='error',duration_ms=%s,error_code=%s WHERE run_id=%s AND status='running'", (duration, code, run["id"]))
                append_event(db, run["id"], "retry_scheduled", {"error_code": code, "next_attempt": attempt + 1, "backoff_seconds": attempt})
            elif active:
                db.execute("UPDATE jobs SET status='failed',lease_token=NULL,lease_until=NULL,worker_id=NULL,last_error_code=%s WHERE id=%s", (code, job["id"]))
                db.execute("UPDATE runs SET status='failed',error_code=%s,completed_at=now() WHERE id=%s", (code, run["id"]))
                db.execute("UPDATE trace_spans SET status='error',duration_ms=%s,error_code=%s WHERE run_id=%s AND status='running'", (duration, code, run["id"]))
                append_event(db, run["id"], "failed", {"error_code": code, "attempt": attempt})
        print(f"handled run={run['id']} error_code={code} attempt={attempt}", flush=True)
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    settings = Settings.load()
    while True:
        consumed = run_once(settings)
        consumed = parse_once(settings) or consumed
        consumed = index_once(settings) or consumed
        if args.once:
            return
        if not consumed:
            time.sleep(1)


if __name__ == "__main__":
    main()
