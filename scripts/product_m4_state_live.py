"""Exercise an explicit natural-language StatePatch and artifact invalidation."""
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
from scripts.product_m4_live import wait_run

DIMENSIONS=['方法机制','实验依据','失败恢复','成本']
QUESTION='请把项目研究维度修改为：方法机制、实验依据、失败恢复、成本。随后比较 ReAct 与 Reflexion，并基于所选原文重新生成证据综述；缺少证据的维度请明确说明。'


def main():
    settings=Settings.load();cfg=conninfo_to_dict(settings.database_url)
    assert cfg['dbname']=='copilot_dev' and cfg['host']=='127.0.0.1' and cfg['port']=='15432' and settings.mode=='live'
    out=ROOT/'artifacts/product/M4/state-revision-live.json'
    with connect(settings) as db:
        owner=db.execute("SELECT id FROM users WHERE username='local_demo'").fetchone()['id']
        project=db.execute("SELECT id FROM projects WHERE owner_id=%s AND title='Agent 产品化研究'",(owner,)).fetchone()['id']
        raw=issue_session(db,owner)
    try:
        with httpx.Client(base_url='http://127.0.0.1:18080',cookies={'copilot_session':raw},headers={'X-Copilot-Request':'1'},timeout=60) as client:
            before=client.get(f'/api/projects/{project}/context').json()
            files=client.get(f'/api/projects/{project}/files').json()['files']
            selected=[next(f for f in files if f['display_name']==name and not f['deleted_at']) for name in ('react-v3.pdf','reflexion-v4.pdf')]
            conversations=client.get(f'/api/projects/{project}/conversations').json()
            conversation=next(c for c in conversations if c['title']=='三个 Skills · M4 验收')
            identity=str(uuid5(NAMESPACE_URL,f'research-copilot-M4-state-{before["revision"]}'))
            response=client.post(f"/api/conversations/{conversation['id']}/messages",json={
                'content':QUESTION,'client_message_id':identity,'skill_id':'evidence-survey','file_ids':[f['id'] for f in selected]})
            response.raise_for_status();run=wait_run(client,response.json()['run_id'],300)
            after=client.get(f'/api/projects/{project}/context').json()
            result=run.get('result_json') or {}
            state_span=next((s for s in run['spans'] if s['name']=='apply_state_patch'),None)
            intent_span=next((s for s in run['spans'] if s['name']=='classify_request_intent'),None)
            generated=[]
            for artifact in result.get('artifacts',[]):
                detail=client.get(f"/api/files/{artifact['file_id']}").json()
                generated.append({'name':artifact['name'],'file_id':artifact['file_id'],
                                  'versions':[{'id':v['id'],'version':v['version'],'stale':v['stale'],'project_revision':v['provenance'].get('project_revision')} for v in detail['versions']]})
            checks={
                'completed':run['status']=='completed',
                'revision_incremented':after['revision']==before['revision']+1==run['project_revision'],
                'dimensions_replaced':after['state']['dimensions']==DIMENSIONS==run['project_snapshot']['state']['dimensions'],
                'natural_language_history':after['history'][0]['change'].get('source')=='natural_language',
                'state_span_recorded':bool(state_span and state_span['status']=='ok' and state_span['metadata'].get('changed_fields')==['dimensions']),
                'intent_function_call_recorded':bool(intent_span and intent_span['status']=='ok' and intent_span['metadata'].get('intent',{}).get('kind')=='revise'),
                'sources_preserved':all(f['status']=='ready' for f in selected),
                'new_artifacts_published':len(generated)==3 and all(v['versions'] and not v['versions'][0]['stale'] for v in generated),
                'prior_artifacts_stale':len(generated)==3 and all(any(v['stale'] for v in item['versions'][1:]) for item in generated),
            }
            payload={'checked_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'question':QUESTION,
                     'before_revision':before['revision'],'after_revision':after['revision'],'run_id':run['id'],
                     'checks':checks,'passed':all(checks.values()),'state_span':state_span,
                     'intent_span':intent_span,'artifacts':generated,'result':result}
            out.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps({'run_id':run['id'],'status':run['status'],'before_revision':before['revision'],'after_revision':after['revision'],'checks':checks,'passed':payload['passed']},ensure_ascii=False))
            if not payload['passed']:raise SystemExit(1)
    finally:
        with connect(settings) as db:db.execute('DELETE FROM sessions WHERE token_hash=%s',(token_hash(raw),))


if __name__=='__main__':main()
