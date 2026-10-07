"""Upload-only automatic pipeline tests. No real LLM or Embedding calls."""

import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from document_loader import load_uploaded_document
from wiki_evidence import locate_quote
from wiki_intake import IntakeService
from wiki_uploads import UploadStore
from wiki_automatic import AutomaticWorker, ensure_worker
from wiki_store import approve_draft, list_pages
from wiki_index import load_wiki_index
from test_wiki_intake import FakeLLM, FakeEmbeddings, analysis, generation, TEXT


def id_generation(payload, old_id=None, conflict=None):
    result = generation(old_id, conflict=conflict)
    evidence_id = payload['analysis']['topics'][0]['evidence_ids'][0]
    for page in result['pages']:
        page.pop('quotes')
        page['evidence_ids'] = [evidence_id.upper()]
    return result


class AutomaticTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        base = Path(self.tmp.name)
        self.service = IntakeService(base/'vault',base/'private')
        self.store = UploadStore(self.service)
        self.emb = FakeEmbeddings()
        self.embedding_factory = lambda: SimpleNamespace(embeddings=self.emb)

    def tearDown(self):
        self.tmp.cleanup()

    def worker(self, llm):
        return AutomaticWorker(self.service, lambda: llm, self.embedding_factory, retry_delay=0)

    def test_upload_saves_exact_original_before_processing(self):
        record = self.store.receive('../public.md', TEXT.encode(),authorize=True)
        self.assertEqual(Path(record['path']).read_bytes(), TEXT.encode())
        self.assertEqual(record['name'], 'public.md')
        self.assertIsNone(record['job_id'])
        self.assertEqual(self.service.queue.list(), [])
        self.assertEqual(list_pages(self.service.root), [])

    def test_upload_automatically_parses_publishes_and_indexes(self):
        record = self.store.receive('cache.md',TEXT.encode(),authorize=True)
        llm = FakeLLM([analysis(),id_generation])
        self.worker(llm).run()
        uploaded = self.store.get(record['id'])
        job = self.service.queue.get(uploaded['job_id'])
        self.assertEqual(job['status'],'completed')
        self.assertEqual(job['payload']['receipt']['index_status'],'ready')
        self.assertEqual(len(llm.calls),2)
        self.assertEqual({p.type for p in list_pages(self.service.root)}, {'source','concept','question'})
        self.assertTrue(all(p.review_status=='automatic' for p in list_pages(self.service.root)))
        self.assertTrue(self.emb.requests)
        self.assertIsNotNone(load_wiki_index(root=self.service.root,directory=self.service.index_directory))
        for page in list_pages(self.service.root):
            for evidence in page.evidence:
                self.assertEqual(evidence['quote'],'Redis stores cached data.')
                self.assertIn('id',evidence)

    def test_generation_uses_program_evidence_not_rewritten_quotes(self):
        self.store.receive('cache.md',TEXT.encode(),authorize=True)
        def response(payload):
            result=id_generation(payload)
            for page in result['pages']:
                page['quotes']=['A deliberately invented rewrite that is ignored.']
            return result
        llm=FakeLLM([analysis(),response])
        self.worker(llm).run()
        job=self.service.queue.list()[0]
        self.assertEqual(job['status'],'completed')
        self.assertTrue(llm.calls[1]['evidence_registry'])
        for page in list_pages(self.service.root):
            self.assertNotIn('deliberately invented',page.body)

    def test_pdf_layout_whitespace_normalization_preserves_actual_original(self):
        text='Redis\n stores\u00a0cached data.'
        lower,upper=locate_quote(text,'Redis stores cached data.')
        self.assertEqual(text[lower:upper],text)
        document=load_uploaded_document('layout.md',text.encode())
        self.service.enqueue(document,automatic=True)
        llm=FakeLLM([analysis(),id_generation])
        self.worker(llm).run()
        job=self.service.queue.list()[0]
        self.assertEqual(job['status'],'completed')
        concept=next(p for p in list_pages(self.service.root) if p.type=='concept')
        self.assertEqual(concept.evidence[0]['quote'],text)
        with self.assertRaises(ValueError):
            locate_quote(text,'Redis guarantees every fact is true.')

    def test_unknown_evidence_ids_retry_without_reanalyzing_then_succeed(self):
        self.store.receive('cache.md',TEXT.encode(),authorize=True)
        def bad(payload):
            result=id_generation(payload)
            result['pages'][0]['evidence_ids']=['e9999']
            return result
        llm=FakeLLM([analysis(),bad,id_generation])
        self.worker(llm).run()
        job=self.service.queue.list()[0]
        self.assertEqual(job['status'],'completed')
        self.assertEqual(job['attempts'],2)
        self.assertEqual(len(llm.calls),3)
        self.assertIn('未知证据编号',llm.calls[-1]['validation_feedback'])

    def test_retries_are_bounded_and_original_file_is_retained(self):
        record=self.store.receive('cache.md',TEXT.encode(),authorize=True)
        llm=FakeLLM([RuntimeError('secret'),RuntimeError('secret'),RuntimeError('secret')])
        self.worker(llm).run()
        job=self.service.queue.list()[0]
        self.assertEqual(job['status'],'failed')
        self.assertEqual(job['attempts'],3)
        self.assertEqual(len(llm.calls),3)
        self.assertEqual(Path(record['path']).read_bytes(),TEXT.encode())
        self.assertNotIn('secret',job['error'])
        self.assertEqual(list_pages(self.service.root),[])
        self.store.receive('cache.md',TEXT.encode(),authorize=True)
        good=FakeLLM([analysis(),id_generation])
        self.worker(good).run()
        self.assertEqual(self.service.queue.list()[0]['status'],'completed')

    def test_duplicate_upload_does_not_make_model_or_embedding_calls(self):
        self.store.receive('cache.md',TEXT.encode(),authorize=True)
        llm=FakeLLM([analysis(),id_generation])
        self.worker(llm).run()
        count=len(self.emb.requests)
        self.store.receive('cache.md',TEXT.encode(),authorize=True)
        other=FakeLLM([])
        self.worker(other).run()
        self.assertEqual(other.calls,[])
        self.assertEqual(len(self.emb.requests),count)
        self.assertEqual(len(self.service.queue.list()),1)
        self.assertEqual(len(self.store.list()),1)

    def test_existing_page_is_preserved_additively_with_conflict_labels(self):
        old=approve_draft(load_uploaded_document('old.md',b'Original source.'),
            {'title':'Redis Cache','body':'Old valid fact must remain.','tags':['Redis'],'links':[]},self.service.root)
        self.store.receive('cache.md',TEXT.encode(),authorize=True)
        conflict='两份资料的适用条件不同，不应自动裁定。'
        llm=FakeLLM([analysis(old_id=old.id,conflict=conflict),lambda p:id_generation(p,old.id,conflict)])
        self.worker(llm).run()
        page=next(p for p in list_pages(self.service.root) if p.id==old.id)
        self.assertIn('Old valid fact must remain.',page.body)
        self.assertIn('新资料补充',page.body)
        self.assertEqual(len(page.sources),2)
        self.assertIn(conflict,page.conflicts)
        self.assertEqual(page.review_status,'automatic')

    def test_unread_large_old_page_gets_linked_supplement_not_overwritten(self):
        old=approve_draft(load_uploaded_document('old.md',b'Original source.'),
            {'title':'Redis Cache','body':'Old valid fact.','tags':['Redis'],'links':[]},self.service.root)
        old.path.write_text(old.path.read_text(encoding='utf-8')+'大段旧知识。'*5000,encoding='utf-8')
        before=old.path.read_bytes()
        self.store.receive('cache.md',TEXT.encode(),authorize=True)
        llm=FakeLLM([analysis(old_id=old.id),lambda p:id_generation(p,old.id)])
        self.worker(llm).run()
        self.assertEqual(self.service.queue.list()[0]['status'],'completed')
        self.assertEqual(old.path.read_bytes(),before)
        supplement=next(p for p in list_pages(self.service.root) if p.type=='concept' and p.id!=old.id)
        self.assertIn(old.id,supplement.links)

    def test_parse_failure_keeps_original_binary(self):
        data=b'not a valid PDF'
        record=self.store.receive('bad.pdf',data,authorize=True)
        self.worker(FakeLLM([])).run()
        failed=self.store.get(record['id'])
        self.assertEqual(failed['status'],'failed')
        self.assertEqual(Path(failed['path']).read_bytes(),data)
        self.assertEqual(self.service.queue.list(),[])

    def test_dead_parser_recovers_from_original(self):
        record=self.store.receive('cache.md',TEXT.encode(),authorize=True)
        self.store.claim_pending()
        with self.service.queue.connection() as db:
            db.execute('UPDATE uploads SET owner_pid=2147483647 WHERE id=?',(record['id'],))
        recovered=UploadStore(IntakeService(self.service.root,self.service.directory))
        recovered.recover()
        self.assertTrue(recovered.pending())
        self.worker(FakeLLM([analysis(),id_generation])).run()
        self.assertEqual(self.service.queue.list()[0]['status'],'completed')

    def test_index_failure_retries_without_undoing_knowledge(self):
        self.store.receive('cache.md',TEXT.encode(),authorize=True)
        failing=lambda: (_ for _ in ()).throw(RuntimeError('key-secret'))
        worker=AutomaticWorker(self.service,lambda:FakeLLM([analysis(),id_generation]),failing,retry_delay=0)
        worker.run()
        job=self.service.queue.list()[0]
        self.assertEqual(job['status'],'completed')
        self.assertEqual(job['payload']['receipt']['index_attempts'],3)
        self.assertEqual(job['payload']['receipt']['index_status'],'failed')
        self.assertEqual(len(list_pages(self.service.root)),3)
        self.assertNotIn('key-secret',json.dumps(job['payload']['receipt']))
        self.store.receive('cache.md',TEXT.encode(),authorize=True)
        self.worker(FakeLLM([])).run()
        self.assertEqual(self.service.queue.list()[0]['payload']['receipt']['index_status'],'ready')

    def test_one_background_worker_per_vault_across_reruns(self):
        self.store.receive('cache.md',TEXT.encode(),authorize=True)
        entered, release = Event(), Event()
        def delayed(payload):
            entered.set()
            release.wait(5)
            return analysis()
        llm=FakeLLM([delayed,id_generation])
        first=ensure_worker(self.service,lambda:llm,self.embedding_factory)
        self.assertTrue(entered.wait(5))
        second=ensure_worker(self.service,lambda:llm,self.embedding_factory)
        self.assertIs(first,second)
        release.set()
        first.thread.join(10)
        self.assertFalse(first.thread.is_alive())
        self.assertEqual(len(llm.calls),2)
        self.assertEqual(self.service.queue.list()[0]['status'],'completed')

    def test_manual_legacy_failed_tasks_are_not_silently_charged(self):
        self.service.enqueue(load_uploaded_document('legacy.md',TEXT.encode()))
        llm=FakeLLM([])
        self.worker(llm).run()
        self.assertEqual(llm.calls,[])
        self.assertEqual(self.service.queue.list()[0]['status'],'queued')


if __name__=='__main__':
    unittest.main()
