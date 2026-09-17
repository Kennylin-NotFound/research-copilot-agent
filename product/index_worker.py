"""Resumable per-chunk indexing, project-scoped cache, no partial-ready publication."""
import json
import time
from uuid import uuid4
from psycopg.types.json import Jsonb

from product.db import connect
from product.embeddings import embed, embedding_model, DIMENSION, validate_vector
from product.llm import classify_error


def index_once(settings, embedder=embed):
    model=embedding_model(settings.mode)
    with connect(settings) as db:
        db.execute("INSERT INTO file_jobs(id,version_id,kind) SELECT gen_random_uuid(),v.id,'index' FROM file_versions v JOIN files f ON f.current_version_id=v.id WHERE v.status='pending_index' AND f.deleted_at IS NULL AND f.kind IN ('original','user_note') ON CONFLICT DO NOTHING")
        expired=db.execute("SELECT * FROM file_jobs WHERE kind='index' AND status='running' AND lease_until<now() FOR UPDATE SKIP LOCKED").fetchall()
        for row in expired:
            retry=row['attempt']<3
            db.execute('UPDATE file_jobs SET status=%s,error_code=%s WHERE id=%s',('queued' if retry else 'failed','index_interrupted',row['id']))
            if not retry:
                db.execute("UPDATE file_versions SET status='failed',error_code='index_interrupted' WHERE id=%s",(row['version_id'],))
        job=db.execute("SELECT j.*,v.project_id,v.owner_id FROM file_jobs j JOIN file_versions v ON j.version_id=v.id WHERE j.kind='index' AND j.status='queued' ORDER BY j.created_at LIMIT 1 FOR UPDATE OF j SKIP LOCKED").fetchone()
        if not job:
            return False
        token=uuid4()
        db.execute("UPDATE file_jobs SET status='running',attempt=attempt+1,lease_token=%s,lease_until=now()+interval '120 seconds',error_code=NULL WHERE id=%s",(token,job['id']))
        chunks=db.execute('SELECT id,text,content_sha256 FROM document_chunks WHERE version_id=%s ORDER BY page,char_start',(job['version_id'],)).fetchall()
    metadata={'model':model,'dimension':DIMENSION,'chunks':len(chunks),'cache_hits':0,'api_calls':0,'tokens':0,'mode':settings.mode}
    started=time.monotonic()
    try:
        if not chunks:
            raise ValueError('no_original_chunks')
        for offset in range(0,len(chunks),16):
            if time.monotonic()-started>300:
                raise TimeoutError('index_budget_exceeded')
            batch=chunks[offset:offset+16]
            with connect(settings) as db:
                active=db.execute("UPDATE file_jobs SET lease_until=now()+interval '120 seconds' WHERE id=%s AND lease_token=%s AND status='running' AND lease_until>now() RETURNING id",(job['id'],token)).fetchone()
                if not active:
                    return True
                current=db.execute('SELECT id FROM files WHERE current_version_id=%s AND deleted_at IS NULL',(job['version_id'],)).fetchone()
                if not current:
                    raise ValueError('source_version_inactive')
                cached=db.execute('SELECT text_sha256,embedding::text FROM embedding_cache WHERE project_id=%s AND owner_id=%s AND model=%s AND text_sha256=ANY(%s)',(job['project_id'],job['owner_id'],model,[c['content_sha256'] for c in batch])).fetchall()
            by_hash={r['text_sha256']:json.loads(r['embedding']) for r in cached}
            missing=list({c['content_sha256']:c for c in batch if c['content_sha256'] not in by_hash}.values())
            metadata['cache_hits']+=len(batch)-len(missing)
            if missing:
                vectors,usage=embedder([c['text'] for c in missing],settings.mode)
                if len(vectors)!=len(missing):
                    raise ValueError('incomplete_embedding_batch')
                by_hash.update({c['content_sha256']:validate_vector(v) for c,v in zip(missing,vectors)})
                metadata['api_calls']+=1
                metadata['tokens']+=usage.get('total_tokens',0)
            with connect(settings) as db:
                active=db.execute("SELECT id FROM file_jobs WHERE id=%s AND lease_token=%s AND status='running' AND lease_until>now() FOR UPDATE",(job['id'],token)).fetchone()
                if not active:
                    return True
                for chunk in batch:
                    vector=json.dumps(by_hash[chunk['content_sha256']])
                    db.execute('INSERT INTO embedding_cache(project_id,owner_id,text_sha256,model,dimension,embedding) VALUES(%s,%s,%s,%s,%s,%s::vector) ON CONFLICT DO NOTHING',(job['project_id'],job['owner_id'],chunk['content_sha256'],model,DIMENSION,vector))
                    db.execute('INSERT INTO chunk_embeddings(chunk_id,model,dimension,embedding) VALUES(%s,%s,%s,%s::vector) ON CONFLICT(chunk_id) DO UPDATE SET embedding=excluded.embedding,model=excluded.model,dimension=excluded.dimension',(chunk['id'],model,DIMENSION,vector))
                db.execute('UPDATE file_jobs SET metadata=%s WHERE id=%s',(Jsonb(metadata),job['id']))
        with connect(settings) as db:
            db.execute('SELECT id FROM projects WHERE id=%s FOR UPDATE',(job['project_id'],))
            active=db.execute("SELECT id FROM file_jobs WHERE id=%s AND lease_token=%s AND status='running' AND lease_until>now() FOR UPDATE",(job['id'],token)).fetchone()
            if not active:
                return True
            current=db.execute('SELECT id FROM files WHERE current_version_id=%s AND deleted_at IS NULL',(job['version_id'],)).fetchone()
            if not current:
                raise ValueError('source_version_inactive')
            db.execute("UPDATE file_versions SET status='ready',embedding_model=%s,embedding_dimension=%s,error_code=NULL WHERE id=%s",(model,DIMENSION,job['version_id']))
            db.execute("UPDATE file_jobs SET status='completed',lease_until=NULL,metadata=%s WHERE id=%s",(Jsonb(metadata),job['id']))
    except Exception as error:
        code=str(error) if isinstance(error,ValueError) and str(error) in {'source_version_inactive','no_original_chunks'} else classify_error(error)
        with connect(settings) as db:
            active=db.execute("UPDATE file_jobs SET status='failed',error_code=%s,lease_until=NULL,metadata=%s WHERE id=%s AND lease_token=%s AND status='running' RETURNING id",(code,Jsonb(metadata),job['id'],token)).fetchone()
            if active:
                db.execute("UPDATE file_versions SET status='failed',error_code=%s WHERE id=%s",(code,job['version_id']))
    return True
