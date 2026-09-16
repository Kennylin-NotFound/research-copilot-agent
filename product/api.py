"""Same-origin web API. Every domain query includes the authenticated owner."""
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import FastAPI, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from psycopg.types.json import Jsonb

from product.settings import Settings
from product.db import connect, migrate
from product.auth import password_hash, DUMMY_HASH, token_hash, issue_session
from product.api_schemas import Credentials, Title, ConversationUpdate, SendMessage
from product.contracts import ProjectState
from product.llm import PROMPT_VERSION, model_name
from product.file_api import file_router


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
        return {"status": "ok", "stage": "M2", "mode": settings.mode}

    @app.get("/api/setup")
    def setup_status():
        with connect(settings) as db:
            empty = not db.execute("SELECT id FROM users LIMIT 1").fetchone()
        return {"required": empty, "local_only": True}

    @app.post("/api/setup", status_code=201)
    def setup(credentials: Credentials, request: Request, response: Response):
        if settings.cookie_secure or request.client.host not in {"127.0.0.1", "::1"}:
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
    def login(credentials: Credentials, response: Response):
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
            return db.execute("SELECT id,role,content,run_id,mode,created_at FROM messages WHERE conversation_id=%s AND owner_id=%s ORDER BY created_at,id", (conversation_id, user["id"])).fetchall()

    @app.post("/api/conversations/{conversation_id}/messages", status_code=202)
    def send_message(conversation_id: UUID, body: SendMessage, user=Depends(current_user)):
        with connect(settings) as db:
            conversation = conversation_owned(db, conversation_id, user)
            project = db.execute("SELECT * FROM projects WHERE id=%s AND owner_id=%s FOR UPDATE", (conversation["project_id"], user["id"])).fetchone()
            existing = db.execute("SELECT id,content,run_id FROM messages WHERE conversation_id=%s AND client_message_id=%s AND owner_id=%s", (conversation_id, body.client_message_id, user["id"])).fetchone()
            if existing:
                if existing["content"] != body.content:
                    raise HTTPException(409, "idempotency_content_conflict")
                return {"message_id": existing["id"], "run_id": existing["run_id"], "deduplicated": True}
            if conversation["archived"]:
                raise HTTPException(409, "conversation_archived")
            if db.execute("SELECT id FROM runs WHERE project_id=%s AND status IN ('queued','running','waiting_user','cancelling')", (project["id"],)).fetchone():
                raise HTTPException(409, "project_busy")
            message_id, run_id, trace_id = uuid4(), uuid4(), uuid4()
            db.execute("INSERT INTO messages(id,conversation_id,project_id,owner_id,role,content,client_message_id,mode) VALUES(%s,%s,%s,%s,'user',%s,%s,%s)", (message_id, conversation_id, project["id"], user["id"], body.content, body.client_message_id, settings.mode))
            db.execute("INSERT INTO runs(id,trace_id,conversation_id,project_id,owner_id,user_message_id,project_revision,status,mode,model,prompt_version) VALUES(%s,%s,%s,%s,%s,%s,%s,'queued',%s,%s,%s)", (run_id, trace_id, conversation_id, project["id"], user["id"], message_id, project["revision"], settings.mode, model_name(settings.mode), PROMPT_VERSION))
            db.execute("UPDATE messages SET run_id=%s WHERE id=%s", (run_id, message_id))
            db.execute("INSERT INTO jobs(id,run_id,status) VALUES(%s,%s,'queued')", (uuid4(), run_id))
            db.execute("UPDATE conversations SET updated_at=now() WHERE id=%s", (conversation_id,))
            return {"message_id": message_id, "run_id": run_id, "trace_id": trace_id, "deduplicated": False}

    @app.get("/api/conversations/{conversation_id}/runs")
    def runs(conversation_id: UUID, user=Depends(current_user)):
        with connect(settings) as db:
            conversation_owned(db, conversation_id, user)
            return db.execute("SELECT id,trace_id,status,mode,model,error_code,created_at FROM runs WHERE conversation_id=%s AND owner_id=%s ORDER BY created_at DESC,id LIMIT 30", (conversation_id, user["id"])).fetchall()

    @app.get("/api/runs/{run_id}")
    def run_detail(run_id: UUID, user=Depends(current_user)):
        with connect(settings) as db:
            row = db.execute("SELECT * FROM runs WHERE id=%s AND owner_id=%s", (run_id, user["id"])).fetchone()
            if not row:
                raise HTTPException(404, "run_not_found")
            row["spans"] = db.execute("SELECT * FROM trace_spans WHERE run_id=%s ORDER BY started_at, CASE WHEN parent_span_id IS NULL THEN 0 ELSE 1 END,id", (run_id,)).fetchall()
            return row

    app.include_router(file_router(settings, current_user))
    static_dir = Path(__file__).parent / "web"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/")
    def index():
        return FileResponse(static_dir / "index.html")

    return app
