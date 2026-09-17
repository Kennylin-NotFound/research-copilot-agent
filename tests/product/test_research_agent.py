import unittest
from unittest.mock import patch
from unittest.mock import Mock
from dataclasses import replace
from types import SimpleNamespace
import json
from uuid import uuid4
import test_rag as base
from domain import AgentAction, ActionType
from product.worker import run_once
from product.db import connect
from product.rag import retrieve
from product.research_agent import ProductPolicy


class AgentTest(unittest.TestCase):
    setUpClass=classmethod(base.RagTest.setUpClass.__func__)
    setUp=base.RagTest.setUp
    tearDown=base.RagTest.tearDown
    conversation=base.RagTest.conversation
    upload=base.RagTest.upload
    indexed=base.RagTest.indexed

    def submit(self,conv,skill,file):
        response=self.client.post(f"/api/conversations/{conv['id']}/messages",json={'content':'Review the original method and its limitations.','client_message_id':str(uuid4()),'skill_id':skill,'file_ids':[file['id']]})
        self.assertEqual(response.status_code,202,response.text)
        return response.json()['run_id']

    def test_review_and_survey_reuse_v2_graph_and_publish_versioned_outputs(self):
        project,conv=self.conversation();file=self.indexed(project)
        for skill in ['paper-review','evidence-survey']:
            rid=self.submit(conv,skill,file);run_once(self.settings)
            result=self.client.get('/api/runs/'+rid).json()
            self.assertEqual(result['status'],'completed',result.get('error_code'))
            self.assertEqual(result['result_json']['graph_summary']['reads'],1)
            artifacts=result['result_json']['artifacts'];self.assertEqual(len(artifacts),3)
            self.assertTrue(result['context_snapshot']['graph']['decisions'])
            for artifact in artifacts:
                response=self.client.get(f"/api/files/{artifact['file_id']}/download")
                self.assertEqual(response.status_code,200)
                detail=self.client.get(f"/api/files/{artifact['file_id']}").json()
                self.assertEqual(detail['file']['kind'],'generated')
                self.assertEqual(detail['versions'][0]['source_run_id'],rid)
                self.assertTrue(detail['page']['text'])
            # Generated output never feeds itself back into original-source retrieval.
            rows,_=retrieve(self.settings,self.owner['id'],project['id'],'method')
            self.assertEqual({str(r['file_id']) for r in rows},{file['id']})
        self.client.patch(f"/api/projects/{project['id']}/state",json={'expected_revision':0,'dimensions':['changed requirement']})
        prior=self.client.get(f"/api/files/{artifacts[0]['file_id']}").json()
        self.assertTrue(prior['versions'][0]['stale'])
        rid=self.submit(conv,'evidence-survey',file);run_once(self.settings)
        new=self.client.get('/api/runs/'+rid).json()
        self.assertEqual(new['status'],'completed',new.get('error_code'))
        detail=self.client.get(f"/api/files/{artifacts[0]['file_id']}").json()
        self.assertEqual([v['version'] for v in detail['versions']],[2,1])
        self.assertFalse(detail['versions'][0]['stale'])
        self.assertTrue(detail['versions'][1]['stale'])

    def test_unavailable_tool_is_rejected_before_execution(self):
        project,conv=self.conversation();file=self.indexed(project);rid=self.submit(conv,'paper-review',file)
        action=AgentAction(action_type=ActionType.SEARCH,tool_name='search_papers',arguments={'query':'outside source scope'},reason_summary='Attempt forbidden search')
        with patch.object(ProductPolicy,'decide',return_value=action):run_once(self.settings)
        result=self.client.get('/api/runs/'+rid).json()
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['error_code'],'tool_not_allowed')
        self.assertNotIn('write_artifact',[s['name'] for s in result['spans']])

    def test_clarification_releases_worker_without_publishing_artifacts(self):
        project,conv=self.conversation();file=self.indexed(project);rid=self.submit(conv,'paper-review',file)
        action=AgentAction(action_type=ActionType.CLARIFY_BRIEF,reason_summary='Which specific research question should this review answer?')
        with patch.object(ProductPolicy,'decide',return_value=action):run_once(self.settings)
        result=self.client.get('/api/runs/'+rid).json()
        self.assertEqual(result['status'],'completed')
        self.assertTrue(result['result_json']['needs_input'])
        self.assertFalse(result['result_json'].get('artifacts'))
        self.assertEqual(result['context_snapshot']['graph']['status'],'awaiting_human')
        self.submit(conv,'paper-review',file)

    def test_ambiguous_multi_source_review_is_guarded_before_model_and_artifact(self):
        project,conv=self.conversation()
        first=self.indexed(project,'react-v3.txt')
        second=self.indexed(project,'reflexion-v4.txt',b'Reflection stores feedback for a later trial.')
        response=self.client.post(f"/api/conversations/{conv['id']}/messages",json={
            'content':'请评议这篇论文。','client_message_id':str(uuid4()),
            'skill_id':'paper-review','file_ids':[first['id'],second['id']]})
        self.assertEqual(response.status_code,202,response.text)
        with patch('product.research_agent.OpenAI',side_effect=AssertionError('ambiguity guard must run before model')):
            run_once(self.settings)
        result=self.client.get('/api/runs/'+response.json()['run_id']).json()
        self.assertEqual(result['status'],'completed',result.get('error_code'))
        self.assertTrue(result['result_json']['needs_input'])
        self.assertFalse(result['result_json'].get('artifacts'))
        self.assertEqual(result['context_snapshot']['graph']['status'],'awaiting_human')
        self.assertIn('react-v3.txt',self.client.get(f"/api/conversations/{conv['id']}/messages").json()[-1]['content'])

    def test_named_paper_review_narrows_multi_selection_to_one_source(self):
        project,conv=self.conversation()
        first=self.indexed(project,'react-v3.txt')
        second=self.indexed(project,'reflexion-v4.txt',b'Reflection stores feedback for a later trial.')
        response=self.client.post(f"/api/conversations/{conv['id']}/messages",json={
            'content':'请评议 ReAct 的方法。','client_message_id':str(uuid4()),
            'skill_id':'paper-review','file_ids':[first['id'],second['id']]})
        self.assertEqual(response.status_code,202,response.text)
        run_once(self.settings)
        result=self.client.get('/api/runs/'+response.json()['run_id']).json()
        self.assertEqual(result['status'],'completed',result.get('error_code'))
        self.assertFalse(result['result_json'].get('needs_input'))
        self.assertEqual(result['result_json']['graph_summary']['candidate_count'],1)
        self.assertEqual({ref['file_id'] for ref in result['result_json']['references']},{first['id']})

    def test_run_keeps_frozen_skill_and_project_snapshot(self):
        project,conv=self.conversation();file=self.indexed(project);rid=self.submit(conv,'paper-review',file)
        original=self.client.get('/api/runs/'+rid).json()['skill_snapshot']
        with patch('product.skills_registry.load_skill',side_effect=AssertionError('must use frozen Skill')):run_once(self.settings)
        result=self.client.get('/api/runs/'+rid).json()
        self.assertEqual(result['status'],'completed',result.get('error_code'))
        self.assertEqual(result['skill_snapshot'],original)
        self.assertEqual(result['project_snapshot']['revision'],0)

    def test_live_policy_retries_one_missing_function_call_then_accepts_valid_action(self):
        project,conv=self.conversation();file=self.indexed(project);rid=self.submit(conv,'paper-review',file)
        run=self.client.get('/api/runs/'+rid).json()
        candidate=SimpleNamespace(paper_id=file['id'],status=SimpleNamespace(value='candidate'),
                                  model_dump=lambda **kwargs:{'paper_id':file['id'],'title':'agent.txt','url':'https://sources.copilot.invalid/'+file['id'],'status':'candidate','abstract':None,'year':None,'discovered_by_query':None})
        state=SimpleNamespace(candidates=[candidate],selected_paper_ids=[],plan=SimpleNamespace(axes=['方法机制']),
                              progress=SimpleNamespace(model_dump=lambda **kwargs:{'reads':0,'searches':0,'iterations':0}))
        invalid=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[]),finish_reason='stop')],usage=None)
        arguments=json.dumps({'action_type':'deep_read','tool_name':'read_paper','target_axis':'方法机制',
                              'arguments':{'paper_id':file['id'],'paper_url':'https://sources.copilot.invalid/'+file['id']},
                              'reason_summary':'Read the selected source.','expected_evidence_gain':'high'})
        call=SimpleNamespace(function=SimpleNamespace(name='select_research_action',arguments=arguments))
        valid=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[call]),finish_reason='tool_calls')],usage=None)
        create=Mock(side_effect=[invalid,valid])
        client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        live=replace(self.settings,mode='live')
        with patch('product.research_agent.OpenAI',return_value=client):
            action=ProductPolicy(live,run,[{'role':'user','content':'Review the selected source.'}],None).decide(state)
        self.assertEqual(action.action_type,ActionType.DEEP_READ)
        self.assertEqual(create.call_count,2)
        with connect(self.settings) as db:
            spans=db.execute("SELECT status,error_code,metadata FROM trace_spans WHERE run_id=%s AND name='choose_action' ORDER BY started_at",(rid,)).fetchall()
        self.assertEqual([(s['status'],s['error_code']) for s in spans],[('error','invalid_decision_tool_call'),('ok',None)])
        self.assertEqual(spans[1]['metadata']['attempt'],2)
        context=spans[1]['metadata']['decision_context']
        self.assertEqual([row['paper_id'] for row in context['all_selected_sources']],[file['id']])
        self.assertEqual(context['already_read_source_ids'],[])

    def test_live_policy_falls_back_to_validated_unread_source_after_two_invalid_calls(self):
        project,conv=self.conversation();file=self.indexed(project);rid=self.submit(conv,'paper-review',file)
        run=self.client.get('/api/runs/'+rid).json()
        candidate=SimpleNamespace(paper_id=file['id'],url='https://sources.copilot.invalid/'+file['id'],
                                  status=SimpleNamespace(value='candidate'),
                                  model_dump=lambda **kwargs:{'paper_id':file['id'],'title':'agent.txt','url':'https://sources.copilot.invalid/'+file['id'],'status':'candidate','abstract':None,'year':None,'discovered_by_query':None})
        state=SimpleNamespace(candidates=[candidate],selected_paper_ids=[],plan=SimpleNamespace(axes=['方法机制']),
                              progress=SimpleNamespace(model_dump=lambda **kwargs:{'reads':0,'searches':0,'iterations':0}))
        invalid=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[]),finish_reason='stop')],usage=None)
        create=Mock(side_effect=[invalid,invalid])
        client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        live=replace(self.settings,mode='live')
        with patch('product.research_agent.OpenAI',return_value=client):
            action=ProductPolicy(live,run,[{'role':'user','content':'Review the selected source.'}],None).decide(state)
        self.assertEqual(action.action_type,ActionType.DEEP_READ)
        self.assertEqual(action.arguments['paper_id'],file['id'])
        self.assertIn('保守回退',action.reason_summary)
        with connect(self.settings) as db:
            spans=db.execute("SELECT status,error_code FROM trace_spans WHERE run_id=%s AND name='choose_action' ORDER BY started_at",(rid,)).fetchall()
        self.assertEqual([(s['status'],s['error_code']) for s in spans],[('error','invalid_decision_tool_call'),('error','invalid_decision_tool_call')])
