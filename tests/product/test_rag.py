import unittest
from uuid import uuid4
from unittest.mock import Mock, patch

import test_files as files_base
from product.db import connect
from product.file_worker import parse_once
from product.index_worker import index_once
from product.embeddings import embed, validate_vector
from product.rag import retrieve, validate_citations, GroundedAnswer, GroundedClaim, Citation, run_rag
from product.worker import run_once


class RagTest(unittest.TestCase):
    setUpClass=classmethod(files_base.FilesTest.setUpClass.__func__)
    setUp=files_base.FilesTest.setUp
    tearDown=files_base.FilesTest.tearDown
    conversation=files_base.FilesTest.conversation
    upload=files_base.FilesTest.upload

    def indexed(self,project,name='agent.txt',text=b'Agent actions collect observations. Reasoning updates the plan.'):
        response=self.upload(project,name,text)
        self.assertEqual(response.status_code,201,response.text)
        file=response.json()
        parse_once(self.settings)
        index_once(self.settings)
        detail=self.client.get(f"/api/files/{file['id']}").json()
        self.assertEqual(detail['file']['status'],'ready',detail)
        return detail['file']

    def test_selected_files_owner_project_and_active_versions_filter_before_retrieval(self):
        project,_=self.conversation()
        a=self.indexed(project)
        b=self.indexed(project,'memory.txt',b'Memory stores feedback from previous attempts.')
        other,_=self.conversation()
        self.indexed(other,'other.txt',b'Private project facts about agent actions.')
        rows,_=retrieve(self.settings,self.owner['id'],project['id'],'agent actions',[a['id']])
        self.assertTrue(rows)
        self.assertEqual({str(r['file_id']) for r in rows},{a['id']})
        self.assertEqual(retrieve(self.settings,str(uuid4()),project['id'],'agent')[0],[])
        self.client.patch(f"/api/files/{a['id']}",json={'trashed':True})
        self.assertEqual(retrieve(self.settings,self.owner['id'],project['id'],'agent actions',[a['id']])[0],[])
        self.client.patch(f"/api/files/{a['id']}",json={'trashed':False})
        self.assertTrue(retrieve(self.settings,self.owner['id'],project['id'],'agent actions',[a['id']])[0])
        self.client.post(f"/api/files/{a['id']}/versions",params={'expected_version':a['current_version_id']},content=b'New source replaces old claims.')
        self.assertEqual(retrieve(self.settings,self.owner['id'],project['id'],'agent actions',[a['id']])[0],[])

    def test_rename_and_identical_content_reuse_embeddings(self):
        project,_=self.conversation()
        file=self.indexed(project)
        self.client.patch(f"/api/files/{file['id']}",json={'display_name':'renamed.txt'})
        fail=Mock(side_effect=AssertionError('unexpected embedding call'))
        self.assertFalse(index_once(self.settings,embedder=fail))
        content=b'Agent actions collect observations. Reasoning updates the plan.'
        self.client.post(f"/api/files/{file['id']}/versions",params={'expected_version':file['current_version_id']},content=content)
        parse_once(self.settings)
        self.assertTrue(index_once(self.settings,embedder=fail))
        self.assertEqual(self.client.get(f"/api/files/{file['id']}").json()['file']['status'],'ready')
        fail.assert_not_called()

    def test_embedding_failure_cannot_publish_partial_ready_and_retry_uses_cache(self):
        project,_=self.conversation()
        file=self.upload(project).json()
        parse_once(self.settings)
        index_once(self.settings,embedder=Mock(side_effect=TimeoutError('secret never persisted')))
        detail=self.client.get(f"/api/files/{file['id']}").json()
        self.assertEqual(detail['file']['status'],'failed')
        self.assertEqual(detail['file']['error_code'],'timeout')
        self.assertNotIn('secret',str(detail))
        self.assertEqual(retrieve(self.settings,self.owner['id'],project['id'],'paragraph')[0],[])
        self.assertEqual(self.client.post(f"/api/files/{file['id']}/retry-index").status_code,200)
        index_once(self.settings)
        self.assertEqual(self.client.get(f"/api/files/{file['id']}").json()['file']['status'],'ready')

    def test_empty_selection_skips_external_calls_and_answers_gap(self):
        project,conv=self.conversation()
        self.indexed(project)
        fail=Mock(side_effect=AssertionError('unexpected external request'))
        self.assertEqual(retrieve(self.settings,self.owner['id'],project['id'],'question',[],fail)[0],[])
        fail.assert_not_called()
        run=self.client.post(f"/api/conversations/{conv['id']}/messages",json={'content':'Can these papers guarantee exactly-once production execution?','client_message_id':str(uuid4()),'skill_id':'evidence-qa','file_ids':[]}).json()
        run_once(self.settings)
        detail=self.client.get(f"/api/runs/{run['run_id']}").json()
        self.assertEqual(detail['status'],'completed')
        self.assertTrue(detail['result_json']['insufficient_evidence'])
        self.assertEqual(detail['result_json']['references'],[])
        self.assertFalse(any(s['kind']=='model' for s in detail['spans']))

    def test_answer_links_skill_context_tool_model_validation_and_original_chunk(self):
        project,conv=self.conversation()
        file=self.indexed(project)
        payload={'content':'What do agent actions do?','client_message_id':str(uuid4()),'skill_id':'evidence-qa','file_ids':[file['id']]}
        response=self.client.post(f"/api/conversations/{conv['id']}/messages",json=payload)
        self.assertEqual(response.status_code,202,response.text)
        run_once(self.settings)
        detail=self.client.get(f"/api/runs/{response.json()['run_id']}").json()
        self.assertEqual(detail['status'],'completed',detail)
        self.assertEqual(detail['skill_snapshot']['skill_id'],'evidence-qa')
        self.assertEqual({s['kind'] for s in detail['spans']},{'run','tool','model','validation'})
        self.assertTrue(detail['context_snapshot']['evidence'])
        citation=detail['result_json']['references'][0]
        original=self.client.get(f"/api/chunks/{citation['chunk_id']}").json()
        self.assertIn(citation['quote'],original['text'])
        self.assertTrue(original['current'])
        self.client.patch(f"/api/files/{file['id']}",json={'trashed':True})
        self.assertFalse(self.client.get(f"/api/chunks/{citation['chunk_id']}").json()['current'])

    def test_unknown_and_fabricated_quotes_rejected_semantics_remain_separate(self):
        row={'id':uuid4(),'file_id':uuid4(),'version_id':uuid4(),'page':1,'paragraph':1,'text':'The experiment tested only ten examples.','display_name':'paper.txt','kind':'original','content_sha256':'a'*64}
        bad=GroundedAnswer(claims=[GroundedClaim(statement='anything',citations=[Citation(chunk_id=uuid4(),quote='fake quotation')])],insufficient_evidence=False)
        with self.assertRaisesRegex(ValueError,'citation_not_in_context'):
            validate_citations(bad,[row])
        bad.claims[0].citations[0].chunk_id=row['id']
        with self.assertRaisesRegex(ValueError,'citation_quote_mismatch'):
            validate_citations(bad,[row])
        bad.claims[0].citations[0].quote=row['text']
        bad.claims[0].statement='This is proven reliable for millions of production users.'
        checked=validate_citations(bad,[row])
        self.assertEqual(checked['references'][0]['semantic_support'],'not_independently_checked')
        # Deliberately unsupported claim is a semantic-evaluation case, not falsely claimed to be rejected by locator checks.

    def test_invalid_skill_and_foreign_selection_rejected_and_options_are_idempotent(self):
        project,conv=self.conversation()
        file=self.indexed(project)
        payload={'content':'question','client_message_id':str(uuid4()),'skill_id':'evidence-qa','file_ids':[file['id']]}
        path=f"/api/conversations/{conv['id']}/messages"
        self.assertEqual(self.client.post(path,json=payload|{'skill_id':'execute-shell'}).status_code,422)
        self.assertEqual(self.client.post(path,json=payload|{'file_ids':[str(uuid4())]}).status_code,404)
        self.assertEqual(self.client.post(path,json=payload).status_code,202)
        self.assertEqual(self.client.post(path,json=payload|{'file_ids':[]}).status_code,409)
        self.assertTrue(self.client.post(path,json=payload).json()['deduplicated'])

    def test_invalid_vectors_rejected(self):
        for vector in ([1.0], [0.0]*2048, [float('nan')]*2048):
            with self.assertRaises(ValueError):
                validate_vector(vector)

    def test_bad_citation_gets_one_bounded_correction_and_preserves_failed_candidate(self):
        project,conv=self.conversation()
        file=self.indexed(project)
        calls=[]
        def generator(question,evidence,skill,mode,model,feedback):
            calls.append(feedback)
            quote='Invented unsupported quotation' if len(calls)==1 else evidence[0]['text']
            return GroundedAnswer(claims=[GroundedClaim(statement='Agent actions collect observations.',citations=[Citation(chunk_id=evidence[0]['id'],quote=quote)])],insufficient_evidence=False),{'prompt_tokens':10,'completion_tokens':10,'total_tokens':20}
        run=self.client.post(f"/api/conversations/{conv['id']}/messages",json={'content':'What do actions do?','client_message_id':str(uuid4()),'skill_id':'evidence-qa','file_ids':[file['id']]}).json()
        with patch('product.worker.run_rag',side_effect=lambda s,r,m,root:run_rag(s,r,m,root,generator=generator)):
            run_once(self.settings)
        detail=self.client.get(f"/api/runs/{run['run_id']}").json()
        self.assertEqual(detail['status'],'completed')
        self.assertEqual(len(calls),2)
        self.assertEqual(calls[1]['error_code'],'citation_quote_mismatch')
        self.assertEqual(len(detail['context_snapshot']['model_candidates']),2)
        self.assertTrue(any(s['error_code']=='citation_quote_mismatch' for s in detail['spans']))
        self.assertEqual(detail['usage']['total_tokens'],40)

    def test_invalid_citations_twice_fail_without_publishing_answer(self):
        project,conv=self.conversation()
        file=self.indexed(project)
        calls=[]
        def generator(question,evidence,skill,mode,model,feedback):
            calls.append(feedback)
            return GroundedAnswer(claims=[GroundedClaim(statement='bad',citations=[Citation(chunk_id=evidence[0]['id'],quote='Invented quotation')])],insufficient_evidence=False),None
        run=self.client.post(f"/api/conversations/{conv['id']}/messages",json={'content':'question','client_message_id':str(uuid4()),'skill_id':'evidence-qa'}).json()
        with patch('product.worker.run_rag',side_effect=lambda s,r,m,root:run_rag(s,r,m,root,generator=generator)):
            run_once(self.settings)
        detail=self.client.get(f"/api/runs/{run['run_id']}").json()
        self.assertEqual(detail['status'],'failed')
        self.assertEqual(detail['error_code'],'citation_quote_mismatch')
        self.assertEqual(len(calls),2)
        self.assertEqual(len(self.client.get(f"/api/conversations/{conv['id']}/messages").json()),1)

    def test_source_replaced_during_model_call_cannot_publish_stale_answer(self):
        project,conv=self.conversation()
        file=self.indexed(project)
        def generator(question,evidence,skill,mode,model,feedback):
            self.client.patch(f"/api/files/{file['id']}",json={'trashed':True})
            return GroundedAnswer(claims=[GroundedClaim(statement='old',citations=[Citation(chunk_id=evidence[0]['id'],quote=evidence[0]['text'])])],insufficient_evidence=False),None
        run=self.client.post(f"/api/conversations/{conv['id']}/messages",json={'content':'question','client_message_id':str(uuid4()),'skill_id':'evidence-qa'}).json()
        with patch('product.worker.run_rag',side_effect=lambda s,r,m,root:run_rag(s,r,m,root,generator=generator)):
            run_once(self.settings)
        detail=self.client.get(f"/api/runs/{run['run_id']}").json()
        self.assertEqual(detail['status'],'failed')
        self.assertEqual(detail['error_code'],'source_changed_before_publication')
        self.assertIsNone(detail['final_message_id'])


if __name__=='__main__':
    unittest.main()
