"""Graph, core entities, explicit stages and safe external-file link workflows."""
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from time import sleep,time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from api_server import create_app
from document_loader import load_uploaded_document
from wiki_intake import IntakeService,single_page_response
from wiki_automatic import additive_proposal
from wiki_actions import WikiActions
from wiki_changes import ChangeTracker
from wiki_publish import PublishConflict,file_digest,commit_files
from wiki_store import list_pages
from wiki_relations import graph_data,relation_cards
from test_wiki_intake import FakeEmbeddings,FakeLLM
from wiki_workbench_fakes import WorkbenchLLM

TEXT='Meituan operates a platform. Redis stores cached data. TTL expires keys.'
HEADERS={'X-Workbench-Request':'1'}

class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();base=Path(self.tmp.name)
        self.service=IntakeService(base/'vault',base/'private');self.llm=WorkbenchLLM();self.emb=FakeEmbeddings()
        self.actions=WikiActions(self.service,lambda:self.llm,lambda:SimpleNamespace(embeddings=self.emb))
    def tearDown(self):
        self.actions.pool.shutdown(wait=True,cancel_futures=True);self.tmp.cleanup()
    def write(self,path,text):
        target=self.service.root/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(text,encoding='utf-8');return target
    def core(self,extra=""):
        job,_=self.service.enqueue(load_uploaded_document('public.md',(TEXT+extra).encode()),stage='core',automatic=True)
        job=self.service.process_next(self.llm,job_id=job['id'])
        self.assertEqual(job['status'],'needs_review',job['error'])
        payload=job['payload'];payload['proposal']=additive_proposal(job);self.service.queue.save(job['id'],payload=payload)
        return self.service.approve(job['id'],acknowledge_conflicts=True)
    def wait(self,id,status):
        for _ in range(200):
            row=self.actions.get(id)
            if row['status'] in (status,'failed'):break
            sleep(.02)
        self.assertEqual(row['status'],status,row['error']);return row
    def imported(self):
        self.write('concepts/old.md','# Redis Knowledge\nRedis stores cached data.')
        self.actions.tracker.scan(now=time()+10)
        self.actions.tracker.state(['concepts/old.md'],'baseline')
        self.write('concepts/new.md','# New cache\nRedis stores cached data. New notes.')
        self.actions.tracker.scan(now=time()-5)
        self.actions.tracker.scan(now=time())
        return 'concepts/new.md'
    def proposal(self):
        path=self.imported();job=self.actions.start_links('owner',[path]);return self.wait(job['id'],'review')

    def test_core_generates_entity_concept_source_concurrently_and_stops(self):
        job=self.core();pages=list_pages(self.service.root)
        self.assertEqual({p.type for p in pages},{'entity','concept','source'})
        self.assertGreaterEqual(self.llm.peak,2);self.assertLessEqual(self.llm.peak,3)
        self.assertFalse((self.service.root/'overview.md').exists())
        self.assertTrue((self.service.root/'index.md').exists());self.assertTrue((self.service.root/'log.md').exists())
        self.assertTrue(all((p.path.relative_to(self.service.root).parts[0]=='sources') for p in pages if p.type=='source'))
        self.assertEqual(self.actions.list('owner'),[])
        graph=graph_data(self.service.root);self.assertTrue(graph['links'])
        self.assertTrue(all(p.evidence for p in pages if p.type!='source'))

    def test_entity_updates_reuse_identity_and_do_not_create_duplicates(self):
        self.core();second=self.core(" New source details.")
        self.assertEqual(len([p for p in list_pages(self.service.root) if p.type=='entity']),1)

    def test_optional_stages_require_explicit_calls_and_order(self):
        job=self.core();count=len(self.llm.calls)
        with self.assertRaises(ValueError):self.actions.start_stage('owner',job['id'],3)
        self.assertEqual(len(self.llm.calls),count)
        stage2=self.actions.start_stage('owner',job['id'],2);self.wait(stage2['id'],'completed')
        overview=(self.service.root/'overview.md').read_bytes()
        self.assertEqual(self.actions.start_stage('owner',job['id'],2)['id'],stage2['id'])
        self.llm.peak=0
        stage3=self.actions.start_stage('owner',job['id'],3);self.wait(stage3['id'],'completed')
        self.assertGreaterEqual(self.llm.peak,2)
        self.assertEqual((self.service.root/'overview.md').read_bytes(),overview)
        self.assertTrue({'entity','concept','source','question','synthesis'}<={p.type for p in list_pages(self.service.root)})

    def test_graph_resolves_aliases_relative_links_and_deduplicates_backlinks(self):
        self.write('concepts/a.md','---\nid: a\ntitle: File A\naliases: [Alpha]\n---\n# A\n[[../entities/b#part|B]]\n[[b]]')
        self.write('entities/b.md','# B\n[[Alpha]]')
        self.write('loose.md','# Isolated')
        graph=graph_data(self.service.root)
        self.assertEqual(len(graph['nodes']),3);self.assertEqual(len(graph['links']),1)
        self.assertEqual(len(graph['links'][0]['directions']),2)
        self.assertEqual(len(relation_cards(self.service.root,'concepts/a.md')),1)

    def test_ambiguous_names_do_not_create_false_edges(self):
        self.write('concepts/a.md','# A\n[[same]]')
        self.write('entities/same.md','# Entity')
        self.write('concepts/same.md','# Concept')
        self.assertEqual(graph_data(self.service.root)['links'],[])

    def test_copy_is_debounced_persistent_and_does_not_call_models(self):
        self.write('concepts/new.md','# New\nRedis notes')
        before=list(self.llm.calls)
        self.assertEqual(self.actions.tracker.scan(now=10),[])
        self.assertEqual(self.actions.tracker.scan(now=11),[])
        self.assertEqual(len(self.actions.tracker.scan(now=13)),1)
        tracker=ChangeTracker(self.service)
        self.assertEqual(len(tracker.scan(now=14)),1);self.assertEqual(self.llm.calls,before)
        self.assertEqual(list_pages(self.service.root),[])

    def test_generated_files_do_not_raise_copy_notifications(self):
        self.core();self.actions.tracker.scan(now=time()-10)
        self.assertEqual(self.actions.tracker.scan(),[])

    def test_model_suggestions_do_not_write_until_confirmed(self):
        row=self.proposal();path=self.service.root/'concepts/new.md'
        before=path.read_bytes();self.assertEqual(len(row['payload']['suggestions']),1)
        self.assertNotIn('[[',path.read_text());self.assertEqual(list_pages(self.service.root),[])
        suggestion=row['payload']['suggestions'][0]
        self.actions.apply_links('owner',row['id'],[suggestion['id']]);self.wait(row['id'],'completed')
        self.assertIn('[[concepts/old]]',path.read_text())
        self.assertTrue(path.read_bytes().startswith(before.rstrip()))
        self.assertEqual(list_pages(self.service.root),[])
        self.assertEqual(len(graph_data(self.service.root)['links']),1)
        self.assertIn('引用本页',relation_cards(self.service.root,'concepts/old.md')[0]['relations'])
        count=path.read_text().count('[[concepts/old]]')
        self.actions.apply_links('owner',row['id'],[suggestion['id']]);self.assertEqual(path.read_text().count('[[concepts/old]]'),count)

    def test_target_edit_rejects_stale_suggestion_and_preserves_user_content(self):
        row=self.proposal();path=self.service.root/'concepts/new.md';before=path.read_bytes()
        self.write('concepts/old.md','# Redis Knowledge\nChanged by user.')
        with self.assertRaises(PublishConflict):self.actions.apply_links('owner',row['id'],[row['payload']['suggestions'][0]['id']])
        self.assertEqual(path.read_bytes(),before)

    def test_forged_quote_is_rejected_without_writing(self):
        path=self.imported()
        wrong={'links':[{'target':'concepts/old.md','type':'concept','reason':'reason','source_quote':'fabricated','target_quote':'Redis stores cached data.'}]}
        self.actions.llm_factory=lambda:FakeLLM([wrong])
        row=self.actions.start_links('owner',[path]);self.wait(row['id'],'failed')
        self.assertNotIn('[[',(self.service.root/path).read_text())

    def test_empty_relationships_can_be_cancelled_without_changes(self):
        path=self.imported();self.actions.llm_factory=lambda:FakeLLM([{'links':[]}])
        row=self.actions.start_links('owner',[path]);row=self.wait(row['id'],'review')
        self.assertEqual(row['payload']['suggestions'],[])
        self.actions.dismiss('owner',row['id']);self.assertEqual(self.actions.get(row['id'])['status'],'dismissed')

    def test_link_actions_are_owner_scoped(self):
        row=self.proposal()
        with self.assertRaises(ValueError):self.actions.apply_links('other',row['id'],[row['payload']['suggestions'][0]['id']])
        self.assertEqual(self.actions.list('other'),[])

    def test_provider_failure_is_redacted(self):
        path=self.imported();self.actions.llm_factory=lambda:FakeLLM([RuntimeError('secret-key-sentinel')])
        row=self.actions.start_links('owner',[path]);row=self.wait(row['id'],'failed')
        self.assertNotIn('secret-key',row['error'])

    def test_restart_does_not_automatically_resume_model_tasks(self):
        with self.service.queue.connection() as db:
            db.execute('INSERT INTO wiki_actions VALUES(?,?,?,?,?,?,?,?,?)',('f'*32,self.service.queue.vault,'owner','links','running',json.dumps({'paths':[]}),'',time(),time()))
        before=list(self.llm.calls);self.actions.recover()
        self.assertEqual(self.actions.get('f'*32)['status'],'failed');self.assertEqual(self.llm.calls,before)

    def test_stage_navigation_preserves_user_overview(self):
        job=self.core();self.write('overview.md','# My navigation\nUser notes that must remain.')
        row=self.actions.start_stage('owner',job['id'],2);self.wait(row['id'],'completed')
        self.assertIn('User notes that must remain.',(self.service.root/'overview.md').read_text())

    def test_unknown_graph_paths_do_not_leak_or_load_private_files(self):
        self.write('concepts/a.md','# A\n[[../../private/secret]]')
        self.write('private/secret.md','# Secret')
        graph=graph_data(self.service.root)
        self.assertEqual(len(graph['nodes']),1);self.assertEqual(graph['links'],[])

    def test_source_only_is_valid_when_no_entity_or_concept_is_supported(self):
        job,_=self.service.enqueue(load_uploaded_document('plain.md',b'General unclassified notes.'),stage='core')
        model=FakeLLM([{'summary':'Only source facts.','topics':[]},{'pages':[]}])
        job=self.service.process_next(model,job_id=job['id'])
        self.assertEqual(job['status'],'needs_review',job['error'])
        self.service.approve(job['id'])
        self.assertEqual({p.type for p in list_pages(self.service.root)},{'source'})

    def test_core_rejects_optional_page_types_without_partial_publication(self):
        class Wrong(WorkbenchLLM):
            def invoke(inner,messages):
                if json.loads(messages[1].content).get('mode')=='plan_core':
                    return SimpleNamespace(content=json.dumps({'pages':[{'id':'wrong','type':'question'}]}))
                return super().invoke(messages)
        job,_=self.service.enqueue(load_uploaded_document('public.md',TEXT.encode()),stage='core')
        job=self.service.process_next(Wrong(),job_id=job['id'])
        self.assertEqual(job['status'],'failed');self.assertEqual(list_pages(self.service.root),[])
        self.assertFalse((self.service.root/'sources').exists())

    def test_parallel_page_failure_never_publishes_a_partial_batch(self):
        class Broken(WorkbenchLLM):
            def invoke(inner,messages):
                data=json.loads(messages[1].content)
                if data.get('mode')=='compile_page' and data['page_plan']['type']=='entity':
                    raise RuntimeError('simulated provider failure')
                return super().invoke(messages)
        job,_=self.service.enqueue(load_uploaded_document('public.md',TEXT.encode()),stage='core')
        job=self.service.process_next(Broken(),job_id=job['id'])
        self.assertEqual(job['status'],'failed');self.assertEqual(list_pages(self.service.root),[])
        self.assertFalse((self.service.root/'sources').exists())

    def test_bom_and_line_endings_are_preserved_by_link_apply(self):
        path=self.imported();target=self.service.root/path
        original=b'\xef\xbb\xbf# New note\r\nRedis stores cached data.\r\n'
        target.write_bytes(original)
        self.actions.tracker.scan(now=time()-4);self.actions.tracker.scan()
        row=self.actions.start_links('owner',[path]);row=self.wait(row['id'],'review')
        self.actions.apply_links('owner',row['id'],[row['payload']['suggestions'][0]['id']]);self.wait(row['id'],'completed')
        self.assertTrue(target.read_bytes().startswith(original))

    def test_api_graph_and_relink_authorization(self):
        app=create_app(self.service,llm_factory=lambda:self.llm,embedding_factory=lambda:SimpleNamespace(embeddings=self.emb))
        with TestClient(app) as client:
            self.assertEqual(client.get('/api/library/graph').status_code,401)
            client.get('/api/bootstrap');path=self.imported();before=len(self.llm.calls)
            pending=client.get('/api/knowledge/activity').json()['pending']
            self.assertTrue(any(p['path']==path for p in pending));self.assertEqual(len(self.llm.calls),before)
            self.assertEqual(client.post('/api/knowledge/relink',json={'paths':[path]}).status_code,403)
            response=client.post('/api/knowledge/relink',headers=HEADERS,json={'paths':[path]})
            self.assertEqual(response.status_code,202,response.text)
            action=app.state.actions
            for _ in range(100):
                row=action.get(response.json()['id'])
                if row['status']=='review':break
                sleep(.02)
            self.assertEqual(row['status'],'review',row['error'])
            stranger=TestClient(app);stranger.get('/api/bootstrap')
            ids=[s['id'] for s in row['payload']['suggestions']]
            self.assertEqual(stranger.post('/api/knowledge/actions/'+row['id']+'/apply',headers=HEADERS,json={'ids':ids}).status_code,409)
            self.assertNotIn('[[',(self.service.root/path).read_text())
            done=client.post('/api/knowledge/actions/'+row['id']+'/apply',headers=HEADERS,json={'ids':ids})
            self.assertEqual(done.status_code,202,done.text)
            for _ in range(100):
                if action.get(row['id'])['status']=='completed':break
                sleep(.02)
            self.assertIn('[[',(self.service.root/path).read_text())
            self.assertEqual(list_pages(self.service.root),[])

    def test_api_continuation_requires_owned_upload(self):
        job=self.core();app=create_app(self.service,llm_factory=lambda:self.llm,embedding_factory=lambda:SimpleNamespace(embeddings=self.emb))
        with patch('api_server.ensure_worker'), TestClient(app) as client:
            client.get('/api/bootstrap')
            result=client.post('/api/knowledge/continue',headers=HEADERS,json={'job_id':job['id'],'stage':2})
            self.assertEqual(result.status_code,404);self.assertFalse((self.service.root/'overview.md').exists())

    def test_graph_ignores_example_links_in_code_and_comments(self):
        self.write('concepts/a.md','# A\n```markdown\n[[b]]\n```\n`[[b]]`\n<!-- [[b]] -->')
        self.write('concepts/b.md','# B')
        self.assertEqual(graph_data(self.service.root)['links'],[])

    def test_entity_without_a_source_name_is_rejected(self):
        class Fabricated(WorkbenchLLM):
            def invoke(inner,messages):
                value=super().invoke(messages);payload=json.loads(messages[1].content)
                if payload.get('mode')=='plan_core':
                    result=json.loads(value.content)
                    for page in result['pages']:
                        if page['type']=='entity':page['title']='Imaginary Company'
                    return SimpleNamespace(content=json.dumps(result))
                return value
        job,_=self.service.enqueue(load_uploaded_document('public.md',TEXT.encode()),stage='core')
        result=self.service.process_next(Fabricated(),job_id=job['id'])
        self.assertEqual(result['status'],'failed');self.assertEqual(list_pages(self.service.root),[])

    def test_single_page_compatible_envelopes_keep_identity(self):
        page={'id':'test','type':'concept','title':'Test','body':'Facts'}
        for response in ({'page':page},{'pages':[page]},page):
            self.assertEqual(single_page_response(response,page),page)
        for wrong in ({'pages':[]},{'pages':[page,page]},{'page':page,'pages':[page]}, {'page':[]},{'unrelated':'value'}):
            with self.assertRaises(ValueError):single_page_response(wrong,page)
        with self.assertRaises(ValueError):single_page_response({'pages':[{**page,'id':'other'}]},page)

    def test_single_page_prompt_has_one_contract_and_accepts_singleton_pages(self):
        class Singleton(WorkbenchLLM):
            def invoke(inner,messages):
                result=super().invoke(messages)
                if json.loads(messages[1].content).get('mode')=='compile_page':
                    instruction=messages[0].content
                    self.assertIn('"page":{',instruction)
                    self.assertNotIn('"pages":[',instruction)
                    return SimpleNamespace(content=json.dumps({'pages':[json.loads(result.content)['page']]}))
                return result
        job,_=self.service.enqueue(load_uploaded_document('public.md',TEXT.encode()),stage='core')
        result=self.service.process_next(Singleton(),job_id=job['id'])
        self.assertEqual(result['status'],'needs_review',result['error'])
        self.service.approve(job['id'])
        self.assertEqual({p.type for p in list_pages(self.service.root)},{'entity','source','concept'})

    def test_single_page_envelope_does_not_bypass_evidence_validation(self):
        class Forged(WorkbenchLLM):
            def invoke(inner,messages):
                result=super().invoke(messages)
                if json.loads(messages[1].content).get('mode')=='compile_page':
                    page=json.loads(result.content)['page'];page['evidence_ids']=['e999999']
                    return SimpleNamespace(content=json.dumps({'pages':[page]}))
                return result
        job,_=self.service.enqueue(load_uploaded_document('public.md',TEXT.encode()),stage='core')
        result=self.service.process_next(Forged(),job_id=job['id'])
        self.assertEqual(result['status'],'failed');self.assertEqual(list_pages(self.service.root),[])

if __name__=='__main__':unittest.main()
