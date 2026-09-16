"""Durable parsing jobs, isolated parser timeout, fenced atomic page publication."""
import json
import subprocess
import sys
from uuid import uuid4

from product.db import connect
from product.settings import ROOT
from product.storage import blob_path


def parse_blob(path, media):
    try:
        result = subprocess.run([sys.executable, '-m', 'product.parser', str(path), media], cwd=ROOT,
                                capture_output=True, timeout=30, check=False)
        if result.returncode:
            return {'error_code': 'parser_process_failed'}
        return json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        return {'error_code': 'parse_timeout'}
    except Exception:
        return {'error_code': 'parse_failed'}


def parse_once(settings, parser=parse_blob):
    with connect(settings) as db:
        # Parse has no external side effects; expired work can safely be attempted once more.
        expired = db.execute("SELECT * FROM file_jobs WHERE kind='parse' AND status='running' AND lease_until<now() FOR UPDATE SKIP LOCKED").fetchall()
        for row in expired:
            retry = row['attempt'] < 2
            db.execute("UPDATE file_jobs SET status=%s,error_code=%s WHERE id=%s", ('queued' if retry else 'failed', 'parser_interrupted', row['id']))
            db.execute("UPDATE file_versions SET status=%s,error_code=%s WHERE id=%s", ('uploaded' if retry else 'failed', 'parser_interrupted', row['version_id']))
        job = db.execute("SELECT j.*,v.storage_key,v.media_type FROM file_jobs j JOIN file_versions v ON v.id=j.version_id WHERE j.kind='parse' AND j.status='queued' ORDER BY j.created_at LIMIT 1 FOR UPDATE OF j SKIP LOCKED").fetchone()
        if not job:
            return False
        token = uuid4()
        db.execute("UPDATE file_jobs SET status='running',attempt=attempt+1,lease_token=%s,lease_until=now()+interval '90 seconds' WHERE id=%s", (token, job['id']))
        db.execute("UPDATE file_versions SET status='parsing',error_code=NULL WHERE id=%s", (job['version_id'],))
    result = parser(blob_path(settings, job['storage_key']), job['media_type'])
    with connect(settings) as db:
        active = db.execute("SELECT id FROM file_jobs WHERE id=%s AND lease_token=%s AND status='running' AND lease_until>now() FOR UPDATE", (job['id'], token)).fetchone()
        if not active:
            return True
        error = result.get('error_code')
        if not error:
            for page in result['pages']:
                db.execute('INSERT INTO document_pages(version_id,page,text) VALUES(%s,%s,%s)', (job['version_id'], page['page'], page['text']))
            for chunk in result['chunks']:
                db.execute('INSERT INTO document_chunks(id,version_id,page,paragraph,char_start,char_end,text,content_sha256) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)', (uuid4(), job['version_id'], chunk['page'], chunk['paragraph'], chunk['char_start'], chunk['char_end'], chunk['text'], chunk['content_sha256']))
            db.execute("UPDATE file_versions SET status='pending_index',parser_version=%s,error_code=NULL WHERE id=%s", (result['parser_version'], job['version_id']))
        else:
            db.execute("UPDATE file_versions SET status='failed',error_code=%s WHERE id=%s", (error, job['version_id']))
        db.execute("UPDATE file_jobs SET status=%s,error_code=%s,lease_until=NULL WHERE id=%s AND lease_token=%s", ('failed' if error else 'completed', error, job['id'], token))
    return True
