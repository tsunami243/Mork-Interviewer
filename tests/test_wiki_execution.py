"""Executable records, targeted recovery and budgets; synthetic documents only."""
import copy,json,os,sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from collections import Counter
from unittest.mock import patch
import unittest
from fastapi.testclient import TestClient
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from wiki_intake import IntakeService
from wiki_execution import BudgetExceeded
from wiki_automatic import AutomaticWorker
from wiki_store import list_pages
from wiki_uploads import UploadStore
from document_loader import load_uploaded_document
from wiki_workbench_fakes import WorkbenchLLM
from test_wiki_intake import FakeEmbeddings
from api_server import create_app
TEXT='Meituan operates a platform. Redis stores cached data. TTL expires keys.'
HEADERS={'X-Workbench-Request':'1'}

class ExecutionTests(unittest.TestCase):
 def setUp(self):
  self.tmp=TemporaryDirectory();p=Path(self.tmp.name);self.service=IntakeService(p/'vault',p/'private')
 def tearDown(self):self.tmp.cleanup()
 def job(self,text=TEXT):return self.service.enqueue(load_uploaded_document('public.md',text.encode()),stage='core',automatic=True)[0]
 def test_page_failure_resumes_only_failed_page_and_keeps_all_attempt_events(self):
  class Once(WorkbenchLLM):
   def __init__(self):super().__init__();self.failed=False;self.modes=[]
   def invoke(inner,messages):
    p=json.loads(messages[1].content);inner.modes.append((p.get('mode','analysis'),p.get('page_plan',{}).get('type')))
    if p.get('mode')=='compile_page' and p['page_plan']['type']=='entity' and not inner.failed:
     inner.failed=True;raise RuntimeError('provider-secret-must-not-be-stored')
    return super().invoke(messages)
  model=Once();job=self.job();first=self.service.process_next(model,job_id=job['id']);self.assertEqual(first['status'],'failed')
  self.assertEqual(list_pages(self.service.root),[])
  self.assertEqual(self.service.queue.automatic_jobs(),[]) # business failure is not automatically replayed
  self.service.queue.retry(job['id']);second=self.service.process_next(model,job_id=job['id']);self.assertEqual(second['status'],'needs_review',second['error'])
  calls=Counter(model.modes);self.assertEqual(calls[('analysis',None)],1);self.assertEqual(calls[('plan_core',None)],1);self.assertEqual(calls[('compile_page','concept')],1);self.assertEqual(calls[('compile_page','entity')],2)
  report=self.service.execution.report(job['id']);self.assertTrue(any(e['kind']=='cache_reused' for e in report['events']))
  self.assertNotIn('provider-secret',str(report));self.assertGreaterEqual(len({e['session'] for e in report['events']}),2)

 def test_call_budget_stops_before_more_model_requests_and_can_be_explicitly_extended(self):
  with patch.dict(os.environ,{'WIKI_MAX_MODEL_CALLS':'1'}):
   job=self.job();model=WorkbenchLLM();first=self.service.process_next(model,job_id=job['id'])
  self.assertEqual(first['status'],'failed');self.assertIn('预算',first['error']);self.assertEqual(len(model.calls),1)
  report=self.service.execution.report(job['id']);self.assertEqual(report['budget']['calls'],1)
  self.service.execution.extend(job['id'],{'calls':10});self.service.queue.retry(job['id'])
  second=self.service.process_next(model,job_id=job['id']);self.assertEqual(second['status'],'needs_review',second['error']);self.assertEqual(len([c for c in model.calls if 'chunk' in c]),1)

 def test_known_token_usage_is_recorded_and_unknown_usage_is_not_said_to_be_zero(self):
  class Usage(WorkbenchLLM):
   def invoke(inner,messages):
    value=super().invoke(messages);value.usage_metadata={'input_tokens':3,'output_tokens':4};return value
  job=self.job();self.service.process_next(Usage(),job_id=job['id']);report=self.service.execution.report(job['id'])
  self.assertEqual(report['budget']['tokens'],report['budget']['calls']*7);self.assertEqual(report['budget']['unknown_usage'],0)
  another=self.job(TEXT+' More source.');self.service.process_next(WorkbenchLLM(),job_id=another['id']);self.assertGreater(self.service.execution.report(another['id'])['budget']['unknown_usage'],0)

 def test_misclassified_document_node_redirects_without_weakening_entity_validation(self):
  class Document(WorkbenchLLM):
   def invoke(inner,messages):
    p=json.loads(messages[1].content);value=super().invoke(messages)
    if 'chunk' in p:
     r=json.loads(value.content);r['topics'].append({'type':'entity','title':'Interview question list','tags':[],'key_points':['source container'],'quotes':['Redis stores cached data.'],'existing_page_id':None,'related_ids':[],'conflicts':[]});value.content=json.dumps(r)
    return value
  job=self.job();model=Document();result=self.service.process_next(model,job_id=job['id']);self.assertEqual(result['status'],'needs_review',result['error'])
  plan=next(c for c in model.calls if c.get('mode')=='plan_core');self.assertFalse(any(t['title']=='Interview question list' for t in plan['analysis']['topics']))
  self.assertTrue(any(e['kind']=='source_classified' for e in self.service.execution.report(job['id'])['events']))

 def test_more_than_seven_pages_is_allowed_within_explicit_budget(self):
  class Many(WorkbenchLLM):
   def invoke(inner,messages):
    p=json.loads(messages[1].content)
    if p.get('mode')=='plan_core':
     evidence=p['evidence_registry'][0]['id'];return SimpleNamespace(content=json.dumps({'pages':[{'id':f'page-{i}','type':'concept','title':f'Concept {i}','tags':[],'links':[],'evidence_ids':[evidence],'conflicts':[]} for i in range(9)]}))
    return super().invoke(messages)
  job=self.job();result=self.service.process_next(Many(),job_id=job['id']);self.assertEqual(result['status'],'needs_review',result['error']);self.assertEqual(len(result['payload']['proposal']['pages']),10)

 def test_cached_source_recovery_calls_no_model_and_preserves_concepts(self):
  job=self.job();value=self.service.process_next(WorkbenchLLM(),job_id=job['id']);data=value['payload'];generated=copy.deepcopy(data['last_generated'])
  bad={'id':'bad-document','type':'entity','title':'Interview question list','body':'Source overview','tags':[],'links':[],'evidence_ids':generated['pages'][0]['evidence_ids'],'conflicts':[]}
  data['analyses'][0]['topics'].append({'type':'entity','title':bad['title'],'tags':[],'key_points':[],'quotes':['Redis stores cached data.'],'existing_page_id':None,'related_ids':[],'conflicts':[]})
  generated['pages'].append(bad)
  for page in generated['pages']:
   if page['type']=='concept':page['links'].append(bad['id'])
  original=UploadStore(self.service).receive('public.md',TEXT.encode())
  data['upload']={k:original[k] for k in ('id','path','digest','name')}
  data['last_generated']=generated;self.service.queue.save(job['id'],payload=data,status='failed',error='old failure')
  recovered=self.service.recover_cached(job['id']);self.assertEqual(recovered['status'],'needs_review')
  self.assertFalse(any(p['title']==bad['title'] for p in recovered['payload']['proposal']['pages']))
  self.assertEqual(self.service.execution.report(job['id'])['budget']['calls'],4) # original generation only

 def test_report_and_budget_are_owner_guarded(self):
  app=create_app(self.service,llm_factory=WorkbenchLLM,embedding_factory=lambda:SimpleNamespace(embeddings=FakeEmbeddings()))
  with TestClient(app) as client:
   client.get('/api/bootstrap');r=client.post('/api/knowledge/upload',headers=HEADERS,files=[('files',('public.md',TEXT.encode(),'text/markdown'))])
   import time
   for _ in range(200):
    upload=client.get('/api/knowledge/uploads').json()['uploads'][0]
    if upload.get('job') and upload['job']['index_status']=='ready':break
    time.sleep(.02)
   id=upload['job']['id'];self.assertEqual(client.get('/api/knowledge/jobs/'+id+'/execution').status_code,200)
   stranger=TestClient(app);stranger.get('/api/bootstrap');self.assertEqual(stranger.get('/api/knowledge/jobs/'+id+'/execution').status_code,404)
   self.assertEqual(stranger.post('/api/knowledge/jobs/'+id+'/budget',headers=HEADERS,json={'calls':128}).status_code,404)

if __name__=='__main__':unittest.main()
