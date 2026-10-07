"""Deterministic local providers for staged workbench tests; no network or keys."""
import json
import re
from threading import Lock
from time import sleep
from types import SimpleNamespace


class WorkbenchLLM:
    def __init__(self):
        self.calls=[];self.lock=Lock();self.active=0;self.peak=0

    def invoke(self,messages):
        payload=json.loads(messages[1].content)
        with self.lock: self.calls.append(payload)
        mode=payload.get('mode')
        if 'chunk' in payload:
            text=payload['chunk']['text'];topics=[]
            for kind,title,quote in [('concept','Redis Cache','Redis stores cached data.'),('entity','Meituan','Meituan operates a platform.')]:
                if quote not in text: continue
                topics.append({'type':kind,'title':title,'tags':['Redis'] if kind=='concept' else ['Meituan'],
                    'key_points':[quote],'quotes':[quote],'existing_page_id':None,'related_ids':[],'conflicts':[]})
            reply={'summary':text[:1200],'topics':topics}
        elif mode=='plan_core':
            pages=[]
            for i,topic in enumerate(payload['analysis']['topics']):
                pages.append({'id':f'plan-{i}','type':topic['type'],'title':topic['title'],'tags':topic['tags'],
                    'aliases':[],'links':[],'evidence_ids':topic['evidence_ids'][:1],'conflicts':[]})
            for page in pages:page['links']=[p['id'] for p in pages if p is not page]
            reply={'pages':pages}
        elif mode=='compile_page':
            with self.lock:self.active+=1;self.peak=max(self.peak,self.active)
            sleep(.035)
            page=dict(payload['page_plan']);page['body']='资料中的知识：'+payload['evidence_registry'][0]['quote']
            with self.lock:self.active-=1
            reply={'page':page}
        elif mode=='plan_enrich':
            evidence=payload['analysis']['topics'][0]['evidence_ids'][0]
            reply={'pages':[{'id':'question-extra','type':'question','title':'缓存设计追问','tags':['Redis'],'links':[],
                'evidence_ids':[evidence],'conflicts':[]},{'id':'synthesis-extra','type':'synthesis','title':'缓存知识综合',
                'tags':['Redis'],'links':['question-extra'],'evidence_ids':[evidence],'conflicts':[]}]}
        elif mode=='navigation':
            reply={'sections':[{'heading':'已有知识导航','paths':[p['path'] for p in payload['catalog'] if p['type'] in ('entity','concept','source')]}]}
        elif mode=='relink':
            source=payload['source']['body'];links=[]
            target=next((p for p in payload['candidates'] if 'Redis' in p['body']),None)
            if target and 'Redis' in source:
                quote=next((line for line in source.splitlines() if 'Redis' in line), 'Redis')
                target_quote=next((line for line in target['body'].splitlines() if 'Redis' in line),'Redis')
                links=[{'target':target['path'],'type':'concept','reason':'新增资料讨论Redis缓存，目标页提供对应知识。',
                    'source_quote':quote[:500],'target_quote':target_quote[:500]}]
            reply={'links':links}
        else:
            evidence=payload['analysis']['topics'][0]['evidence_ids'][0]
            reply={'pages':[{'id':'question-extra','type':'question','title':'缓存设计追问','body':'先讲设计动机，再解释取舍。',
                'tags':['Redis'],'links':[],'evidence_ids':[evidence],'conflicts':[]},
                {'id':'synthesis-extra','type':'synthesis','title':'缓存知识综合','body':'基于来源进行知识综合，不冒充候选人的项目事实。',
                'tags':['Redis'],'links':['question-extra'],'evidence_ids':[evidence],'conflicts':[]}]}
        return SimpleNamespace(content=json.dumps(reply,ensure_ascii=False))
