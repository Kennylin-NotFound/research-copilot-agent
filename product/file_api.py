"""Project file workspace routes. IDs and ownership, never client server paths."""
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from product.db import connect
from product.storage import blob_path, media_for, receive_blob, safe_name


class FolderInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str
    parent_id: UUID | None = None

    @field_validator('name')
    @classmethod
    def valid_name(cls, name):
        return safe_name(name)


class FileUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    display_name: str | None = None
    folder_id: UUID | None = None
    trashed: bool | None = None

    @field_validator('display_name')
    @classmethod
    def valid_name(cls, name):
        return safe_name(name)


class NoteInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str
    content: str = Field(min_length=1, max_length=200000)
    folder_id: UUID | None = None


def lock_project(db, identity, owner):
    row = db.execute('SELECT * FROM projects WHERE id=%s AND owner_id=%s FOR UPDATE', (identity, owner)).fetchone()
    if not row:
        raise HTTPException(404, 'project_not_found')
    return row


def ensure_folders(db, project_id, owner):
    for name, kind in [('sources', 'original'), ('notes', 'user_note'), ('outputs', 'generated')]:
        db.execute('INSERT INTO folders(id,project_id,owner_id,name,system_kind) VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING', (uuid4(), project_id, owner, name, kind))


def folder_owned(db, identity, project_id, owner):
    folder = db.execute('SELECT * FROM folders WHERE id=%s AND project_id=%s AND owner_id=%s', (identity, project_id, owner)).fetchone()
    if not folder:
        raise HTTPException(404, 'folder_not_found')
    return folder


def writable_folder(db, folder_id, project_id, owner):
    folder = folder_owned(db, folder_id, project_id, owner)
    for _ in range(10):
        if folder['system_kind'] == 'generated':
            raise HTTPException(409, 'outputs_are_agent_managed')
        if not folder['parent_id']:
            return
        folder = folder_owned(db, folder['parent_id'], project_id, owner)
    raise HTTPException(422, 'folder_depth_limit')


def file_owned(db, identity, owner):
    row = db.execute('SELECT f.*,v.media_type,v.content_sha256,v.size_bytes,v.version,v.status,v.error_code FROM files f JOIN file_versions v ON v.id=f.current_version_id WHERE f.id=%s AND f.owner_id=%s', (identity, owner)).fetchone()
    if not row:
        raise HTTPException(404, 'file_not_found')
    return row


def attach_blob(db, project_id, owner, name, kind, folder_id, blob, existing=None, expected=None):
    lock_project(db, project_id, owner)
    ensure_folders(db, project_id, owner)
    if not folder_id:
        folder_id = db.execute('SELECT id FROM folders WHERE project_id=%s AND system_kind=%s', (project_id, kind)).fetchone()['id']
    writable_folder(db, folder_id, project_id, owner)
    if existing:
        existing = file_owned(db, existing['id'], owner)
        if existing['deleted_at']:
            raise HTTPException(409, 'file_trashed')
        if existing['kind'] == 'generated':
            raise HTTPException(409, 'outputs_are_agent_managed')
        if existing['current_version_id'] != expected:
            raise HTTPException(409, 'file_version_conflict')
        identity, version = existing['id'], existing['version'] + 1
    else:
        count = db.execute("SELECT count(*) n FROM files WHERE project_id=%s AND deleted_at IS NULL AND kind<>'generated'", (project_id,)).fetchone()['n']
        if count >= 10:
            raise HTTPException(409, 'project_file_limit')
        identity, version = uuid4(), 1
        db.execute('INSERT INTO files(id,project_id,owner_id,folder_id,display_name,kind) VALUES(%s,%s,%s,%s,%s,%s)', (identity, project_id, owner, folder_id, name, kind))
    version_id = uuid4()
    # Blob is already durable; metadata and parse job either all commit or all roll back.
    db.execute('INSERT INTO file_versions(id,file_id,project_id,owner_id,version,content_sha256,storage_key,size_bytes,media_type) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)', (version_id, identity, project_id, owner, version, blob['content_sha256'], blob['storage_key'], blob['size_bytes'], media_for(name)))
    db.execute('UPDATE files SET current_version_id=%s,updated_at=now() WHERE id=%s', (version_id, identity))
    db.execute("INSERT INTO file_jobs(id,version_id,kind) VALUES(%s,%s,'parse')", (uuid4(), version_id))
    db.execute("UPDATE upload_attempts SET status='attached' WHERE id=%s", (blob['attempt_id'],))
    return file_owned(db, identity, owner)


