import asyncio
import hashlib
import io
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException
import fitz
import test_api as base
from product.db import connect
from product.file_worker import parse_once
from product.parser import extract
from product.storage import receive_blob, reconcile


class FilesTest(unittest.TestCase):
    setUpClass = classmethod(base.ApiTest.setUpClass.__func__)
    setUp = base.ApiTest.setUp
    tearDown = base.ApiTest.tearDown
    conversation = base.ApiTest.conversation

    def upload(self, project, name='source.txt', data=b'First paragraph.\n\nSecond paragraph.', **params):
        return self.client.post(f"/api/projects/{project['id']}/files", params={'name':name, **params}, content=data, headers={'Content-Type':'application/octet-stream'})

    def test_three_types_parse_locators_download_and_persistence(self):
        project, _ = self.conversation()
        with fitz.open() as document:
            document.new_page().insert_text((72,72), 'Page one original fact.')
            document.new_page().insert_text((72,72), 'Page two original result.')
            pdf = document.tobytes()
        for name, content in [('notes.md', b'# Heading\n\nOriginal Markdown evidence.\n'), ('input.txt', b'First paragraph.\n\nSecond paragraph.\n'), ('paper.pdf', pdf)]:
            response = self.upload(project, name, content)
            self.assertEqual(response.status_code, 201, response.text)
            file = response.json()
            self.assertEqual(file['status'], 'uploaded')
            self.assertTrue(parse_once(self.settings))
            detail = self.client.get(f"/api/files/{file['id']}").json()
            self.assertEqual(detail['file']['status'], 'pending_index')
            self.assertTrue(detail['chunks'])
            self.assertTrue(detail['chunks'][-1]['text'].rstrip().endswith(('evidence.', 'paragraph.', 'fact.')))
            for chunk in detail['chunks']:
                text = detail['page']['text'][chunk['char_start']:chunk['char_end']]
                self.assertEqual(text, chunk['text'])
                self.assertEqual(hashlib.sha256(text.encode()).hexdigest(), chunk['content_sha256'])
            self.assertEqual(self.client.get(f"/api/files/{file['id']}/download").content, content)
            if name.endswith('.pdf'):
                self.assertEqual(detail['page_count'], 2)
                second = self.client.get(f"/api/files/{file['id']}?page=2").json()
                self.assertIn('Page two original result', second['page']['text'])
        # A newly constructed application reads the same persistent data.
        with base.TestClient(base.create_app(self.settings), headers=base.HEADERS, client=('127.0.0.1', 50000)) as new_app:
            new_app.cookies.update(self.client.cookies)
            self.assertEqual(len(new_app.get(f"/api/projects/{project['id']}/files").json()['files']), 3)

    def test_rename_move_edit_trash_restore_keep_versions(self):
        project, _ = self.conversation()
        file = self.upload(project, 'note.md', b'Old note.', kind='user_note').json()
        parse_once(self.settings)
        folder = self.client.post(f"/api/projects/{project['id']}/folders", json={'name':'reading'}).json()
        endpoint = f"/api/files/{file['id']}"
        changed = self.client.patch(endpoint, json={'display_name':'renamed.md','folder_id':folder['id']})
        self.assertEqual(changed.status_code,200,changed.text)
        self.assertEqual(changed.json()['content_sha256'], file['content_sha256'])
        replacement = self.client.post(endpoint+'/versions', params={'expected_version':file['current_version_id']}, content=b'New note.')
        self.assertEqual(replacement.status_code, 201, replacement.text)
        self.assertEqual(replacement.json()['version'], 2)
        self.assertEqual(self.client.post(endpoint+'/versions', params={'expected_version':file['current_version_id']}, content=b'stale edit').status_code,409)
        self.assertEqual(self.client.get(endpoint+'/download',params={'version_id':file['current_version_id']}).content,b'Old note.')
        self.assertEqual(self.client.patch(endpoint,json={'trashed':True}).status_code,200)
        self.assertEqual(self.client.post(endpoint+'/versions',params={'expected_version':replacement.json()['current_version_id']},content=b'trashed').status_code,409)
        self.assertEqual(self.client.patch(endpoint,json={'trashed':False}).status_code,200)
        detail=self.client.get(endpoint).json()
        self.assertEqual(len(detail['versions']),2)
        self.assertEqual(detail['file']['folder_id'],folder['id'])

    def test_invalid_files_and_limits_do_not_become_ready(self):
        project, _ = self.conversation()
        self.assertEqual(self.upload(project, 'bad.html').status_code,415)
        self.assertEqual(self.upload(project, '../escape.txt').status_code,422)
        self.assertEqual(self.upload(project, 'C:\\secret.txt').status_code,422)
        self.assertEqual(self.upload(project, data=b'').status_code,422)
        for name, content in [('broken.pdf',b'not a pdf'), ('binary.txt',b'\x00\xff\x00')]:
            file = self.upload(project,name,content).json()
            parse_once(self.settings)
            detail = self.client.get(f"/api/files/{file['id']}").json()
            self.assertEqual(detail['file']['status'],'failed')
            self.assertEqual(detail['chunks'],[])
            self.assertIsNotNone(detail['file']['error_code'])

    def test_oversize_is_stopped_while_reading_and_interruption_is_recorded(self):
        project, _ = self.conversation()
        consumed=[]
        async def chunks():
            for i in range(10):
                consumed.append(i)
                yield b'x'*6
        with patch('product.storage.MAX_BYTES',10):
            with self.assertRaises(HTTPException):
                asyncio.run(receive_blob(self.settings, project['id'], self.owner['id'],chunks()))
        self.assertEqual(len(consumed),2)
        async def interrupted():
            yield b'partial'
            raise RuntimeError('simulated disconnect')
        with self.assertRaises(RuntimeError):
            asyncio.run(receive_blob(self.settings,project['id'],self.owner['id'],interrupted()))
        with connect(self.settings) as db:
            states = db.execute('SELECT status,error_code FROM upload_attempts ORDER BY created_at').fetchall()
        self.assertEqual([r['status'] for r in states],['failed','failed'])
        self.assertEqual(states[0]['error_code'],'file_too_large')
        self.assertEqual(states[1]['error_code'],'upload_interrupted')

    def test_transaction_failure_leaves_reconcilable_blob_without_file(self):
        project, _ = self.conversation()
        with connect(self.settings) as db:
            db.execute("CREATE OR REPLACE FUNCTION fail_file_job() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'injected'; END $$")
            db.execute('CREATE TRIGGER fail_file_job BEFORE INSERT ON file_jobs FOR EACH ROW EXECUTE FUNCTION fail_file_job()')
        try:
            with self.assertRaises(Exception):
                self.upload(project)
        finally:
            with connect(self.settings) as db:
                db.execute('DROP TRIGGER fail_file_job ON file_jobs')
                db.execute('DROP FUNCTION fail_file_job()')
        with connect(self.settings) as db:
            self.assertEqual(db.execute('SELECT count(*) n FROM files').fetchone()['n'],0)
            self.assertEqual(db.execute('SELECT count(*) n FROM file_versions').fetchone()['n'],0)
        report = reconcile(self.settings)
        self.assertEqual(report['missing_referenced_blobs'],[])
        self.assertEqual(len(report['unattached_attempts']),1)
        self.assertTrue(report['unattached_attempts'][0]['blob_exists'])

    def test_foreign_ids_and_other_project_folders_rejected(self):
        project, _ = self.conversation()
        file = self.upload(project).json()
        other, _ = self.conversation()
        folder = self.client.post(f"/api/projects/{other['id']}/folders",json={'name':'foreign'}).json()
        self.assertEqual(self.client.patch(f"/api/files/{file['id']}",json={'folder_id':folder['id']}).status_code,404)
        self.assertEqual(self.client.get(f"/api/files/{file['id']}?version_id={uuid4()}").status_code,404)
        with connect(self.settings) as db:
            owner=uuid4()
            db.execute('INSERT INTO users(id,username,password_hash) VALUES(%s,%s,%s)',(owner,'second_owner',base.password_hash.hash('OnlyTestsSecond!2026')))
        self.client.post('/api/logout')
        self.client.post('/api/login',json={'username':'second_owner','password':'OnlyTestsSecond!2026'})
        for path in [f"/api/files/{file['id']}",f"/api/files/{file['id']}/download",f"/api/projects/{project['id']}/files"]:
            self.assertEqual(self.client.get(path).status_code,404)
        self.assertEqual(self.client.patch(f"/api/files/{file['id']}",json={'trashed':True}).status_code,404)
        self.assertEqual(self.upload(project).status_code,404)

    def test_parser_timeout_and_expired_lease_are_bounded(self):
        project, _ = self.conversation()
        file = self.upload(project).json()
        parse_once(self.settings, parser=lambda *args:{'error_code':'parse_timeout'})
        self.assertEqual(self.client.get(f"/api/files/{file['id']}").json()['file']['error_code'],'parse_timeout')
        self.assertEqual(self.client.post(f"/api/files/{file['id']}/retry-parse").status_code,200)
        with connect(self.settings) as db:
            db.execute("UPDATE file_jobs SET status='running',lease_until=now()-interval '1 second',attempt=2")
        self.assertFalse(parse_once(self.settings))
        detail = self.client.get(f"/api/files/{file['id']}").json()
        self.assertEqual(detail['file']['status'],'failed')
        self.assertEqual(detail['file']['error_code'],'parser_interrupted')

    def test_page_and_project_file_limits(self):
        project, _ = self.conversation()
        with fitz.open() as pdf:
            for _ in range(101):
                pdf.new_page()
            content=pdf.tobytes()
        file=self.upload(project,'too-many.pdf',content).json()
        parse_once(self.settings)
        self.assertEqual(self.client.get(f"/api/files/{file['id']}").json()['file']['error_code'],'too_many_pages')
        for index in range(9):
            self.assertEqual(self.upload(project,f'{index}.txt').status_code,201)
        self.assertEqual(self.upload(project,'eleventh.txt').status_code,409)

    def test_reserved_folders_initialized_before_user_folder_creation(self):
        project, _ = self.conversation()
        result=self.client.post(f"/api/projects/{project['id']}/folders",json={'name':'sources'})
        self.assertEqual(result.status_code,409)
        self.assertEqual(self.upload(project).status_code,201)


if __name__ == '__main__':
    unittest.main()
