"""Same-origin web API. Every domain query includes the authenticated owner."""
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
import json
import time
from uuid import UUID, uuid4

from fastapi import FastAPI, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from psycopg.types.json import Jsonb

from product.settings import Settings
from product.db import connect, migrate
from product.auth import password_hash, DUMMY_HASH, token_hash, issue_session
from product.api_schemas import SetupCredentials, LoginCredentials, Title, ConversationUpdate, SendMessage, FeedbackInput
from product.contracts import ProjectState
from product.llm import PROMPT_VERSION, model_name
from product.file_api import file_router
from product.skills_registry import AVAILABLE, load_skill, display_name as skill_display_name
from product import rag
from product.project_context import context_router, snapshot as project_snapshot
from product.execution import append_event
from product.model_registry import available_models, resolve_model, estimate_cost
from product.observability import configured_secrets, redact


def create_app(settings: Settings | None = None):
    settings = settings or Settings.load()

    @asynccontextmanager
    async def lifespan(app):
        migrate(settings)
        yield

    app = FastAPI(title="Research Copilot", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.settings = settings

    @app.middleware("http")
    async def browser_boundary(request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if request.headers.get("x-copilot-request") != "1" or (origin and origin != str(request.base_url).rstrip("/")):
                return JSONResponse({"detail": "invalid_request_origin"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.allowed_hosts))

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        return JSONResponse({"detail": "invalid_input", "fields": [".".join(map(str, e["loc"])) for e in exc.errors()]}, status_code=422)

    def current_user(request: Request):
        raw = request.cookies.get("copilot_session", "")
        if not raw:
            raise HTTPException(401, "login_required")
        with connect(settings) as db:
            user = db.execute("SELECT u.id, u.username FROM sessions s JOIN users u ON u.id=s.owner_id WHERE s.token_hash=%s AND s.expires_at>now()", (token_hash(raw),)).fetchone()
        if not user:
            raise HTTPException(401, "login_required")
        return user

    def set_cookie(response, raw):
        response.set_cookie("copilot_session", raw, max_age=7*86400, httponly=True,
                            secure=settings.cookie_secure, samesite="strict", path="/")

    def project_owned(db, identity, user):
        row = db.execute("SELECT * FROM projects WHERE id=%s AND owner_id=%s", (identity, user["id"])).fetchone()
        if not row:
            raise HTTPException(404, "project_not_found")
        return row

    def conversation_owned(db, identity, user):
        row = db.execute("SELECT * FROM conversations WHERE id=%s AND owner_id=%s", (identity, user["id"])).fetchone()
        if not row:
            raise HTTPException(404, "conversation_not_found")
        return row

    @app.get("/health")
    def health():
        with connect(settings) as db:
            db.execute("SELECT 1")
        return {"status": "ok", "version": settings.app_version, "environment": settings.app_env, "mode": settings.mode}

    @app.get("/api/setup")
    def setup_status():
        with connect(settings) as db:
            empty = not db.execute("SELECT id FROM users LIMIT 1").fetchone()
        available = not settings.cookie_secure and settings.allow_setup
        return {"required": empty and available, "available": available}

    @app.post("/api/setup", status_code=201)
    def setup(credentials: SetupCredentials, request: Request, response: Response):
        if settings.cookie_secure or not settings.allow_setup:
            raise HTTPException(403, "local_setup_only")
        with connect(settings) as db:
            db.execute("SELECT pg_advisory_xact_lock(8931702)")
            if db.execute("SELECT id FROM users LIMIT 1").fetchone():
                raise HTTPException(409, "setup_already_completed")
            identity = uuid4()
            db.execute("INSERT INTO users(id,username,password_hash) VALUES(%s,%s,%s)",
                       (identity, credentials.username.lower(), password_hash.hash(credentials.password)))
            raw = issue_session(db, identity)
        set_cookie(response, raw)
        return {"id": identity, "username": credentials.username.lower()}

    @app.post("/api/login")
    def login(credentials: LoginCredentials, response: Response):
        username = credentials.username.lower()
        with connect(settings) as db:
            db.execute("INSERT INTO login_attempts(username) VALUES(%s) ON CONFLICT DO NOTHING", (username,))
            attempt = db.execute("SELECT * FROM login_attempts WHERE username=%s FOR UPDATE", (username,)).fetchone()
            if attempt["window_started"] < datetime.now(timezone.utc)-timedelta(minutes=15):
                db.execute("UPDATE login_attempts SET failures=0,window_started=now() WHERE username=%s", (username,))
                attempt["failures"] = 0
            if attempt["failures"] >= 10:
                raise HTTPException(429, "login_rate_limit")
            user = db.execute("SELECT * FROM users WHERE username=%s", (username,)).fetchone()
            valid = password_hash.verify(credentials.password, user["password_hash"] if user else DUMMY_HASH)
            if not user or not valid:
                db.execute("UPDATE login_attempts SET failures=failures+1 WHERE username=%s", (username,))
                raw = None
            else:
                db.execute("UPDATE login_attempts SET failures=0 WHERE username=%s", (username,))
                raw = issue_session(db, user["id"])
        if raw is None:
            raise HTTPException(401, "invalid_credentials")
        set_cookie(response, raw)
        return {"id": user["id"], "username": user["username"]}

    @app.post("/api/logout")
    def logout(request: Request, response: Response, user=Depends(current_user)):
        with connect(settings) as db:
            db.execute("DELETE FROM sessions WHERE token_hash=%s", (token_hash(request.cookies["copilot_session"]),))
        response.delete_cookie("copilot_session", path="/")
        return {"ok": True}

    @app.get("/api/me")
    def me(user=Depends(current_user)):
        return user

    @app.get("/api/projects")
    def projects(user=Depends(current_user)):
        with connect(settings) as db:
            return db.execute("SELECT id,title,revision,updated_at FROM projects WHERE owner_id=%s ORDER BY updated_at DESC,id", (user["id"],)).fetchall()

    @app.post("/api/projects", status_code=201)
    def new_project(body: Title, user=Depends(current_user)):
        with connect(settings) as db:
            return db.execute("INSERT INTO projects(id,owner_id,title,state) VALUES(%s,%s,%s,%s) RETURNING id,title,revision", (uuid4(), user["id"], body.title, Jsonb(ProjectState().model_dump(mode="json")))).fetchone()

    @app.patch("/api/projects/{project_id}")
    def rename_project(project_id: UUID, body: Title, user=Depends(current_user)):
        with connect(settings) as db:
            project_owned(db, project_id, user)
            return db.execute("UPDATE projects SET title=%s,updated_at=now() WHERE id=%s AND owner_id=%s RETURNING id,title,revision", (body.title, project_id, user["id"])).fetchone()

    @app.get("/api/projects/{project_id}/conversations")
    def conversations(project_id: UUID, archived: bool = False, user=Depends(current_user)):
        with connect(settings) as db:
            project_owned(db, project_id, user)
            return db.execute("SELECT id,title,archived,updated_at FROM conversations WHERE project_id=%s AND owner_id=%s AND archived=%s ORDER BY updated_at DESC,id", (project_id, user["id"], archived)).fetchall()

    @app.post("/api/projects/{project_id}/conversations", status_code=201)
    def new_conversation(project_id: UUID, body: Title, user=Depends(current_user)):
        with connect(settings) as db:
            project_owned(db, project_id, user)
            return db.execute("INSERT INTO conversations(id,project_id,owner_id,title) VALUES(%s,%s,%s,%s) RETURNING id,title,archived", (uuid4(), project_id, user["id"], body.title)).fetchone()

    @app.patch("/api/conversations/{conversation_id}")
    def update_conversation(conversation_id: UUID, body: ConversationUpdate, user=Depends(current_user)):
        with connect(settings) as db:
            row = conversation_owned(db, conversation_id, user)
            return db.execute("UPDATE conversations SET title=%s,archived=%s,updated_at=now() WHERE id=%s AND owner_id=%s RETURNING id,title,archived", (body.title if body.title is not None else row["title"], body.archived if body.archived is not None else row["archived"], conversation_id, user["id"])).fetchone()

    @app.get("/api/conversations/{conversation_id}/messages")
    def messages(conversation_id: UUID, user=Depends(current_user)):
        with connect(settings) as db:
            conversation_owned(db, conversation_id, user)
            return db.execute("SELECT m.id,m.role,m.content,m.run_id,m.mode,m.created_at,CASE WHEN m.role='assistant' THEN r.result_json ELSE NULL END result_json,COALESCE((SELECT f.rating FROM feedback f WHERE f.message_id=m.id AND f.owner_id=m.owner_id AND f.artifact_version_id IS NULL ORDER BY f.updated_at DESC LIMIT 1),0) feedback_rating FROM messages m LEFT JOIN runs r ON r.id=m.run_id WHERE m.conversation_id=%s AND m.owner_id=%s ORDER BY m.created_at,m.id", (conversation_id, user["id"])).fetchall()

    @app.post("/api/conversations/{conversation_id}/messages", status_code=202)
    def send_message(conversation_id: UUID, body: SendMessage, user=Depends(current_user)):
        with connect(settings) as db:
            conversation = conversation_owned(db, conversation_id, user)
            project = db.execute("SELECT * FROM projects WHERE id=%s AND owner_id=%s FOR UPDATE", (conversation["project_id"], user["id"])).fetchone()
            existing = db.execute("SELECT id,content,run_id FROM messages WHERE conversation_id=%s AND client_message_id=%s AND owner_id=%s", (conversation_id, body.client_message_id, user["id"])).fetchone()
            if existing:
                prior = db.execute('SELECT request_options,skill_id FROM runs WHERE id=%s',(existing['run_id'],)).fetchone()
                if (existing["content"] != body.content or prior['skill_id'] != body.skill_id
                        or prior['request_options'].get('file_ids') != ([str(i) for i in body.file_ids] if body.file_ids is not None else None)
                        or prior['request_options'].get('model_id','default') != body.model_id
                        or prior['request_options'].get('allow_network',False) != body.allow_network):
                    raise HTTPException(409, "idempotency_content_conflict")
                return {"message_id": existing["id"], "run_id": existing["run_id"], "deduplicated": True}
            if conversation["archived"]:
                raise HTTPException(409, "conversation_archived")
            if db.execute("SELECT id FROM runs WHERE project_id=%s AND status IN ('queued','running','waiting_user','cancelling')", (project["id"],)).fetchone():
                raise HTTPException(409, "project_busy")
            if body.file_ids:
                found = db.execute('SELECT id FROM files WHERE project_id=%s AND owner_id=%s AND id=ANY(%s::uuid[]) AND deleted_at IS NULL',(project['id'],user['id'],[str(i) for i in body.file_ids])).fetchall()
                if {r['id'] for r in found} != set(body.file_ids):
                    raise HTTPException(404,'selected_file_not_found')
            message_id, run_id, trace_id = uuid4(), uuid4(), uuid4()
            db.execute("INSERT INTO messages(id,conversation_id,project_id,owner_id,role,content,client_message_id,mode) VALUES(%s,%s,%s,%s,'user',%s,%s,%s)", (message_id, conversation_id, project["id"], user["id"], body.content, body.client_message_id, settings.mode))
            chosen_model=model_name(settings.mode) if settings.mode=='mock' else resolve_model(body.model_id)
            db.execute("INSERT INTO runs(id,trace_id,conversation_id,project_id,owner_id,user_message_id,project_revision,status,mode,model,prompt_version) VALUES(%s,%s,%s,%s,%s,%s,%s,'queued',%s,%s,%s)", (run_id, trace_id, conversation_id, project["id"], user["id"], message_id, project["revision"], settings.mode, chosen_model, PROMPT_VERSION))
            options={'file_ids':[str(i) for i in body.file_ids] if body.file_ids is not None else None,
                     'model_id':body.model_id,'allow_network':body.allow_network}
            snapshot=load_skill(body.skill_id) if body.skill_id else None
            db.execute('UPDATE runs SET skill_id=%s,skill_snapshot=%s,request_options=%s,prompt_version=%s WHERE id=%s',(body.skill_id,Jsonb(snapshot) if snapshot else None,Jsonb(options),rag.PROMPT_VERSION if snapshot else PROMPT_VERSION,run_id))
            db.execute('UPDATE runs SET project_snapshot=%s WHERE id=%s',(Jsonb(project_snapshot(db,project)),run_id))
            db.execute("UPDATE messages SET run_id=%s WHERE id=%s", (run_id, message_id))
            db.execute("INSERT INTO jobs(id,run_id,status) VALUES(%s,%s,'queued')", (uuid4(), run_id))
            append_event(db, run_id, "queued", {"project_revision": project["revision"]})
            db.execute("UPDATE conversations SET updated_at=now() WHERE id=%s", (conversation_id,))
            return {"message_id": message_id, "run_id": run_id, "trace_id": trace_id, "deduplicated": False}

    @app.get("/api/conversations/{conversation_id}/runs")
    def runs(conversation_id: UUID, user=Depends(current_user)):
        with connect(settings) as db:
            conversation_owned(db, conversation_id, user)
            rows = db.execute(
                "SELECT r.id,r.trace_id,r.status,r.mode,r.model,r.error_code,r.skill_id,r.created_at,"
                "u.content user_content,a.content answer_content FROM runs r "
                "JOIN messages u ON u.id=r.user_message_id "
                "LEFT JOIN messages a ON a.id=r.final_message_id "
                "WHERE r.conversation_id=%s AND r.owner_id=%s ORDER BY r.created_at DESC,r.id LIMIT 30",
                (conversation_id, user["id"]),
            ).fetchall()
            for row in rows:
                source = " ".join((row.pop("answer_content") or row.pop("user_content") or "").split())
                summary = source[:22] + ("…" if len(source) > 22 else "")
                row["display_name"] = skill_display_name(row["skill_id"]) + (f" · {summary}" if summary else "")
            return rows

    @app.get("/api/runs/{run_id}")
    def run_detail(run_id: UUID, user=Depends(current_user)):
        with connect(settings) as db:
            row = db.execute("SELECT * FROM runs WHERE id=%s AND owner_id=%s", (run_id, user["id"])).fetchone()
            if not row:
                raise HTTPException(404, "run_not_found")
            row["spans"] = db.execute("SELECT * FROM trace_spans WHERE run_id=%s ORDER BY started_at, CASE WHEN parent_span_id IS NULL THEN 0 ELSE 1 END,id", (run_id,)).fetchall()
            context=db.execute('SELECT context FROM context_snapshots WHERE run_id=%s',(run_id,)).fetchone()
            row['context_snapshot']=context['context'] if context else None
            row['actions']=db.execute("SELECT action_id,attempt,action_type,status,result_summary,error_code,created_at,updated_at FROM action_records WHERE run_id=%s ORDER BY created_at,action_id,attempt",(run_id,)).fetchall()
            row['feedback']=db.execute("SELECT id,message_id,artifact_version_id,rating,note,created_at,updated_at FROM feedback WHERE run_id=%s AND owner_id=%s ORDER BY created_at,id",(run_id,user['id'])).fetchall()
            row['cost_estimate']=estimate_cost(row['model'],row['usage'],row.get('completed_at') or row['created_at'])
            return redact(row,configured_secrets(Path(__file__).resolve().parents[1]))

    @app.get("/api/runs/{run_id}/export")
    def export_run(run_id: UUID, user=Depends(current_user)):
        with connect(settings) as db:
            run=db.execute("SELECT id,trace_id,conversation_id,project_id,project_revision,status,mode,model,prompt_version,skill_id,error_code,usage,created_at,completed_at,request_options,skill_snapshot,result_json,project_snapshot FROM runs WHERE id=%s AND owner_id=%s",(run_id,user['id'])).fetchone()
            if not run:
                raise HTTPException(404,"run_not_found")
            run['cost_estimate']=estimate_cost(run['model'],run['usage'],run.get('completed_at') or run['created_at'])
            payload={
                'schema_version':1,'run':run,
                'spans':db.execute("SELECT id,parent_span_id,kind,name,status,metadata,started_at,duration_ms,error_code FROM trace_spans WHERE run_id=%s ORDER BY started_at,id",(run_id,)).fetchall(),
                'actions':db.execute("SELECT action_id,attempt,action_type,status,request_fingerprint,result_summary,error_code,created_at,updated_at FROM action_records WHERE run_id=%s ORDER BY created_at,action_id,attempt",(run_id,)).fetchall(),
                'events':db.execute("SELECT seq,event_type,payload,created_at FROM run_events WHERE run_id=%s ORDER BY seq",(run_id,)).fetchall(),
                'citations':db.execute("SELECT ordinal,chunk_id,quote FROM run_citations WHERE run_id=%s ORDER BY ordinal",(run_id,)).fetchall(),
                'feedback':db.execute("SELECT message_id,artifact_version_id,rating,note,created_at FROM feedback WHERE run_id=%s AND owner_id=%s ORDER BY created_at,id",(run_id,user['id'])).fetchall(),
            }
        return redact(payload,configured_secrets(Path(__file__).resolve().parents[1]))

    @app.post("/api/messages/{message_id}/feedback")
    def save_feedback(message_id: UUID, body: FeedbackInput, user=Depends(current_user)):
        with connect(settings) as db:
            message=db.execute("SELECT m.*,r.id run_identity FROM messages m JOIN runs r ON r.id=m.run_id WHERE m.id=%s AND m.owner_id=%s AND m.role='assistant'",(message_id,user['id'])).fetchone()
            if not message:
                raise HTTPException(404,"assistant_message_not_found")
            if body.artifact_version_id:
                artifact=db.execute("SELECT id FROM file_versions WHERE id=%s AND owner_id=%s AND project_id=%s AND source_run_id=%s",(body.artifact_version_id,user['id'],message['project_id'],message['run_identity'])).fetchone()
                if not artifact:
                    raise HTTPException(404,"artifact_version_not_found")
            return db.execute("INSERT INTO feedback(id,owner_id,project_id,message_id,run_id,artifact_version_id,rating,note) VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(owner_id,message_id,artifact_version_id) DO UPDATE SET rating=excluded.rating,note=excluded.note,updated_at=now() RETURNING id,message_id,run_id,artifact_version_id,rating,note,created_at,updated_at",(uuid4(),user['id'],message['project_id'],message_id,message['run_identity'],body.artifact_version_id,body.rating,body.note)).fetchone()

    @app.post("/api/runs/{run_id}/cancel")
    def cancel_run(run_id: UUID, user=Depends(current_user)):
        with connect(settings) as db:
            run = db.execute("SELECT * FROM runs WHERE id=%s AND owner_id=%s FOR UPDATE", (run_id, user["id"])).fetchone()
            if not run:
                raise HTTPException(404, "run_not_found")
            if run["status"] == "cancelled":
                return {"id": run_id, "status": "cancelled", "deduplicated": True}
            if run["status"] in {"completed", "failed"}:
                raise HTTPException(409, "run_already_terminal")
            target = "cancelling" if run["status"] == "running" else "cancelled"
            db.execute("UPDATE runs SET status=%s,completed_at=CASE WHEN %s='cancelled' THEN now() ELSE completed_at END WHERE id=%s", (target, target, run_id))
            if target == "cancelled":
                db.execute("UPDATE jobs SET status='cancelled',lease_token=NULL,lease_until=NULL WHERE run_id=%s AND status='queued'", (run_id,))
            append_event(db, run_id, "cancel_requested" if target == "cancelling" else "cancelled", {})
            return {"id": run_id, "status": target, "deduplicated": False}

    @app.get("/api/runs/{run_id}/events")
    def run_events(run_id: UUID, request: Request, after: int = 0, user=Depends(current_user)):
        with connect(settings) as db:
            owned = db.execute("SELECT id FROM runs WHERE id=%s AND owner_id=%s", (run_id, user["id"])).fetchone()
        if not owned:
            raise HTTPException(404, "run_not_found")
        header = request.headers.get("last-event-id")
        cursor = max(after, int(header) if header and header.isdigit() else 0)
        if "text/event-stream" not in request.headers.get("accept", ""):
            with connect(settings) as db:
                return db.execute("SELECT seq,event_type,payload,created_at FROM run_events WHERE run_id=%s AND seq>%s ORDER BY seq LIMIT 200", (run_id, cursor)).fetchall()

        def stream():
            last, idle = cursor, 0
            while idle < 25:
                with connect(settings) as db:
                    events = db.execute("SELECT seq,event_type,payload,created_at FROM run_events WHERE run_id=%s AND seq>%s ORDER BY seq LIMIT 200", (run_id, last)).fetchall()
                    status = db.execute("SELECT status FROM runs WHERE id=%s", (run_id,)).fetchone()["status"]
                if events:
                    idle = 0
                    for event in events:
                        last = event["seq"]
                        payload = json.dumps({"type": event["event_type"], "payload": event["payload"], "created_at": event["created_at"].isoformat()}, ensure_ascii=False)
                        yield f"id: {last}\nevent: {event['event_type']}\ndata: {payload}\n\n"
                else:
                    idle += 1
                    yield ": keep-alive\n\n"
                if status in {"completed", "failed", "cancelled"} and not events:
                    return
                time.sleep(1)
        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get('/api/skills')
    def skills(user=Depends(current_user)):
        return [{k:v for k,v in load_skill(identity).items() if k!='body'} for identity in AVAILABLE]

    @app.get('/api/models')
    def models(user=Depends(current_user)):
        return available_models() if settings.mode=='live' else [{'id':'default','name':'mock-conversation','purpose':'开发替身'}]

    @app.get('/api/chunks/{chunk_id}')
    def chunk(chunk_id: UUID,user=Depends(current_user)):
        with connect(settings) as db:
            row=db.execute("SELECT c.*,f.id file_id,f.display_name,f.kind,v.version,(f.current_version_id=v.id AND f.deleted_at IS NULL AND v.status='ready') current FROM document_chunks c JOIN file_versions v ON v.id=c.version_id JOIN files f ON f.id=v.file_id WHERE c.id=%s AND f.owner_id=%s",(chunk_id,user['id'])).fetchone()
            if not row:
                raise HTTPException(404,'chunk_not_found')
            return row

    app.include_router(file_router(settings, current_user))
    app.include_router(context_router(settings, current_user))
    static_dir = Path(__file__).parent / "web"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/")
    def index():
        return FileResponse(static_dir / "index.html")

    return app
