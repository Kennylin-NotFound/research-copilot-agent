"""Execute the predeclared M6 live set and three controlled model comparisons."""
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

EVAL_VERSION='2026-09-18.3-decision-fallback'


def wait_run(client,run_id,timeout=420):
    started=time.monotonic()
    while True:
        response=client.get(f'/api/runs/{run_id}');response.raise_for_status();run=response.json()
        if run['status'] not in {'queued','running','cancelling'}:
            return run
        if time.monotonic()-started>timeout:
            raise RuntimeError(f'Run timeout: {run_id}')
        time.sleep(1)


def assess(case,run,file_ids):
    result=run.get('result_json') or {};refs=result.get('references',[]);artifacts=result.get('artifacts',[])
    validation=[s for s in run.get('spans',[]) if s['name']=='validate_citations']
    checks={
        'completed':run['status']=='completed',
        'minimum_references':len(refs)>=case['min_references'],
        'source_scope':bool(refs) and {r['file_id'] for r in refs}.issubset(set(file_ids)),
        'artifact_contract':len(artifacts)==case['expected_artifacts'],
        'citation_validation':bool(validation) and validation[-1]['status']=='ok',
        'no_error':not run.get('error_code'),
    }
    return {'case_id':case['id'],'partition':case['partition'],'run_id':run['id'],'model':run['model'],'skill_id':run['skill_id'],
            'status':run['status'],'reference_count':len(refs),'artifact_count':len(artifacts),'usage':run.get('usage'),
            'duration_ms':next((s['duration_ms'] for s in run.get('spans',[]) if s['kind']=='run'),None),
            'checks':checks,'passed':all(checks.values())}


def main():
    settings=Settings.load();cfg=conninfo_to_dict(settings.database_url)
    assert cfg['dbname']=='copilot_dev' and cfg['host']=='127.0.0.1' and cfg['port']=='15432' and settings.mode=='live'
    spec=json.loads((ROOT/'evaluation/product/m6_tasks.json').read_text(encoding='utf-8'))
    out=ROOT/'artifacts/product/M6';out.mkdir(parents=True,exist_ok=True)
    with connect(settings) as db:
        owner=db.execute("SELECT id FROM users WHERE username='local_demo'").fetchone()['id']
        project=db.execute("SELECT id FROM projects WHERE owner_id=%s AND title='Agent 产品化研究'",(owner,)).fetchone()['id']
        raw=issue_session(db,owner)
    try:
        with httpx.Client(base_url='http://127.0.0.1:18080',cookies={'copilot_session':raw},headers={'X-Copilot-Request':'1'},timeout=60) as client:
            files=client.get(f'/api/projects/{project}/files').json()['files']
            file_map={f['display_name']:f for f in files if not f['deleted_at']}
            conversations=client.get(f'/api/projects/{project}/conversations').json()
            conversation=next((c for c in conversations if c['title']=='M6 固定质量评测'),None)
            if not conversation:
                response=client.post(f'/api/projects/{project}/conversations',json={'title':'M6 固定质量评测'});response.raise_for_status();conversation=response.json()
            results=[]
            for case in spec['tasks']:
                file_ids=[]
                for name in case['files']:
                    file=file_map[name];assert file['status']=='ready',(name,file['status']);file_ids.append(file['id'])
                identity=str(uuid5(NAMESPACE_URL,'research-copilot-M6-'+EVAL_VERSION+'-default-'+case['id']))
                response=client.post(f"/api/conversations/{conversation['id']}/messages",json={'content':case['question'],'client_message_id':identity,'skill_id':case['skill_id'],'file_ids':file_ids,'model_id':'default'})
                response.raise_for_status();run=wait_run(client,response.json()['run_id'])
                check=assess(case,run,file_ids);results.append(check)
                (out/(case['id']+'-default.json')).write_text(json.dumps({'case':case,'check':check,'run':run},ensure_ascii=False,indent=2),encoding='utf-8')
                print(json.dumps(check,ensure_ascii=False),flush=True)
            comparisons=[]
            for case in [item for item in spec['tasks'] if item.get('compare_models')]:
                file_ids=[file_map[name]['id'] for name in case['files']]
                identity=str(uuid5(NAMESPACE_URL,'research-copilot-M6-'+EVAL_VERSION+'-fast-'+case['id']))
                response=client.post(f"/api/conversations/{conversation['id']}/messages",json={'content':case['question'],'client_message_id':identity,'skill_id':case['skill_id'],'file_ids':file_ids,'model_id':'fast'})
                response.raise_for_status();run=wait_run(client,response.json()['run_id'])
                check=assess(case,run,file_ids);comparisons.append(check)
                (out/(case['id']+'-fast.json')).write_text(json.dumps({'case':case,'check':check,'run':run},ensure_ascii=False,indent=2),encoding='utf-8')
                print(json.dumps(check,ensure_ascii=False),flush=True)
            summary={'checked_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'mode':'live','eval_version':EVAL_VERSION,'declared_before_execution':spec['declared_before_execution'],'holdout_used_for_tuning':False,
                     'default_results':results,'model_comparisons':comparisons,'default_passed':all(r['passed'] for r in results),'comparisons_completed':len(comparisons)==3,
                     'no_superiority_claim':'Three paired cases are descriptive only; they do not establish statistical superiority.'}
            summary['passed']=summary['default_passed'] and summary['comparisons_completed']
            (out/'live_evaluation_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
            if not summary['passed']:
                raise SystemExit(1)
    finally:
        with connect(settings) as db:
            db.execute('DELETE FROM sessions WHERE token_hash=%s',(token_hash(raw),))


if __name__=='__main__':main()
