"""Run bounded M4 live cases against localhost and preserve complete run evidence."""
from pathlib import Path
import json
import sys
import time
from uuid import NAMESPACE_URL, uuid5

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import httpx
from psycopg.conninfo import conninfo_to_dict
from product.auth import issue_session, token_hash
from product.db import connect
from product.settings import Settings
from product.skills_registry import load_skill
from product.rag import PROMPT_VERSION
from product.research_agent import PROMPT_VERSION as AGENT_PROMPT_VERSION


def wait_file(client,file_id,timeout=240):
    started=time.monotonic()
    while True:
        detail=client.get(f'/api/files/{file_id}').json()
        status=detail['file']['status']
        if status in {'ready','failed'}:
            return detail
        if time.monotonic()-started>timeout:
            raise RuntimeError('Index timeout; inspect the existing file job before retrying')
        time.sleep(1)


def wait_run(client,run_id,timeout=240):
    started=time.monotonic()
    while True:
        response=client.get(f'/api/runs/{run_id}');response.raise_for_status();run=response.json()
        if run['status'] not in {'queued','running','cancelling'}:
            return run
        if time.monotonic()-started>timeout:
            raise RuntimeError('Run timeout; inspect the existing run before retrying')
        time.sleep(1)


def main():
    settings=Settings.load();cfg=conninfo_to_dict(settings.database_url)
    assert cfg['dbname']=='copilot_dev' and cfg['host']=='127.0.0.1' and cfg['port']=='15432' and settings.mode=='live'
    out=ROOT/'artifacts/product/M4';out.mkdir(parents=True,exist_ok=True)
    cases=json.loads((ROOT/'evaluation/product/m4_live_cases.json').read_text(encoding='utf-8'))['cases']
    with connect(settings) as db:
        owner=db.execute("SELECT id FROM users WHERE username='local_demo'").fetchone()['id']
        project=db.execute("SELECT id FROM projects WHERE owner_id=%s AND title='Agent 产品化研究'",(owner,)).fetchone()['id']
        raw=issue_session(db,owner)
    try:
        with httpx.Client(base_url='http://127.0.0.1:18080',cookies={'copilot_session':raw},headers={'X-Copilot-Request':'1'},timeout=60) as client:
            files=client.get(f'/api/projects/{project}/files').json()['files']
            if not any(f['display_name']=='reflexion-v4.pdf' and not f['deleted_at'] for f in files):
                source=ROOT/'artifacts/product/M0/sources/reflexion-v4.pdf'
                response=client.post(f'/api/projects/{project}/files',params={'name':'reflexion-v4.pdf','kind':'original'},content=source.read_bytes())
                response.raise_for_status();added=response.json();print('Uploaded reflexion-v4.pdf',flush=True)
                detail=wait_file(client,added['id'])
                if detail['file']['status']!='ready':
                    raise RuntimeError('Reflexion indexing failed: '+str(detail['file']['error_code']))
                files=client.get(f'/api/projects/{project}/files').json()['files']
            conversations=client.get(f'/api/projects/{project}/conversations').json()
            conversation=next((c for c in conversations if c['title']=='三个 Skills · M4 验收'),None)
            if not conversation:
                response=client.post(f'/api/projects/{project}/conversations',json={'title':'三个 Skills · M4 验收'});response.raise_for_status();conversation=response.json()
            checks=[]
            for case in cases:
                target=out/(case['id']+'.json')
                retry_suffix=''
                if target.exists():
                    saved=json.loads(target.read_text(encoding='utf-8'))
                    expected=load_skill(case['skill_id'])['version']
                    context=saved['run'].get('context_snapshot') or {}
                    if (saved['check']['passed'] and saved['check']['skill_version']==expected
                            and saved['run']['prompt_version']==PROMPT_VERSION
                            and (not case.get('requires_graph',True)
                                 or context.get('agent_prompt_version')==AGENT_PROMPT_VERSION)):
                        checks.append(saved['check']);continue
                    archived=out/(case['id']+'-failed-'+saved['check']['run_id']+'.json')
                    if not archived.exists():
                        target.rename(archived)
                    retry_suffix='-retry-'+saved['check']['run_id']
                file_ids=[]
                for name in case['files']:
                    match=next(f for f in files if f['display_name']==name and not f['deleted_at'])
                    assert match['status']=='ready',(name,match['status'])
                    file_ids.append(match['id'])
                identity=str(uuid5(NAMESPACE_URL,'research-copilot-M4-'+case['id']+retry_suffix))
                response=client.post(f"/api/conversations/{conversation['id']}/messages",json={'content':case['question'],'client_message_id':identity,'skill_id':case['skill_id'],'file_ids':file_ids})
                response.raise_for_status();run=wait_run(client,response.json()['run_id'])
                result=run.get('result_json') or {};refs=result.get('references',[]);artifacts=result.get('artifacts',[])
                expected_artifacts=case.get('expected_artifacts',3)
                requires_graph=case.get('requires_graph',True)
                check={'case_id':case['id'],'run_id':run['id'],'status':run['status'],'skill_id':run['skill_id'],'skill_version':(run.get('skill_snapshot') or {}).get('version'),
                       'project_revision':run['project_revision'],'reference_count':len(refs),'artifact_count':len(artifacts),
                       'source_scope_matches':{r['file_id'] for r in refs}.issubset(set(file_ids)) and bool(refs),
                       'graph_recorded':bool((run.get('context_snapshot') or {}).get('graph'))}
                check['passed']=(run['status']=='completed' and check['source_scope_matches']
                                 and (check['graph_recorded'] if requires_graph else True)
                                 and len(artifacts)==expected_artifacts)
                target.write_text(json.dumps({'case':case,'check':check,'run':run},ensure_ascii=False,indent=2),encoding='utf-8')
                checks.append(check);print(json.dumps(check,ensure_ascii=False),flush=True)
            review_file=out/'semantic_review.json'
            semantic_review=(json.loads(review_file.read_text(encoding='utf-8'))
                             if review_file.exists() else {'status':'pending'})
            summary={'checked_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'mode':'live','cases':checks,'passed':all(c['passed'] for c in checks),'semantic_review':semantic_review}
            (out/'live_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
            if not summary['passed']:
                raise SystemExit(1)
    finally:
        with connect(settings) as db:
            db.execute('DELETE FROM sessions WHERE token_hash=%s',(token_hash(raw),))


if __name__=='__main__':main()
