"""Real local API -> worker -> embedding/retrieval/model/citation acceptance."""
from pathlib import Path
import json
import sys
import time
from uuid import uuid5, NAMESPACE_URL
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import httpx
from psycopg.conninfo import conninfo_to_dict
from product.auth import issue_session,token_hash
from product.db import connect
from product.settings import Settings


def main():
    settings=Settings.load()
    cfg=conninfo_to_dict(settings.database_url)
    assert cfg['dbname']=='copilot_dev' and cfg['host']=='127.0.0.1' and cfg['port']=='15432'
    out=ROOT/'artifacts/product/M3'
    out.mkdir(parents=True,exist_ok=True)
    cases=json.loads((ROOT/'evaluation/product/m3_live_cases.json').read_text(encoding='utf-8'))['cases']
    with connect(settings) as db:
        owner=db.execute("SELECT id FROM users WHERE username='local_demo'").fetchone()['id']
        project=db.execute('SELECT id FROM projects WHERE owner_id=%s AND title=%s',(owner,'Agent 产品化研究')).fetchone()['id']
        raw=issue_session(db,owner)
    try:
        with httpx.Client(base_url='http://127.0.0.1:18080',cookies={'copilot_session':raw},headers={'X-Copilot-Request':'1'},timeout=40) as client:
            conversations=client.get(f'/api/projects/{project}/conversations').json()
            conversation=next((c for c in conversations if c['title']=='原文证据问答 · M3 验收'),None)
            if not conversation:
                response=client.post(f'/api/projects/{project}/conversations',json={'title':'原文证据问答 · M3 验收'})
                response.raise_for_status()
                conversation=response.json()
            files=client.get(f'/api/projects/{project}/files').json()['files']
            results=[]
            for case in cases:
                path=out/(case['id']+'.json')
                retry_suffix=''
                if path.exists():
                    saved=json.loads(path.read_text(encoding='utf-8'))
                    if saved['check']['structural_passed'] or '--retry-failed' not in sys.argv:
                        results.append(saved['check'])
                        continue
                    archived=out/(case['id']+'-failed-'+saved['check']['run_id']+'.json')
                    if not archived.exists():
                        path.rename(archived)
                    retry_suffix='-retry-'+saved['check']['run_id']
                file=next(f for f in files if f['display_name']==case['file'])
                assert file['status']=='ready', f"Index not ready: {case['id']}"
                identity=str(uuid5(NAMESPACE_URL,'research-copilot-M3-'+case['id']+retry_suffix))
                response=client.post(f"/api/conversations/{conversation['id']}/messages",json={'content':case['question'],'client_message_id':identity,'skill_id':'evidence-qa','file_ids':[file['id']]})
                response.raise_for_status()
                run_id=response.json()['run_id']
                print(f"Submitted {case['id']} run={run_id}",flush=True)
                start=time.monotonic()
                while True:
                    response=client.get(f'/api/runs/{run_id}')
                    response.raise_for_status()
                    run=response.json()
                    if run['status'] not in {'queued','running'}:
                        break
                    if time.monotonic()-start>150:
                        raise RuntimeError('Local live run exceeded verification timeout; inspect existing run before retrying')
                    time.sleep(1)
                result=run['result_json'] or {}
                refs=result.get('references',[])
                check={'case_id':case['id'],'run_id':run_id,'status':run['status'],'live':run['mode']=='live','expected_gap':case['expect_gap'],
                       'gap_matches':result.get('insufficient_evidence')==case['expect_gap'],
                       'source_scope_matches':all(r['file_id']==file['id'] and r['kind']==case['kind'] for r in refs),
                       'citation_count':len(refs),'claim_count':len(result.get('claims',[])),'semantic_review':'pending'}
                check['structural_passed']=run['status']=='completed' and check['live'] and check['gap_matches'] and check['source_scope_matches'] and (bool(refs) or case['expect_gap'])
                path.write_text(json.dumps({'case':case,'check':check,'run':run},ensure_ascii=False,indent=2),encoding='utf-8')
                results.append(check)
                print(json.dumps(check,ensure_ascii=True),flush=True)
            with connect(settings) as db:
                indexing=db.execute("SELECT f.display_name,v.id,v.status,v.embedding_model,v.embedding_dimension,j.metadata,j.error_code FROM files f JOIN file_versions v ON v.id=f.current_version_id JOIN file_jobs j ON j.version_id=v.id AND j.kind='index' WHERE f.project_id=%s",(project,)).fetchall()
            (out/'live_summary.json').write_text(json.dumps({'cases':results,'indexing':indexing,'semantic_review':'pending'},default=str,ensure_ascii=False,indent=2),encoding='utf-8')
            if not all(r['structural_passed'] for r in results):
                raise SystemExit(1)
    finally:
        with connect(settings) as db:
            db.execute('DELETE FROM sessions WHERE token_hash=%s',(token_hash(raw),))


if __name__=='__main__':
    main()
