"""Immutable generated outputs, transactionally attached with their source run."""
import csv
import hashlib
import io
import json
import os
from uuid import uuid4
from psycopg.types.json import Jsonb
from product.file_api import ensure_folders
from product.storage import blob_path


def publish_artifacts(db,settings,run,answer):
    if run['skill_id'] not in {'paper-review','evidence-survey'} or (answer.result or {}).get('needs_input'):
        return []
    if 'write_artifact' not in run['skill_snapshot']['tools']:
        raise ValueError('tool_not_allowed')
    ensure_folders(db,run['project_id'],run['owner_id'])
    folder=db.execute("SELECT id FROM folders WHERE project_id=%s AND system_kind='generated'",(run['project_id'],)).fetchone()['id']
    result=answer.result or {}
    citations='\n'.join(f"[{r['ordinal']}] {r['display_name']} page {r['page']} version {r['version_id']} chunk {r['chunk_id']}: {r['quote']}" for r in result.get('references',[]))
    markdown=f"# {run['skill_id']}\n\n{answer.content}\n\n## 来源\n{citations}\n\nRun: {run['id']}\nProject revision: {run['project_revision']}\n"
    output=io.StringIO(newline=''); writer=csv.writer(output);writer.writerow(['statement','citation_ordinals'])
    for claim in result.get('claims',[]):
        # Avoid spreadsheet formula execution when users open exported cells.
        statement=claim['statement'];statement="'"+statement if statement.lstrip().startswith(('=','+','-','@','\t','\r')) else statement
        writer.writerow([statement,','.join(map(str,claim['citations']))])
    provenance={'run_id':str(run['id']),'project_revision':run['project_revision'],'skill_version':run['skill_snapshot']['version'],'prompt_version':run['prompt_version'],'source_versions':sorted({r['version_id'] for r in result.get('references',[])})}
    outputs=[('md','text/markdown',markdown),('json','application/json',json.dumps({'provenance':provenance,'result':result},ensure_ascii=False,indent=2)),('csv','text/csv',output.getvalue())]
    published=[]
    for extension,media,content in outputs:
        name=f"{run['skill_id']}.{extension}"
        file=db.execute("SELECT * FROM files WHERE project_id=%s AND kind='generated' AND display_name=%s AND deleted_at IS NULL FOR UPDATE",(run['project_id'],name)).fetchone()
        if not file:
            file=db.execute("INSERT INTO files(id,project_id,owner_id,folder_id,display_name,kind) VALUES(%s,%s,%s,%s,%s,'generated') RETURNING *",(uuid4(),run['project_id'],run['owner_id'],folder,name)).fetchone()
        existing=db.execute('SELECT id FROM file_versions WHERE source_run_id=%s AND file_id=%s',(run['id'],file['id'])).fetchone()
        if existing:
            published.append({'file_id':str(file['id']),'version_id':str(existing['id']),'name':name});continue
        number=db.execute('SELECT COALESCE(max(version),0)+1 n FROM file_versions WHERE file_id=%s',(file['id'],)).fetchone()['n']
        identity=uuid4();key=identity.hex+'.blob';data=content.encode('utf-8');target=blob_path(settings,key);target.parent.mkdir(parents=True,exist_ok=True)
        # An interrupted transaction leaves a discoverable orphan, never a dangling version.
        with target.open('xb') as stream:
            stream.write(data);stream.flush();os.fsync(stream.fileno())
        db.execute("INSERT INTO file_versions(id,file_id,project_id,owner_id,version,content_sha256,storage_key,size_bytes,media_type,status,provenance,source_run_id) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,'published',%s,%s)",(identity,file['id'],run['project_id'],run['owner_id'],number,hashlib.sha256(data).hexdigest(),key,len(data),media,Jsonb(provenance),run['id']))
        db.execute('INSERT INTO document_pages(version_id,page,text) VALUES(%s,1,%s)',(identity,content))
        db.execute('UPDATE files SET current_version_id=%s,updated_at=now() WHERE id=%s',(identity,file['id']))
        published.append({'file_id':str(file['id']),'version_id':str(identity),'name':name})
    return published