def file_router(settings, current_user):
    router = APIRouter()

    @router.get('/api/projects/{project_id}/files')
    def tree(project_id: UUID, user=Depends(current_user)):
        with connect(settings) as db:
            lock_project(db, project_id, user['id'])
            ensure_folders(db, project_id, user['id'])
            folders = db.execute('SELECT id,parent_id,name,system_kind FROM folders WHERE project_id=%s AND owner_id=%s ORDER BY name', (project_id, user['id'])).fetchall()
            files = db.execute('SELECT f.id,f.folder_id,f.display_name,f.kind,f.current_version_id,f.deleted_at,v.version,v.content_sha256,v.size_bytes,v.status,v.error_code,v.media_type FROM files f JOIN file_versions v ON f.current_version_id=v.id WHERE f.project_id=%s AND f.owner_id=%s ORDER BY f.created_at', (project_id, user['id'])).fetchall()
        return {'folders': folders, 'files': files, 'limits': {'max_bytes': 20971520, 'max_pages': 100, 'max_files': 10}}

    @router.post('/api/projects/{project_id}/folders', status_code=201)
    def create_folder(project_id: UUID, body: FolderInput, user=Depends(current_user)):
        with connect(settings) as db:
            lock_project(db, project_id, user['id'])
            ensure_folders(db, project_id, user['id'])
            if body.parent_id:
                writable_folder(db, body.parent_id, project_id, user['id'])
                cursor, depth = body.parent_id, 0
                while cursor:
                    depth += 1
                    cursor = folder_owned(db, cursor, project_id, user['id'])['parent_id']
                if depth >= 8:
                    raise HTTPException(422, 'folder_depth_limit')
            if db.execute('SELECT id FROM folders WHERE project_id=%s AND parent_id IS NOT DISTINCT FROM %s AND lower(name)=lower(%s)', (project_id, body.parent_id, body.name)).fetchone():
                raise HTTPException(409, 'folder_name_conflict')
            return db.execute('INSERT INTO folders(id,project_id,owner_id,parent_id,name) VALUES(%s,%s,%s,%s,%s) RETURNING id,parent_id,name', (uuid4(), project_id, user['id'], body.parent_id, body.name)).fetchone()

    @router.post('/api/projects/{project_id}/files', status_code=201)
    async def upload(project_id: UUID, request: Request, name: str, kind: Literal['original','user_note']='original', folder_id: UUID | None=None, user=Depends(current_user)):
        media_for(name)
        if kind == 'user_note' and Path(name).suffix.lower() == '.pdf':
            raise HTTPException(422, 'notes_require_text')
        with connect(settings) as db:
            lock_project(db, project_id, user['id'])
            if folder_id:
                writable_folder(db, folder_id, project_id, user['id'])
        blob = await receive_blob(settings, project_id, user['id'], request.stream())
        with connect(settings) as db:
            return attach_blob(db, project_id, user['id'], name, kind, folder_id, blob)

    @router.post('/api/projects/{project_id}/notes', status_code=201)
    async def note(project_id: UUID, body: NoteInput, user=Depends(current_user)):
        if media_for(body.name) == 'application/pdf':
            raise HTTPException(422, 'notes_require_text')
        with connect(settings) as db:
            lock_project(db, project_id, user['id'])
        async def chunks():
            yield body.content.encode('utf-8')
        blob = await receive_blob(settings, project_id, user['id'], chunks())
        with connect(settings) as db:
            return attach_blob(db, project_id, user['id'], body.name, 'user_note', body.folder_id, blob)

    @router.post('/api/files/{file_id}/versions', status_code=201)
    async def replace(file_id: UUID, request: Request, expected_version: UUID, user=Depends(current_user)):
        with connect(settings) as db:
            original = file_owned(db, file_id, user['id'])
            if original['kind'] == 'generated' or original['deleted_at']:
                raise HTTPException(409, 'file_not_editable')
            if original['current_version_id'] != expected_version:
                raise HTTPException(409, 'file_version_conflict')
        blob = await receive_blob(settings, original['project_id'], user['id'], request.stream())
        with connect(settings) as db:
            return attach_blob(db, original['project_id'], user['id'], original['display_name'], original['kind'], original['folder_id'], blob, original, expected_version)

    @router.patch('/api/files/{file_id}')
    def update(file_id: UUID, body: FileUpdate, user=Depends(current_user)):
        with connect(settings) as db:
            row = file_owned(db, file_id, user['id'])
            lock_project(db, row['project_id'], user['id'])
            row = file_owned(db, file_id, user['id'])
            if body.display_name and Path(body.display_name).suffix.lower() != Path(row['display_name']).suffix.lower():
                raise HTTPException(422, 'file_extension_must_match')
            if body.folder_id:
                writable_folder(db, body.folder_id, row['project_id'], user['id'])
            if body.trashed is False and row['deleted_at']:
                count = db.execute("SELECT count(*) n FROM files WHERE project_id=%s AND deleted_at IS NULL AND kind<>'generated'", (row['project_id'],)).fetchone()['n']
                if count >= 10:
                    raise HTTPException(409, 'project_file_limit')
            db.execute('UPDATE files SET display_name=%s,folder_id=%s,deleted_at=CASE WHEN %s::boolean IS NULL THEN deleted_at WHEN %s::boolean THEN now() ELSE NULL END,updated_at=now() WHERE id=%s AND owner_id=%s', (body.display_name or row['display_name'], body.folder_id or row['folder_id'], body.trashed, body.trashed, file_id, user['id']))
            return file_owned(db, file_id, user['id'])

    @router.get('/api/files/{file_id}')
    def detail(file_id: UUID, version_id: UUID | None=None, page: int=1, user=Depends(current_user)):
        if page < 1 or page > 100:
            raise HTTPException(422, 'invalid_page')
        with connect(settings) as db:
            row = file_owned(db, file_id, user['id'])
            versions = db.execute('SELECT id,version,content_sha256,size_bytes,status,parser_version,error_code,created_at FROM file_versions WHERE file_id=%s AND owner_id=%s ORDER BY version DESC', (file_id, user['id'])).fetchall()
            selected = version_id or row['current_version_id']
            if not any(v['id'] == selected for v in versions):
                raise HTTPException(404, 'file_version_not_found')
            page_row = db.execute('SELECT page,text FROM document_pages WHERE version_id=%s AND page=%s', (selected, page)).fetchone()
            count = db.execute('SELECT count(*) n FROM document_pages WHERE version_id=%s', (selected,)).fetchone()['n']
            chunks = db.execute('SELECT id,paragraph,char_start,char_end,text,content_sha256 FROM document_chunks WHERE version_id=%s AND page=%s ORDER BY char_start', (selected, page)).fetchall()
            jobs = db.execute('SELECT id,kind,status,attempt,error_code FROM file_jobs WHERE version_id=%s', (selected,)).fetchall()
        return {'file': row, 'versions': versions, 'selected_version_id': selected, 'page_count': count, 'page': page_row, 'chunks': chunks, 'jobs': jobs}

    @router.get('/api/files/{file_id}/download')
    def download(file_id: UUID, version_id: UUID | None=None, user=Depends(current_user)):
        with connect(settings) as db:
            row = file_owned(db, file_id, user['id'])
            version = db.execute('SELECT storage_key,media_type FROM file_versions WHERE file_id=%s AND id=%s AND owner_id=%s', (file_id, version_id or row['current_version_id'], user['id'])).fetchone()
            if not version:
                raise HTTPException(404, 'file_version_not_found')
        target = blob_path(settings, version['storage_key'])
        if not target.is_file():
            raise HTTPException(409, 'source_blob_missing')
        return FileResponse(target, media_type=version['media_type'], filename=row['display_name'])

    @router.post('/api/files/{file_id}/retry-parse')
    def retry_parse(file_id: UUID, user=Depends(current_user)):
        with connect(settings) as db:
            row = file_owned(db, file_id, user['id'])
            if row['deleted_at']:
                raise HTTPException(409, 'file_trashed')
            job = db.execute("UPDATE file_jobs SET status='queued',error_code=NULL WHERE version_id=%s AND kind='parse' AND status='failed' AND attempt<3 RETURNING id", (row['current_version_id'],)).fetchone()
            if not job:
                raise HTTPException(409, 'parse_not_retryable')
            db.execute("UPDATE file_versions SET status='uploaded',error_code=NULL WHERE id=%s", (row['current_version_id'],))
        return {'status': 'queued'}

    @router.post('/api/files/{file_id}/retry-index')
    def retry_index(file_id: UUID, user=Depends(current_user)):
        with connect(settings) as db:
            row=file_owned(db,file_id,user['id'])
            if row['deleted_at']:
                raise HTTPException(409,'file_trashed')
            job=db.execute("UPDATE file_jobs SET status='queued',error_code=NULL WHERE version_id=%s AND kind='index' AND status='failed' AND attempt<3 RETURNING id",(row['current_version_id'],)).fetchone()
            if not job:
                raise HTTPException(409,'index_not_retryable')
            db.execute("UPDATE file_versions SET status='pending_index',error_code=NULL WHERE id=%s",(row['current_version_id'],))
        return {'status':'queued'}

    return router
