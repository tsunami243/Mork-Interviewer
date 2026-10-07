"""Explicit knowledge-upload authorization, not default ingestion."""
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import unittest
from streamlit.testing.v1 import AppTest

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from wiki_intake import IntakeService
from wiki_uploads import UploadStore
from wiki_ui import UPLOAD_KEY,stage_uploads,start_expansion
from wiki_automatic import AutomaticWorker
from test_wiki_intake import FakeLLM,FakeEmbeddings,analysis,TEXT
from test_wiki_automatic import id_generation


class WikiManagerPageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();base=Path(self.tmp.name)
        self.service=IntakeService(base/'vault',base/'private')
    def tearDown(self): self.tmp.cleanup()

    def test_upload_stages_original_without_starting_worker(self):
        upload=SimpleNamespace(name='cache.md',getvalue=lambda:TEXT.encode())
        state={UPLOAD_KEY:[upload]}
        with patch('wiki_ui.st',SimpleNamespace(session_state=state)),patch('wiki_ui.ensure_worker') as worker:
            stage_uploads(self.service)
            worker.assert_not_called()
        records=UploadStore(self.service).list()
        self.assertEqual(len(records),1)
        self.assertFalse(records[0]['authorized'])
        self.assertEqual(Path(records[0]['path']).read_bytes(),TEXT.encode())
        self.assertEqual(self.service.queue.list(),[])

    def test_explicit_start_authorizes_only_selected_files(self):
        store=UploadStore(self.service)
        first=store.receive('cache.md',TEXT.encode())
        second=store.receive('other.md',b'Not selected for knowledge.')
        with patch('wiki_ui.ensure_worker') as schedule:
            start_expansion(self.service,[first['id']]);schedule.assert_called_once()
        llm=FakeLLM([analysis(),id_generation]);emb=FakeEmbeddings()
        AutomaticWorker(self.service,lambda:llm,lambda:SimpleNamespace(embeddings=emb),retry_delay=0).run()
        self.assertEqual(self.service.queue.list()[0]['status'],'completed')
        self.assertFalse(store.get(second['id'])['authorized'])
        self.assertIsNone(store.get(second['id'])['job_id'])
        self.assertEqual(len(llm.calls),2)

    def test_staged_file_is_never_picked_up_by_worker_or_restart(self):
        store=UploadStore(self.service)
        record=store.receive('staged.md',TEXT.encode())
        llm=FakeLLM([]);emb=FakeEmbeddings()
        restarted=IntakeService(self.service.root,self.service.directory)
        AutomaticWorker(restarted,lambda:llm,lambda:SimpleNamespace(embeddings=emb),retry_delay=0).run()
        self.assertEqual(llm.calls,[])
        self.assertEqual(emb.requests,[])
        self.assertFalse(store.get(record['id'])['authorized'])
        self.assertEqual(self.service.queue.list(),[])

    def test_invalid_batch_does_not_partially_authorize_material(self):
        store=UploadStore(self.service);record=store.receive('staged.md',TEXT.encode())
        with self.assertRaises(ValueError): start_expansion(self.service,[record['id'],'missing'])
        self.assertFalse(store.get(record['id'])['authorized'])

    def test_old_unfinished_auto_upload_is_paused_by_upgrade(self):
        import json,time
        job,_=self.service.enqueue(__import__('document_loader').load_uploaded_document('old.md',TEXT.encode()),automatic=True)
        with self.service.queue.connection() as db:
            db.execute("CREATE TABLE uploads(id TEXT PRIMARY KEY,vault TEXT,name TEXT,path TEXT,digest TEXT,job_id TEXT,status TEXT,error TEXT,created REAL,updated REAL)")
            db.execute("INSERT INTO uploads VALUES(?,?,?,?,?,?,'processing','',?,?)",('old-file',self.service.queue.vault,'old.md','unused','digest',job['id'],time.time(),time.time()))
        store=UploadStore(self.service)
        self.assertFalse(store.get('old-file')['authorized'])
        self.assertEqual(store.get('old-file')['status'],'staged')
        self.assertFalse(self.service.queue.get(job['id'])['payload']['automatic'])

    def test_dialog_has_one_start_action_no_operator_controls(self):
        script='from wiki_ui import expansion_dialog\nexpansion_dialog()'
        with patch('wiki_ui.IntakeService',return_value=self.service),patch('wiki_ui.ensure_worker'):
            page=AppTest.from_string(script,default_timeout=10).run()
        self.assertFalse(page.exception)
        self.assertEqual(len(page.get('file_uploader')),1)
        self.assertEqual([button.label for button in page.button],['开始拓展'])
        self.assertEqual(len(page.checkbox),0)
        self.assertEqual(len(page.text_area),0)


if __name__=='__main__': unittest.main()
