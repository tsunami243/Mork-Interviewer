"""Upload authorizes core processing immediately; progress records real completion counts."""
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Event
from time import sleep
from types import SimpleNamespace
import unittest
from fastapi.testclient import TestClient
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from api_server import create_app
from wiki_intake import IntakeService
from wiki_store import list_pages
from wiki_uploads import UploadStore
from test_wiki_intake import FakeEmbeddings
from wiki_workbench_fakes import WorkbenchLLM
HEADERS={'X-Workbench-Request':'1'}
TEXT='Meituan operates a platform. Redis stores cached data. TTL expires keys.'

class UploadProgressTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();base=Path(self.tmp.name);self.service=IntakeService(base/'vault',base/'private')
        self.llm=WorkbenchLLM();self.emb=FakeEmbeddings();self.gate=Event();self.gate.set()
        self.app=create_app(self.service,llm_factory=lambda:self.llm,embedding_factory=lambda:SimpleNamespace(embeddings=self.emb))
        self.context=TestClient(self.app);self.client=self.context.__enter__();self.client.get('/api/bootstrap')
    def tearDown(self):
        self.gate.set()
        from wiki_automatic import _WORKERS
        for worker in list(_WORKERS.values()):
            if worker.service.directory==self.service.directory and worker.thread:worker.thread.join(timeout=5)
        self.context.__exit__(None,None,None);self.tmp.cleanup()
    def upload(self,name='public.md',content=TEXT.encode()):
        return self.client.post('/api/knowledge/upload',headers=HEADERS,files=[('files',(name,content,'application/octet-stream'))])
    def wait(self,predicate):
        for _ in range(200):
            rows=self.client.get('/api/knowledge/uploads').json()['uploads']
            if rows and predicate(rows[0]):return rows[0]
            sleep(.02)
        self.fail('Progress state did not arrive')

    def test_upload_immediately_authorizes_publishes_and_indexes(self):
        response=self.upload();self.assertEqual(response.status_code,202,response.text)
        self.assertTrue(response.json()['selected'][0]['authorized'])
        row=self.wait(lambda r:r.get('job') and r['job']['index_status']=='ready')
        self.assertEqual(row['job']['stage'],'core');self.assertEqual(row['job']['progress'],{'stage':'index','completed':1,'total':1})
        self.assertEqual({p.type for p in list_pages(self.service.root)},{'concept','entity','source'})
        self.assertFalse((self.service.root/'overview.md').exists())

    def test_page_progress_is_partial_while_one_parallel_page_waits(self):
        self.gate.clear();gate=self.gate
        class Blocking(WorkbenchLLM):
            def invoke(inner,messages):
                payload=json.loads(messages[1].content)
                if payload.get('mode')=='compile_page' and payload['page_plan']['type']=='entity':gate.wait(5)
                return super().invoke(messages)
        self.llm=Blocking();self.upload()
        row=self.wait(lambda r:r.get('job') and r['job']['progress'].get('stage')=='generation' and r['job']['progress'].get('completed')==1)
        self.assertEqual(row['job']['progress']['total'],2);self.assertEqual(row['job']['status'],'running')
        self.assertEqual(list_pages(self.service.root),[])
        self.gate.set();self.wait(lambda r:r.get('job') and r['job']['index_status']=='ready')

    def test_parse_failure_is_visible_and_original_is_retained(self):
        response=self.upload('broken.pdf',b'%PDF-1.4\nThis is not a PDF document.')
        self.assertEqual(response.status_code,202)
        row=self.wait(lambda r:r['status']=='failed')
        self.assertIsNone(row['job']);self.assertTrue(row['error']);self.assertEqual(self.llm.calls,[])
        record=UploadStore(self.service).get(row['id']);self.assertTrue(Path(record['path']).exists())

    def test_invalid_batch_does_not_start_valid_earlier_files(self):
        response=self.client.post('/api/knowledge/upload',headers=HEADERS,files=[('files',('valid.md',TEXT.encode(),'text/markdown')),('files',('invalid.exe',b'executable','application/octet-stream'))])
        self.assertEqual(response.status_code,400);self.assertEqual(UploadStore(self.service).list(),[]);self.assertEqual(self.llm.calls,[])

    def test_index_failure_does_not_report_a_completed_index(self):
        class BadEmbedding:
            def create(self,**kwargs):raise RuntimeError('sentinel-secret-provider-body')
        self.app.state.actions.embedding_factory=lambda:SimpleNamespace(embeddings=BadEmbedding())
        # Worker factory is closed over create_app; replace the existing fake client method only.
        self.emb.create=lambda **kwargs:BadEmbedding().create(**kwargs)
        self.upload();row=self.wait(lambda r:r.get('job') and r['job']['index_status']=='failed' and r['job']['index_attempts']==3)
        self.assertEqual(row['job']['status'],'completed');self.assertEqual(row['job']['progress']['completed'],0)
        self.assertNotIn('sentinel-secret',str(row));self.assertTrue(list_pages(self.service.root))

    def test_progress_api_remains_owner_scoped_and_guarded(self):
        self.assertEqual(self.client.post('/api/knowledge/upload',files=[('files',('public.md',TEXT.encode(),'text/markdown'))]).status_code,403)
        self.upload();self.wait(lambda r:r.get('job') and r['job']['index_status']=='ready')
        stranger=TestClient(self.app);stranger.get('/api/bootstrap')
        self.assertEqual(stranger.get('/api/knowledge/uploads').json()['uploads'],[])

if __name__=='__main__':unittest.main()
