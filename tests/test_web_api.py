"""New web API invariants: read-only files, scoped uploads, explicit intake and idempotent answers."""
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Event
from time import sleep
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from fastapi.testclient import TestClient
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from api_server import create_app
from wiki_workbench_fakes import WorkbenchLLM
from wiki_intake import IntakeService
from wiki_uploads import UploadStore
from wiki_store import approve_draft,list_pages
from document_loader import load_uploaded_document
from test_wiki_intake import FakeLLM,FakeEmbeddings,analysis,generation,TEXT
from test_wiki_automatic import id_generation

HEADERS={'X-Workbench-Request':'1'}


class FakeRun:
    def __init__(self,turns,gate=None):
        self.thread_id='private-thread';self.gate=gate;self.calls=[]
        self.state={'turn_count':0,'max_turns':turns,'interview_history':[],'evaluation_history':[]}
        self.question={'question':'请介绍你的项目？','technology':['Redis']}
    def resume(self,text,on_progress=None):
        self.calls.append(text)
        if self.gate:self.gate.wait(5)
        self.state['interview_history'].append({'question':self.question['question'],'answer':text})
        self.state['turn_count']+=1
        self.question={'question':'为什么采用缓存？','technology':['Redis']}


class WebApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();base=Path(self.tmp.name)
        self.service=IntakeService(base/'vault',base/'private');self.emb=FakeEmbeddings();self.runs=[]
        def factory(documents,turns,**kwargs):
            run=FakeRun(turns);self.runs.append(run);return run
        self.app=create_app(self.service,llm_factory=WorkbenchLLM,
                            embedding_factory=lambda:SimpleNamespace(embeddings=self.emb),interview_factory=factory)
        self.context=TestClient(self.app);self.client=self.context.__enter__();self.client.get('/api/bootstrap')
    def tearDown(self):
        self.context.__exit__(None,None,None);self.tmp.cleanup()
    def wait(self,id,status='waiting'):
        for _ in range(100):
            response=self.client.get('/api/interviews/'+id);value=response.json()
            if value.get('status')==status:return value
            sleep(.025)
        self.fail('Session did not reach '+status)
    def start(self):
        response=self.client.post('/api/interviews',headers=HEADERS,files={
            'resume_file':('resume.md',b'Resume synthetic public text.','text/markdown'),
            'project_file':('project.md',b'Redis project synthetic text.','text/markdown')},data={'max_turns':'5'})
        self.assertEqual(response.status_code,202,response.text);id=response.json()['id'];self.wait(id);return id

    def test_explicit_legacy_upgrade_preserves_old_pages_and_changes_stage(self):
        store=UploadStore(self.service)
        record=store.receive('legacy.md',TEXT.encode())
        job,_=self.service.enqueue(load_uploaded_document('legacy.md',TEXT.encode()))
        job=self.service.process_next(FakeLLM([analysis(),generation(question=False)]),job_id=job['id'])
        self.assertEqual(job['status'],'needs_review')
        self.service.approve(job['id']);store.update(record['id'],status='processing',job_id=job['id'])
        before=[p.path.read_bytes() for p in list_pages(self.service.root)]
        self.client.post('/api/knowledge/stage',headers=HEADERS,files=[('files',('legacy.md',TEXT.encode(),'text/markdown'))])
        with patch('api_server.ensure_worker'):
            response=self.client.post('/api/knowledge/expand',headers=HEADERS,json={'upload_ids':[record['id']]})
        self.assertEqual(response.status_code,202,response.text)
        updated=store.get(record['id']);self.assertEqual(updated['build_stage'],'core');self.assertIsNone(updated['job_id'])
        self.assertEqual(before,[p.path.read_bytes() for p in list_pages(self.service.root)])
        self.assertEqual(self.service.queue.get(job['id'])['status'],'completed')

    def test_bootstrap_exposes_no_keys_or_absolute_private_paths(self):
        response=self.client.get('/api/bootstrap')
        self.assertEqual(response.status_code,200)
        self.assertNotIn('api_key',response.text.lower())
        self.assertNotIn(str(self.service.directory),response.text)
        self.assertEqual(response.json()['files'],[])

    def test_staging_does_not_parse_publish_or_call_models(self):
        response=self.client.post('/api/knowledge/stage',headers=HEADERS,files=[('files',('cache.md',TEXT.encode(),'text/markdown'))])
        self.assertEqual(response.status_code,200)
        self.assertFalse(response.json()['selected'][0]['authorized'])
        self.assertEqual(self.service.queue.list(),[])
        self.assertEqual(self.emb.requests,[])
        self.assertEqual(list_pages(self.service.root),[])

    def test_explicit_start_only_authorizes_owned_selected_uploads(self):
        staged=self.client.post('/api/knowledge/stage',headers=HEADERS,files=[('files',('cache.md',TEXT.encode(),'text/markdown')),('files',('other.md',b'Not selected.','text/markdown'))]).json()
        id=staged['selected'][0]['id'];other=staged['selected'][1]['id']
        response=self.client.post('/api/knowledge/expand',headers=HEADERS,json={'upload_ids':[id]})
        self.assertEqual(response.status_code,202)
        for _ in range(100):
            jobs=self.service.queue.list()
            if jobs and jobs[0]['status']=='completed' and jobs[0]['payload']['receipt']['index_status']=='ready':break
            sleep(.04)
        self.assertEqual(jobs[0]['status'],'completed')
        self.assertFalse(UploadStore(self.service).get(other)['authorized'])
        self.assertTrue(self.emb.requests)

    def test_read_only_information_relations_and_no_hover_server_requirement(self):
        document=load_uploaded_document('source.md',b'Original facts.')
        first=approve_draft(document,{'title':'First','body':'Base knowledge.','tags':['Redis'],'links':[]},self.service.root)
        second=approve_draft(document,{'title':'Second','body':'Related knowledge.','tags':['Redis'],'links':[first.id]},self.service.root)
        before=first.path.read_bytes();path=first.path.relative_to(self.service.root).as_posix()
        response=self.client.get('/api/library/file',params={'path':path})
        self.assertEqual(response.status_code,200)
        info=response.json();self.assertEqual(info['title'],'First')
        self.assertIn('modified_at',info)
        self.assertIn(first.sources[0],[card['path'] for card in info['relations']])
        self.assertIn(second.path.relative_to(self.service.root).as_posix(),[card['path'] for card in info['relations']])
        self.assertEqual(first.path.read_bytes(),before)
        self.assertEqual(self.service.queue.list(),[])
        self.assertEqual(self.emb.requests,[])
        self.assertEqual(self.client.get('/api/library/file',params={'path':'../.env'}).status_code,404)

    def test_conversation_materials_never_enter_library_storage(self):
        id=self.start()
        self.assertEqual(UploadStore(self.service).list(),[])
        self.assertEqual(self.service.queue.list(),[])
        self.assertNotIn('project_text',self.client.get('/api/interviews/'+id).text)

    def test_answer_submission_is_idempotent_and_conflicts_are_rejected(self):
        id=self.start()
        body={'answer':'我负责缓存设计。','request_id':'request-1'}
        self.assertEqual(self.client.post(f'/api/interviews/{id}/answers',headers=HEADERS,json=body).status_code,202)
        self.wait(id)
        self.assertEqual(self.client.post(f'/api/interviews/{id}/answers',headers=HEADERS,json=body).status_code,202)
        self.assertEqual(self.runs[0].calls,['我负责缓存设计。'])
        conflict={**body,'answer':'不同回答'}
        self.assertEqual(self.client.post(f'/api/interviews/{id}/answers',headers=HEADERS,json=conflict).status_code,409)

    def test_simultaneous_different_answer_cannot_advance_same_session_twice(self):
        id=self.start();gate=Event();self.runs[0].gate=gate
        try:
            first=self.client.post(f'/api/interviews/{id}/answers',headers=HEADERS,json={'answer':'one','request_id':'one'})
            self.assertEqual(first.status_code,202)
            second=self.client.post(f'/api/interviews/{id}/answers',headers=HEADERS,json={'answer':'two','request_id':'two'})
            self.assertEqual(second.status_code,409)
        finally:gate.set()
        self.wait(id);self.assertEqual(self.runs[0].calls,['one'])

    def test_owner_isolation_and_cross_site_mutation_guard(self):
        id=self.start()
        upload=self.client.post('/api/knowledge/stage',headers=HEADERS,files=[('files',('cache.md',TEXT.encode(),'text/markdown'))]).json()['selected'][0]['id']
        other=TestClient(self.app);other.get('/api/bootstrap')
        self.assertEqual(other.get('/api/interviews/'+id).status_code,404)
        self.assertEqual(other.post('/api/knowledge/expand',headers=HEADERS,json={'upload_ids':[upload]}).status_code,404)
        self.assertEqual(other.get('/api/knowledge/uploads').json()['uploads'],[])
        self.assertEqual(self.client.post('/api/knowledge/expand',json={'upload_ids':[upload]}).status_code,403)
        bad={**HEADERS,'Origin':'https://outside.test'}
        self.assertEqual(self.client.post('/api/knowledge/expand',headers=bad,json={'upload_ids':[upload]}).status_code,403)

    def test_failed_document_parsing_is_visible_without_a_model_call(self):
        response=self.client.post('/api/interviews',headers=HEADERS,files={
            'resume_file':('bad.pdf',b'not a PDF','application/pdf'),
            'project_file':('project.md',b'public project','text/markdown')})
        self.assertEqual(response.status_code,202)
        value=self.wait(response.json()['id'],'error')
        self.assertIn('PDF',value['error']);self.assertEqual(self.runs,[])


if __name__=='__main__':unittest.main()
