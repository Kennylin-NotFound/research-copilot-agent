"""Verify a live ambiguous paper-review asks one necessary question."""
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
from product.research_agent import PROMPT_VERSION as AGENT_PROMPT_VERSION


def main():
    settings=Settings.load();cfg=conninfo_to_dict(settings.database_url)
    assert cfg['dbname']=='copilot_dev' and cfg['host']=='127.0.0.1' and cfg['port']=='15432' and settings.mode=='live'
    target=ROOT/'artifacts/product/M4/clarification-live.json'
    retry=''
    if target.exists():
        saved=json.loads(target.read_text(encoding='utf-8'))
        if saved.get('passed') and (saved.get('run',{}).get('context_snapshot') or {}).get('agent_prompt_version')==AGENT_PROMPT_VERSION:
            print(json.dumps({k:saved[k] for k in ('run_id','assistant_question','checks','passed')},ensure_ascii=False));return
        archived=target.with_name('clarification-live-failed-'+saved['run_id']+'.json')
        if not archived.exists():target.rename(archived)
        retry='-'+saved['run_id']
    with connect(settings) as db:
        owner=db.execute("SELECT id FROM users WHERE username='local_demo'").fetchone()['id']
        project=db.execute("SELECT id,revision FROM projects WHERE owner_id=%s AND title='Agent 产品化研究'",(owner,)).fetchone()
        raw=issue_session(db,owner)
    try:
        with httpx.Client(base_url='http://127.0.0.1:18080',cookies={'copilot_session':raw},headers={'X-Copilot-Request':'1'},timeout=60) as client:
            files=client.get(f"/api/projects/{project['id']}/files").json()['files']
            selected=[next(f for f in files if f['display_name']==name and not f['deleted_at']) for name in ('react-v3.pdf','reflexion-v4.pdf')]
            conversations=client.get(f"/api/projects/{project['id']}/conversations").json()
            conversation=next(c for c in conversations if c['title']=='三个 Skills · M4 验收')
            identity=str(uuid5(NAMESPACE_URL,f"research-copilot-M4-clarify-{project['revision']}{retry}"))
            response=client.post(f"/api/conversations/{conversation['id']}/messages",json={
                'content':'请评议这篇论文。','client_message_id':identity,'skill_id':'paper-review','file_ids':[f['id'] for f in selected]})
            response.raise_for_status();run=wait_run(client,response.json()['run_id'],180)
            result=run.get('result_json') or {}
            graph=(run.get('context_snapshot') or {}).get('graph') or {}
            message=client.get(f"/api/conversations/{conversation['id']}/messages").json()
            assistant=next((m for m in reversed(message) if m.get('run_id')==run['id'] and m['role']=='assistant'),None)
            checks={'completed':run['status']=='completed','needs_input':result.get('needs_input') is True,
                    'no_artifacts':not result.get('artifacts'),'graph_waiting_human':graph.get('status')=='awaiting_human',
                    'single_question':bool(assistant and assistant['content'].strip().endswith(('?','？')) and len(assistant['content'])<=300)}
            payload={'checked_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'run_id':run['id'],
                     'question':'请评议这篇论文。','assistant_question':assistant['content'] if assistant else None,
                     'checks':checks,'passed':all(checks.values()),'run':run}
            target.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps({k:payload[k] for k in ('run_id','assistant_question','checks','passed')},ensure_ascii=False))
            if not payload['passed']:raise SystemExit(1)
    finally:
        with connect(settings) as db:db.execute('DELETE FROM sessions WHERE token_hash=%s',(token_hash(raw),))


if __name__=='__main__':main()
