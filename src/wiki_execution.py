"""Durable, privacy-safe Wiki execution events, checkpoints and explicit work budgets."""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import os
import re
import time
from uuid import uuid4

ACTIVE = ContextVar('wiki_execution_scope',default=None)

class BudgetExceeded(ValueError): pass
class ValidationFailure(ValueError): pass


def limit(name,default,maximum):
    try:value=int(os.getenv(name,str(default)))
    except ValueError:raise ValueError(name+'必须是整数') from None
    if not 1<=value<=maximum:raise ValueError(name+'超出安全范围')
    return value


def settings():
    return {'calls':limit('WIKI_MAX_MODEL_CALLS',64,512),'tokens':limit('WIKI_MAX_TOKENS',100000,2000000),
            'seconds':limit('WIKI_MAX_ACTIVE_SECONDS',600,7200),'pages':limit('WIKI_MAX_PAGES',24,64),
            'concurrency':limit('WIKI_CONCURRENCY',3,8),'request_attempts':limit('WIKI_REQUEST_ATTEMPTS',2,4)}


class ExecutionStore:
    def __init__(self,queue):
        self.queue=queue
        with queue.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS wiki_events(seq INTEGER PRIMARY KEY AUTOINCREMENT,vault TEXT,task TEXT,session TEXT,stage TEXT,item TEXT,kind TEXT,created REAL,details TEXT)')
            db.execute('CREATE INDEX IF NOT EXISTS wiki_events_task ON wiki_events(vault,task,seq)')
            db.execute('CREATE TABLE IF NOT EXISTS wiki_checkpoints(vault TEXT,task TEXT,key TEXT,stage TEXT,item TEXT,payload TEXT,updated REAL,PRIMARY KEY(vault,task,key))')
            db.execute('CREATE TABLE IF NOT EXISTS wiki_budgets(vault TEXT,task TEXT,limits TEXT,calls INTEGER DEFAULT 0,tokens INTEGER DEFAULT 0,unknown_usage INTEGER DEFAULT 0,elapsed REAL DEFAULT 0,PRIMARY KEY(vault,task))')

    def event(self,task,stage,kind,*,item='',session='',**details):
        allowed={'message','code','count','total','elapsed_ms','input_tokens','output_tokens','usage_known','response_shape','cached','error_type','relation_count','embedded_chunks','reused_chunks','attempt','limits','model','provider','title','call_id','checkpoint_key'}
        public={k:v for k,v in details.items() if k in allowed}
        def redact(value):
            if isinstance(value,str):
                for secret in (os.getenv('LLM_API_KEY',''),os.getenv('DASHSCOPE_API_KEY','')):
                    if len(secret)>6:value=value.replace(secret,'[redacted]')
                return re.sub(r'\bsk-[A-Za-z0-9_-]{8,}\b','[redacted]',value)
            if isinstance(value,dict):return {k:redact(v) for k,v in value.items()}
            if isinstance(value,list):return [redact(v) for v in value]
            return value
        public=redact(public);item=redact(str(item))
        with self.queue.connection() as db:
            db.execute('INSERT INTO wiki_events(vault,task,session,stage,item,kind,created,details) VALUES(?,?,?,?,?,?,?,?)',
                (self.queue.vault,task,session,stage,str(item)[:255],kind,time.time(),json.dumps(public,ensure_ascii=False)))

    def checkpoint(self,task,key,stage,item,value=None):
        with self.queue.connection() as db:
            if value is not None:
                db.execute('INSERT OR REPLACE INTO wiki_checkpoints VALUES(?,?,?,?,?,?,?)',
                    (self.queue.vault,task,key,stage,item,json.dumps(value,ensure_ascii=False),time.time()))
                return value
            row=db.execute('SELECT payload FROM wiki_checkpoints WHERE vault=? AND task=? AND key=?',(self.queue.vault,task,key)).fetchone()
        return json.loads(row[0]) if row else None

    def report(self,task,after=0):
        with self.queue.connection() as db:
            rows=db.execute('SELECT seq,session,stage,item,kind,created,details FROM wiki_events WHERE vault=? AND task=? AND seq>? ORDER BY seq LIMIT 400',(self.queue.vault,task,after)).fetchall()
            budget=db.execute('SELECT * FROM wiki_budgets WHERE vault=? AND task=?',(self.queue.vault,task)).fetchone()
        return {'events':[{**{k:r[k] for k in ('seq','session','stage','item','kind','created')},'details':json.loads(r['details'])} for r in rows],
            'budget':{'limits':json.loads(budget['limits']),'calls':budget['calls'],'tokens':budget['tokens'],'unknown_usage':budget['unknown_usage'],'elapsed_seconds':round(budget['elapsed'],2)} if budget else None}

    @contextmanager
    def scope(self,task):
        current=ACTIVE.get()
        if current and current['task']==task:
            yield current;return
        limits=settings();session=uuid4().hex;started=time.monotonic()
        with self.queue.connection() as db:
            db.execute('INSERT OR IGNORE INTO wiki_budgets(vault,task,limits) VALUES(?,?,?)',(self.queue.vault,task,json.dumps(limits)))
        ctx={'store':self,'task':task,'session':session,'started':started}
        token=ACTIVE.set(ctx);self.event(task,'execution','session_started',session=session,limits=limits)
        try:yield ctx
        finally:
            elapsed=time.monotonic()-started
            with self.queue.connection() as db:
                db.execute('UPDATE wiki_budgets SET elapsed=elapsed+? WHERE vault=? AND task=?',(elapsed,self.queue.vault,task))
            self.event(task,'execution','session_finished',session=session,elapsed_ms=round(elapsed*1000));ACTIVE.reset(token)

    def reserve(self,ctx):
        with self.queue.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM wiki_budgets WHERE vault=? AND task=?',(self.queue.vault,ctx['task'])).fetchone();limits=json.loads(row['limits'])
            elapsed=row['elapsed']+time.monotonic()-ctx['started']
            if row['calls']>=limits['calls'] or row['tokens']>=limits['tokens'] or elapsed>=limits['seconds']:
                raise BudgetExceeded('已达到本任务调用/token/活动耗时预算；保留检查点，等待明确调整预算或恢复')
            db.execute('UPDATE wiki_budgets SET calls=calls+1 WHERE vault=? AND task=?',(self.queue.vault,ctx['task']))
        return limits

    def extend(self,task,new_limits):
        # Explicit owner-authorized increases only; never reset spent usage.
        limits=settings()
        if set(new_limits)-{'calls','tokens','seconds','pages'}:raise ValueError('只能调整调用、token和耗时预算')
        with self.queue.connection() as db:
            db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT limits FROM wiki_budgets WHERE vault=? AND task=?',(self.queue.vault,task)).fetchone()
            if not row:raise ValueError('任务预算尚未建立')
            old=json.loads(row[0])
            for key,value in new_limits.items():
                cap={'calls':512,'tokens':2000000,'seconds':7200,'pages':64}[key]
                if type(value) is not int or not old[key]<=value<=cap:raise ValueError('预算只能在安全范围内增加')
                old[key]=value
            db.execute('UPDATE wiki_budgets SET limits=? WHERE vault=? AND task=?',(json.dumps(old),self.queue.vault,task))
        self.event(task,'execution','budget_extended',limits=old)


