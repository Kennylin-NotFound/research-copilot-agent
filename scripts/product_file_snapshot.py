"""Local-only file acceptance: public fixtures, API downloads, restart snapshot."""
from pathlib import Path
import hashlib
import json
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import fitz
import httpx
from psycopg.conninfo import conninfo_to_dict
from product.auth import issue_session, token_hash
from product.db import connect
from product.settings import Settings
from product.storage import blob_path, reconcile
from product.parser import PARSER_VERSION


def main():
    settings = Settings.load()
    cfg = conninfo_to_dict(settings.database_url)
    assert cfg['dbname'] == 'copilot_dev' and cfg['host'] == '127.0.0.1' and cfg['port'] == '15432'
    output = ROOT / 'artifacts/product/M2'
    output.mkdir(parents=True, exist_ok=True)
    with connect(settings) as db:
        owner = db.execute("SELECT id FROM users WHERE username='local_demo'").fetchone()['id']
        project = db.execute("SELECT id FROM projects WHERE owner_id=%s AND title=%s", (owner, 'Agent 产品化研究')).fetchone()['id']
        raw = issue_session(db, owner)
    try:
        with httpx.Client(base_url='http://127.0.0.1:18080',cookies={'copilot_session':raw},headers={'X-Copilot-Request':'1'},timeout=40) as client:
            if '--seed' in sys.argv:
                files = client.get(f'/api/projects/{project}/files').json()['files']
                with fitz.open(ROOT/'artifacts/product/M0/sources/react-v3.pdf') as pdf:
                    text = pdf[0].get_text(sort=False)
                fixtures = {'ReAct-original-page1.txt': text.encode('utf-8')}
                for name, content in fixtures.items():
                    if not any(f['display_name']==name for f in files):
                        response=client.post(f'/api/projects/{project}/files',params={'name':name},content=content)
                        response.raise_for_status()
                for file in files:
                    detail=client.get(f"/api/files/{file['id']}").json()
                    if detail['versions'][0]['parser_version']!=PARSER_VERSION:
                        original=client.get(f"/api/files/{file['id']}/download").content
                        response=client.post(f"/api/files/{file['id']}/versions",params={'expected_version':file['current_version_id']},content=original)
                        response.raise_for_status()
                print('Prepared local public TXT fixture and updated PDF parse-version input; wait for worker')
                return
            with connect(settings) as db:
                files=db.execute('SELECT id,folder_id,display_name,kind,current_version_id,deleted_at FROM files WHERE project_id=%s ORDER BY id',(project,)).fetchall()
                versions=db.execute('SELECT id,file_id,version,content_sha256,storage_key,size_bytes,status,parser_version FROM file_versions WHERE project_id=%s ORDER BY id',(project,)).fetchall()
                chunks=db.execute('SELECT c.* FROM document_chunks c JOIN file_versions v ON v.id=c.version_id WHERE v.project_id=%s ORDER BY c.id',(project,)).fetchall()
            assert len(files)>=3 and all(v['status']=='pending_index' for v in versions)
            downloads=[]
            for version in versions:
                response=client.get(f"/api/files/{version['file_id']}/download",params={'version_id':str(version['id'])})
                response.raise_for_status()
                assert hashlib.sha256(response.content).hexdigest()==version['content_sha256']
                assert hashlib.sha256(blob_path(settings,version['storage_key']).read_bytes()).hexdigest()==version['content_sha256']
                downloads.append({'version_id':str(version['id']),'http_download_matches_hash':True})
            snapshot=json.loads(json.dumps({'files':files,'versions':versions,'chunks':chunks},default=str,ensure_ascii=True))
            report=reconcile(settings)
            assert not report['missing_referenced_blobs']
            (output/'storage_reconciliation.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
            (output/'downloads_verified.json').write_text(json.dumps(downloads,indent=2),encoding='utf-8')
            path=output/'before_restart.json'
            if '--verify' in sys.argv:
                assert snapshot==json.loads(path.read_text(encoding='utf-8'))
                (output/'restart_verified.json').write_text(json.dumps({'passed':True,'files':len(files),'versions':len(versions),'chunks':len(chunks),'all_blob_hashes_match':True},indent=2),encoding='utf-8')
                print('PASS: files, versions, original chunks and every downloaded blob unchanged across actual process restart')
            else:
                if path.exists():
                    raise SystemExit('Preserve existing pre-restart evidence; use --verify')
                path.write_text(json.dumps(snapshot,ensure_ascii=False,indent=2),encoding='utf-8')
                print(f'Captured {len(files)} files, {len(versions)} versions, {len(chunks)} original chunks; download hashes match')
    finally:
        with connect(settings) as db:
            db.execute('DELETE FROM sessions WHERE token_hash=%s',(token_hash(raw),))


if __name__=='__main__':
    main()
