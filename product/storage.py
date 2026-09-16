"""Immutable private blobs. Client names never become server paths."""
import hashlib
import os
from pathlib import Path
import re
from uuid import uuid4

from fastapi import HTTPException
from product.db import connect

MAX_BYTES = 20 * 1024 * 1024
MIME = {'.pdf': 'application/pdf', '.txt': 'text/plain', '.md': 'text/markdown'}


def safe_name(name):
    if not name or len(name) > 120 or name != name.strip() or name in {'.', '..'} or re.search(r'[\\/\x00-\x1f\x7f:]', name):
        raise HTTPException(422, 'invalid_file_name')
    return name


def media_for(name):
    safe_name(name)
    if Path(name).suffix.lower() not in MIME:
        raise HTTPException(415, 'unsupported_file_type')
    return MIME[Path(name).suffix.lower()]


def blob_path(settings, key):
    if not re.fullmatch(r'[a-f0-9]{32}\.blob', key):
        raise ValueError('Invalid internal storage key')
    root = settings.data_dir.resolve()
    target = (root / 'blobs' / key).resolve()
    if root not in target.parents:
        raise ValueError('Storage boundary violation')
    return target


async def receive_blob(settings, project_id, owner_id, stream):
    """Read a bounded raw body; failed DB attachment remains reconcilable."""
    identity = uuid4()
    key = identity.hex + '.blob'
    target = blob_path(settings, key)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix('.part')
    with connect(settings) as db:
        db.execute("INSERT INTO upload_attempts(id,project_id,owner_id,storage_key,status) VALUES(%s,%s,%s,%s,'uploading')", (identity, project_id, owner_id, key))
    size, digest = 0, hashlib.sha256()
    try:
        with temporary.open('xb') as output:
            async for chunk in stream:
                size += len(chunk)
                if size > MAX_BYTES:
                    raise HTTPException(413, 'file_too_large')
                digest.update(chunk)
                output.write(chunk)
            if not size:
                raise HTTPException(422, 'empty_file')
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(target)
        with connect(settings) as db:
            db.execute("UPDATE upload_attempts SET status='blob_written',size_bytes=%s WHERE id=%s", (size, identity))
    except BaseException as error:
        # Only this request's newly-created partial file; no existing user blob is removed.
        temporary.unlink(missing_ok=True)
        with connect(settings) as db:
            db.execute("UPDATE upload_attempts SET status='failed',size_bytes=%s,error_code=%s WHERE id=%s", (size, error.detail if isinstance(error, HTTPException) else 'upload_interrupted', identity))
        raise
    return {'attempt_id': identity, 'storage_key': key, 'size_bytes': size, 'content_sha256': digest.hexdigest()}


def reconcile(settings):
    """Read-only inventory; abandoned entries are reported, never silently deleted."""
    with connect(settings) as db:
        versions = db.execute('SELECT id,storage_key FROM file_versions').fetchall()
        attempts = db.execute("SELECT id,storage_key,status,size_bytes,error_code FROM upload_attempts WHERE status<>'attached'").fetchall()
    referenced = {v['storage_key'] for v in versions}
    folder = settings.data_dir / 'blobs'
    return {'missing_referenced_blobs': [str(v['id']) for v in versions if not blob_path(settings, v['storage_key']).is_file()],
            'unattached_attempts': [{**a, 'id': str(a['id']), 'blob_exists': blob_path(settings, a['storage_key']).is_file()} for a in attempts],
            'orphan_blobs': [p.name for p in folder.glob('*.blob') if p.name not in referenced],
            'partial_uploads': [p.name for p in folder.glob('*.part')]}
