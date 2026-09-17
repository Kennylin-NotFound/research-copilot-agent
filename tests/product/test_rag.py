import unittest
from uuid import uuid4
from unittest.mock import Mock, patch

import test_files as files_base
from product.db import connect
from product.file_worker import parse_once
from product.index_worker import index_once
from product.embeddings import embed, validate_vector
from product.rag import (retrieve, validate_citations, GroundedAnswer, GroundedClaim,
                         Citation, run_rag, normalize_quote, canonical_source_quote,
                         SemanticAssessment, quote_looks_truncated, trim_retrieved_chunk_tail)
from product.worker import run_once


class RagTest(unittest.TestCase):
    setUpClass=classmethod(files_base.FilesTest.setUpClass.__func__)
    setUp=files_base.FilesTest.setUp
    tearDown=files_base.FilesTest.tearDown
    conversation=files_base.FilesTest.conversation
    upload=files_base.FilesTest.upload

    def test_quote_normalization_preserves_identifier_hyphen_but_joins_wrapped_word(self):
        self.assertEqual(normalize_quote('PaLM-\n540B'), 'PaLM540B')
        self.assertEqual(normalize_quote('PaLM-540B'), 'PaLM540B')
        self.assertEqual(normalize_quote('high-\nlevel'), 'highlevel')
        restored,repaired=canonical_source_quote(
            'competitive with chain-of-thought reasoning (CoT). The best approach',
            'competitive with chain-of-\nthought reasoning (CoT) (Wei et al., 2022). The best approach')
        self.assertTrue(repaired)
        self.assertIn('(Wei et al., 2022)',restored)
        self.assertEqual(canonical_source_quote('unsupported deletion here','different source'),(None,False))
        restored,repaired=canonical_source_quote('Generate reflection; Append to memory','Generate reflection\nAppend to memory')
        self.assertTrue(repaired);self.assertEqual(restored,'Generate reflection Append to memory')
        self.assertEqual(normalize_quote('ˆ\nA = A'),normalize_quote('ˆA = A'))
        restored,repaired=canonical_source_quote(
            'After each trial, reflection is appended to memory. In practice, memory is bounded by a maximum capacity.',
            'After each trial, reflection is appended to memory. In practice, memory is b')
        self.assertTrue(repaired);self.assertEqual(restored,'After each trial, reflection is appended to memory.')
        self.assertTrue(quote_looks_truncated('additional information into rea','full clause ends with additional information into rea'))
        self.assertTrue(quote_looks_truncated('perform arithmetic reasoning, guide','perform arithmetic reasoning, guide\n2Footnote'))
        self.assertFalse(quote_looks_truncated('create, maintain, and adjust high-level plans for acting','longer source with create, maintain, and adjust high-level plans for acting before more text'))
        self.assertEqual(trim_retrieved_chunk_tail('plans for acting, while incorporating data into rea'),'plans for acting')
        self.assertEqual(trim_retrieved_chunk_tail('arithmetic reasoning, guide\n2We find more examples.\n4'),'arithmetic reasoning')
        self.assertEqual(trim_retrieved_chunk_tail('complete claim without punctuation'),'complete claim without punctuation')

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
        self.assertEqual(calls[1]['allowed_verbatim_source'],'Agent actions collect observations. Reasoning updates the plan.')
        self.assertEqual(calls[1]['invalid_quote'],'Invented unsupported quotation')
        self.assertEqual(len(detail['context_snapshot']['model_candidates']),2)
        self.assertTrue(any(s['error_code']=='citation_quote_mismatch' for s in detail['spans']))
        self.assertEqual(detail['usage']['total_tokens'],40)

    def test_invalid_structured_output_gets_one_bounded_correction(self):
        project,conv=self.conversation();file=self.indexed(project);calls=[]
        def generator(question,evidence,skill,mode,model,feedback):
            calls.append(feedback)
            if len(calls)==1:
                GroundedAnswer.model_validate({'claims':'not-a-list','insufficient_evidence':False})
            return GroundedAnswer(claims=[GroundedClaim(statement='Agent actions collect observations.',citations=[Citation(chunk_id=evidence[0]['id'],quote='Agent actions collect observations.')])],insufficient_evidence=False),None
        run=self.client.post(f"/api/conversations/{conv['id']}/messages",json={'content':'What do actions do?','client_message_id':str(uuid4()),'skill_id':'evidence-qa','file_ids':[file['id']]}).json()
        with patch('product.worker.run_rag',side_effect=lambda s,r,m,root:run_rag(s,r,m,root,generator=generator)):
            run_once(self.settings)
        detail=self.client.get(f"/api/runs/{run['run_id']}").json()
        self.assertEqual(detail['status'],'completed',detail.get('error_code'))
        self.assertEqual(calls[1]['error_code'],'invalid_grounded_schema')
        self.assertEqual(len(detail['context_snapshot']['schema_failures']),1)
        self.assertTrue(any(s['name']==detail['model'] and s['status']=='error' and s['error_code']=='schema_error' for s in detail['spans']))

    def test_semantic_support_failure_gets_one_bounded_correction(self):
        project,conv=self.conversation();file=self.indexed(project)
        generator_feedback=[];judge_calls=[]
        def generator(question,evidence,skill,mode,model,feedback):
            generator_feedback.append(feedback)
            statement='Proven reliable for millions of production users.' if len(generator_feedback)==1 else 'Agent actions collect observations.'
            return GroundedAnswer(claims=[GroundedClaim(statement=statement,citations=[Citation(chunk_id=evidence[0]['id'],quote='Agent actions collect observations.')])],insufficient_evidence=False),None
        def judge(result,mode,model):
            judge_calls.append(result['claims'][0]['statement'])
            supported=len(judge_calls)>1
            return SemanticAssessment(claim_supported=[supported],limitations_scoped=True,limitations_consistent=True,reasons=[] if supported else ['Production reliability is absent from the quote.']),None
        run=self.client.post(f"/api/conversations/{conv['id']}/messages",json={'content':'What do actions do?','client_message_id':str(uuid4()),'skill_id':'evidence-qa','file_ids':[file['id']]}).json()
        with patch('product.worker.run_rag',side_effect=lambda s,r,m,root:run_rag(s,r,m,root,generator=generator,semantic_validator=judge)):
            run_once(self.settings)
        detail=self.client.get(f"/api/runs/{run['run_id']}").json()
        self.assertEqual(detail['status'],'completed',detail.get('error_code'))
        self.assertEqual(generator_feedback[1]['error_code'],'citation_semantic_mismatch')
        self.assertEqual(len(detail['context_snapshot']['semantic_assessments']),2)
        self.assertEqual(detail['result_json']['references'][0]['semantic_support'],'llm_checked')

    def test_semantic_support_twice_removes_only_unsupported_claim(self):
        project,conv=self.conversation();file=self.indexed(project);generator_calls=[]
        def generator(question,evidence,skill,mode,model,feedback):
            generator_calls.append(feedback);citation=Citation(chunk_id=evidence[0]['id'],quote='Agent actions collect observations.')
            return GroundedAnswer(claims=[
                GroundedClaim(statement='Proven reliable for millions of production users.',citations=[citation]),
                GroundedClaim(statement='Agent actions collect observations.',citations=[citation])],insufficient_evidence=False),None
        def judge(result,mode,model):
            return SemanticAssessment(claim_supported=[False,True],limitations_scoped=True,limitations_consistent=True,
                                      reasons=['Production scale is absent from the quote.']),None
        run=self.client.post(f"/api/conversations/{conv['id']}/messages",json={'content':'What do actions do?','client_message_id':str(uuid4()),'skill_id':'evidence-qa','file_ids':[file['id']]}).json()
        with patch('product.worker.run_rag',side_effect=lambda s,r,m,root:run_rag(s,r,m,root,generator=generator,semantic_validator=judge)):
            run_once(self.settings)
        detail=self.client.get(f"/api/runs/{run['run_id']}").json();result=detail['result_json']
        self.assertEqual(detail['status'],'completed',detail.get('error_code'))
        self.assertEqual(len(generator_calls),2)
        self.assertEqual([claim['statement'] for claim in result['claims']],['Agent actions collect observations.'])
        self.assertEqual(result['semantic_fallback']['removed_claim_indexes'],[0])
        self.assertEqual(len(result['references']),1)

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

    def test_second_citation_failure_removes_only_invalid_claim(self):
        project,conv=self.conversation();file=self.indexed(project);calls=[]
        def generator(question,evidence,skill,mode,model,feedback):
            calls.append(feedback)
            return GroundedAnswer(claims=[
                GroundedClaim(statement='Invented claim.',citations=[Citation(chunk_id=evidence[0]['id'],quote='Invented quotation')]),
                GroundedClaim(statement='Agent actions collect observations.',citations=[Citation(chunk_id=evidence[0]['id'],quote='Agent actions collect observations.')])],insufficient_evidence=False),None
        run=self.client.post(f"/api/conversations/{conv['id']}/messages",json={'content':'question','client_message_id':str(uuid4()),'skill_id':'evidence-qa'}).json()
        with patch('product.worker.run_rag',side_effect=lambda s,r,m,root:run_rag(s,r,m,root,generator=generator)):
            run_once(self.settings)
        detail=self.client.get(f"/api/runs/{run['run_id']}").json();result=detail['result_json']
        self.assertEqual(detail['status'],'completed',detail.get('error_code'))
        self.assertEqual(len(calls),2)
        self.assertEqual([c['statement'] for c in result['claims']],['Agent actions collect observations.'])
        self.assertEqual(result['citation_fallback']['removed_claim_indexes'],[0])

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
