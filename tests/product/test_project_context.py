import unittest
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import test_api as base
from product.db import connect
from product.worker import run_once
from product.llm import ModelAnswer
from product.request_intent import RequestIntent, may_request_revision
from unittest.mock import patch


class ContextTest(unittest.TestCase):
    setUpClass=classmethod(base.ApiTest.setUpClass.__func__)
    setUp=base.ApiTest.setUp
    tearDown=base.ApiTest.tearDown
    conversation=base.ApiTest.conversation
    send=base.ApiTest.send

    def test_explicit_natural_language_revision_updates_run_snapshot_before_execution(self):
        p,c=self.conversation()
        self.assertTrue(may_request_revision('请把研究维度修改为方法机制和成本'))
        self.assertFalse(may_request_revision('请按方法机制和成本两个维度回答'))
        response=self.client.post(f"/api/conversations/{c['id']}/messages",json={
            'content':'请把研究维度修改为方法机制和成本，然后继续回答。',
            'client_message_id':str(uuid4())})
        self.assertEqual(response.status_code,202,response.text)
        intent=RequestIntent(kind='revise',dimensions=['方法机制','成本'],reason_summary='用户明确要求持久修改研究维度。')
        with patch('product.worker.classify_message',return_value=(intent,None)):
            run_once(self.settings,responder=lambda messages,mode,model:ModelAnswer('已按新维度继续。',None))
        detail=self.client.get('/api/runs/'+response.json()['run_id']).json()
        self.assertEqual(detail['status'],'completed',detail.get('error_code'))
        self.assertEqual(detail['project_revision'],1)
        self.assertEqual(detail['project_snapshot']['state']['dimensions'],['方法机制','成本'])
        state_span=next(item for item in detail['spans'] if item['name']=='apply_state_patch')
        self.assertEqual(state_span['metadata']['changed_fields'],['dimensions'])
        context=self.client.get(f"/api/projects/{p['id']}/context").json()
        self.assertEqual(context['revision'],1)
        self.assertEqual(context['history'][0]['change']['source'],'natural_language')

    def test_concurrent_patch_only_one_wins_and_history_is_preserved(self):
        p,_=self.conversation()
        url=f"/api/projects/{p['id']}/state"
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses=list(pool.map(lambda objective:self.client.patch(url,json={'expected_revision':0,'objective':objective}).status_code,['alpha','beta']))
        self.assertEqual(sorted(statuses),[200,409])
        data=self.client.get(f"/api/projects/{p['id']}/context").json()
        self.assertEqual(data['revision'],1)
        self.assertEqual([h['revision'] for h in data['history']],[1,0])

    def test_memory_cross_conversation_revoke_and_snapshot_retention(self):
        p,c=self.conversation(); url=f"/api/projects/{p['id']}"
        data=self.client.post(url+'/memories',json={'expected_revision':0,'content':'Prefer concise bullet lists.'}).json()
        memory=data['memories'][0]
        first=self.send(c).json();run_once(self.settings)
        other=self.client.post(url+'/conversations',json={'title':'new conversation'}).json()
        observed=[]
        def capture(messages,mode,model):
            observed.extend(messages)
            return ModelAnswer('done',None)
        second=self.send(other).json();run_once(self.settings,responder=capture)
        self.assertIn('Prefer concise bullet lists.',observed[0]['content'])
        response=self.client.patch(url+'/memories/'+memory['id'],json={'expected_revision':1,'expected_version':1,'revoke':True})
        self.assertEqual(response.status_code,200,response.text)
        observed.clear();third=self.send(other).json();run_once(self.settings,responder=capture)
        self.assertNotIn('Prefer concise bullet lists.',observed[0]['content'])
        old=self.client.get('/api/runs/'+first['run_id']).json()
        current=self.client.get('/api/runs/'+third['run_id']).json()
        self.assertEqual(len(old['project_snapshot']['memories']),1)
        self.assertEqual(current['project_snapshot']['memories'],[])
        self.assertIn(memory['id'],current['project_snapshot']['revoked_memory_ids'])
        p2,_=self.conversation()
        self.assertEqual(self.client.get(f"/api/projects/{p2['id']}/context").json()['memories'],[])

    def test_memory_source_and_selected_files_must_belong_to_project(self):
        p,c=self.conversation();other,c2=self.conversation()
        msg=self.send(c2).json()['message_id']
        self.assertEqual(self.client.post(f"/api/projects/{p['id']}/memories",json={'expected_revision':0,'content':'fake source','source_message_id':msg}).status_code,404)
        self.assertEqual(self.client.patch(f"/api/projects/{p['id']}/state",json={'expected_revision':0,'selected_file_ids':[str(uuid4())]}).status_code,404)
        with connect(self.settings) as db:
            db.execute('INSERT INTO users(id,username,password_hash) VALUES(%s,%s,%s)',(uuid4(),'foreign','unused'))
            foreign=db.execute("SELECT id FROM users WHERE username='foreign'").fetchone()['id']
            project_id=uuid4()
            db.execute('INSERT INTO projects(id,owner_id,title) VALUES(%s,%s,%s)',(project_id,foreign,'private'))
        self.assertEqual(self.client.get(f'/api/projects/{project_id}/context').status_code,404)

    def test_revision_change_during_call_cannot_publish_old_answer(self):
        p,c=self.conversation();run=self.send(c).json()
        def change(messages,mode,model):
            response=self.client.patch(f"/api/projects/{p['id']}/state",json={'expected_revision':0,'dimensions':['new dimension']})
            self.assertEqual(response.status_code,200)
            return ModelAnswer('obsolete result',None)
        run_once(self.settings,responder=change)
        result=self.client.get('/api/runs/'+run['run_id']).json()
        self.assertEqual(result['error_code'],'project_changed_before_publication')
        self.assertEqual(len(self.client.get(f"/api/conversations/{c['id']}/messages").json()),1)

    def test_memory_edit_uses_version_and_cannot_restore_revoked_record(self):
        p,_=self.conversation();url=f"/api/projects/{p['id']}"
        m=self.client.post(url+'/memories',json={'expected_revision':0,'content':'old'}).json()['memories'][0]
        r=self.client.patch(url+'/memories/'+m['id'],json={'expected_revision':1,'expected_version':1,'content':'new'})
        self.assertEqual(r.json()['memories'][0]['version'],2)
        self.assertEqual(self.client.patch(url+'/memories/'+m['id'],json={'expected_revision':2,'expected_version':1,'revoke':True}).status_code,409)
        self.assertEqual(self.client.patch(url+'/memories/'+m['id'],json={'expected_revision':2,'expected_version':2,'revoke':True}).status_code,200)
        self.assertEqual(self.client.patch(url+'/memories/'+m['id'],json={'expected_revision':3,'expected_version':3,'content':'old'}).status_code,409)