def effective_settings():
    ctx=ACTIVE.get()
    if not ctx:return settings()
    with ctx['store'].queue.connection() as db:
        row=db.execute('SELECT limits FROM wiki_budgets WHERE vault=? AND task=?',(ctx['store'].queue.vault,ctx['task'])).fetchone()
    return json.loads(row[0]) if row else settings()


def event(stage,kind,**details):
    ctx=ACTIVE.get()
    if ctx:ctx['store'].event(ctx['task'],stage,kind,session=ctx['session'],**details)


def invoke_traced(llm,instructions,payload):
    from langchain_core.messages import HumanMessage,SystemMessage
    from json_utils import parse_json_object
    from telemetry import invoke_model
    ctx=ACTIVE.get();mode=payload.get('mode','analysis' if 'chunk' in payload else 'compile_legacy')
    item=str(payload.get('page_plan',{}).get('id',payload.get('chunk',{}).get('id',payload.get('source',{}).get('path','') if isinstance(payload.get('source'),dict) else '')))
    from urllib.parse import urlparse
    model=str(getattr(llm,'model_name',type(llm).__name__))
    provider=urlparse(str(getattr(llm,'openai_api_base',''))).hostname or type(llm).__name__
    key=hashlib.sha256(json.dumps(['execution-v1',model,provider,instructions,payload],ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    if ctx:
        cached=ctx['store'].checkpoint(ctx['task'],key,mode,item)
        if cached is not None:event(mode,'cache_reused',item=item,cached=True,checkpoint_key=key[:16]);return cached
    attempts=settings()['request_attempts'] if ctx else 1
    for attempt in range(1,attempts+1):
        if ctx:ctx['store'].reserve(ctx)
        started=time.monotonic();call_id=uuid4().hex;event(mode,'call_started',item=item,attempt=attempt,model=model,provider=provider,call_id=call_id,checkpoint_key=key[:16])
        try:response=invoke_model(llm,[SystemMessage(content=instructions),HumanMessage(content=json.dumps(payload,ensure_ascii=False))])
        except Exception as error:
            transient=type(error).__name__ in ('APITimeoutError','APIConnectionError','RateLimitError','InternalServerError')
            event(mode,'call_failed',item=item,call_id=call_id,error_type=type(error).__name__,elapsed_ms=round((time.monotonic()-started)*1000),code='TRANSIENT_PROVIDER' if transient else 'PROVIDER_FAILURE')
            if transient and attempt<attempts:time.sleep(min(attempt,2));continue
            raise RuntimeError('模型调用失败：'+type(error).__name__) from None
        usage=getattr(response,'usage_metadata',None) or (getattr(response,'response_metadata',None) or {}).get('token_usage') or {}
        known=bool(usage);input_tokens=int(usage.get('input_tokens') or usage.get('prompt_tokens') or 0);output_tokens=int(usage.get('output_tokens') or usage.get('completion_tokens') or 0)
        if ctx:
            with ctx['store'].queue.connection() as db:
                db.execute('UPDATE wiki_budgets SET tokens=tokens+?,unknown_usage=unknown_usage+? WHERE vault=? AND task=?',
                    (input_tokens+output_tokens,0 if known else 1,ctx['store'].queue.vault,ctx['task']))
        try:result=parse_json_object(response.content)
        except ValueError:
            event(mode,'validation_failed',item=item,call_id=call_id,code='JSON_FORMAT',elapsed_ms=round((time.monotonic()-started)*1000))
            raise ValidationFailure('模型响应不是有效JSON对象，需要局部格式修复') from None
        shape={'pages':len(result['pages']) if isinstance(result.get('pages'),list) else 'missing_or_wrong_type'} if mode.startswith('plan_') else {'page':isinstance(result.get('page'),dict),'single_pages':isinstance(result.get('pages'),list) and len(result['pages'])==1}
        event(mode,'call_finished',item=item,call_id=call_id,elapsed_ms=round((time.monotonic()-started)*1000),input_tokens=input_tokens,output_tokens=output_tokens,usage_known=known,response_shape=shape)
        if ctx:ctx['store'].checkpoint(ctx['task'],key,mode,item,result)
        return result
