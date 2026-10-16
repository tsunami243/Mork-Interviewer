"""Local FastAPI workbench: isolated interviews, explicit intake and read-only files."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass,field
import hashlib
import json
import os
from pathlib import Path
import re
from threading import Event,Lock,Thread
from time import perf_counter,time
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import FastAPI,File,Form,HTTPException,Request,Response,UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse,JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel,Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

ROOT=Path(__file__).resolve().parents[1]
load_dotenv(ROOT/'src'/'.env')
from document_loader import MAX_FILE_BYTES,SUPPORTED_SUFFIXES,DocumentLoadError,load_uploaded_document
from embedding_index import create_embedding_client
from model_config import configured_llm,configured_wiki_llm
from wiki_automatic import ensure_worker,wait_worker
from wiki_browser import GROUPS
from wiki_details import file_listing,file_details
from wiki_intake import IntakeService
from wiki_uploads import UploadStore
from wiki_actions import WikiActions
from wiki_relations import graph_data
from wiki_watcher import WikiChangeWatcher
from wiki_snapshot import get_snapshot, snapshot_status
from interview_session import PersistentCheckpoint, restore_interview
from interview_store import InterviewStore
from profile_store import ProfileStore


COOKIE='workbench_client'


class Expansion(BaseModel):
    upload_ids:list[str]=Field(min_length=1,max_length=20)


class FileSelection(BaseModel):
    paths:list[str]=Field(min_length=1,max_length=20)


class StageSelection(BaseModel):
    job_id:str=Field(pattern=r"^[a-f0-9]{32}$")
    stage:int=Field(ge=2,le=3)


class LinkSelection(BaseModel):
    ids:list[str]=Field(min_length=1,max_length=160)


class BudgetChange(BaseModel):
    pages:int|None=Field(default=None,ge=1,le=64)
    calls:int|None=Field(default=None,ge=1,le=512)
    tokens:int|None=Field(default=None,ge=1,le=2000000)
    seconds:int|None=Field(default=None,ge=1,le=7200)


class Answer(BaseModel):
    answer:str=Field(min_length=1,max_length=3000)
    request_id:str=Field(pattern=r'^[a-zA-Z0-9_-]{1,64}$')


@dataclass
class Session:
    id:str
    owner:str
    name:str
    max_turns:int
    created:float=field(default_factory=time)
    run:object=None
    busy:bool=True
    status:str='preparing'
    phase:str='准备面试'
    error:str=''
    public:dict=field(default_factory=dict)
    submissions:dict=field(default_factory=dict)
    profile_version_id:str|None=None
    lock:Lock=field(default_factory=Lock)

    def snapshot(self):
        with self.lock:
            return {**self.public,'id':self.id,'name':self.name,'status':self.status,'busy':self.busy,
                    'phase':self.phase,'error':self.error,'max_turns':self.max_turns,'profile_version_id':self.profile_version_id}


def owner(request):
    value=request.cookies.get(COOKIE,'')
    if not re.fullmatch(r'[a-f0-9]{32}',value):raise HTTPException(401,'请重新打开工作台')
    return hashlib.sha256(value.encode()).hexdigest()


def create_app(service=None,*,llm_factory=configured_wiki_llm,embedding_factory=create_embedding_client,
               interview_factory=None,frontend_dist=None):
    service=service or IntakeService()
    store=UploadStore(service)
    actions=WikiActions(service,llm_factory,embedding_factory)
    sessions={};sessions_lock=Lock()
    persistence=InterviewStore(service.directory / 'interviews.db')
    checkpoints=PersistentCheckpoint(service.directory / 'checkpoints.db')
    profiles=ProfileStore(service.directory / 'profile')
    executor=ThreadPoolExecutor(max_workers=2,thread_name_prefix='interview-api')
    dist=Path(frontend_dist or ROOT/'frontend'/'dist')
    with service.queue.connection() as db:
        db.execute('CREATE TABLE IF NOT EXISTS web_upload_owners(owner TEXT NOT NULL,upload_id TEXT NOT NULL,PRIMARY KEY(owner,upload_id))')

    @asynccontextmanager
    async def lifespan(app):
        service.recover();store.recover()
        ensure_worker(service,llm_factory,embedding_factory)
        get_snapshot(service.root, asynchronous=True)
        watcher=WikiChangeWatcher(service.root)
        stop_watcher=Event()
        def watch_wiki():
            while not stop_watcher.wait(3):
                try:
                    watcher.poll()
                except (OSError,ValueError,RuntimeError):
                    pass
        watcher_thread=Thread(target=watch_wiki,name='wiki-change-watcher',daemon=True)
        watcher_thread.start()
        yield
        stop_watcher.set(); watcher_thread.join(timeout=2)
        executor.shutdown(wait=True,cancel_futures=True)
        actions.close()
        wait_worker(service)
        checkpoints.close()

    app=FastAPI(title='Interview Workbench',lifespan=lifespan,docs_url=None,redoc_url=None)
    app.state.service=service;app.state.sessions=sessions;app.state.actions=actions
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=['127.0.0.1','localhost','testserver'])
    app.add_middleware(CORSMiddleware,allow_origins=['http://127.0.0.1:5173','http://localhost:5173'],
                       allow_credentials=True,allow_methods=['GET','POST','DELETE'],allow_headers=['Content-Type','X-Workbench-Request'])

    @app.middleware('http')
    async def guard(request,call_next):
        if request.url.path.startswith('/api/') and request.method not in ('GET','HEAD','OPTIONS'):
            limit=161*1024*1024 if request.url.path in ('/api/knowledge/stage','/api/knowledge/upload') else 18*1024*1024 if request.url.path=='/api/interviews' else 65536
            length=request.headers.get('content-length','0')
            if not length.isdigit() or int(length)>limit:
                return JSONResponse({'detail':'请求内容超过允许大小'},status_code=413)
            origin=request.headers.get('origin')
            allowed={str(request.base_url).rstrip('/'),'http://127.0.0.1:5173','http://localhost:5173'}
            if request.headers.get('x-workbench-request')!='1' or (origin and origin not in allowed):
                return JSONResponse({'detail':'拒绝未授权的跨站操作'},status_code=403)
        response=await call_next(request)
        if request.url.path.startswith('/api/'):
            response.headers['Cache-Control']='no-store'
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='no-referrer'
        response.headers['X-Frame-Options']='DENY'
        return response

    def hydrate(owner_id):
        from interview_graph import create_interview_graph
        rows=persistence.list(owner_id)
        for row in rows:
            if row['id'] in sessions: continue
            try:
                run=restore_interview(lambda: create_interview_graph(checkpointer=checkpoints.saver), row['id'])
            except Exception:
                continue
            record=Session(row['id'],row['owner'],row['name'],row['max_turns'],created=row['created_at'],run=run,
                           busy=False,status='interrupted' if row['status'] in ('processing','preparing') else row['status'],
                           phase='已恢复到上一完整轮次' if row['status'] in ('processing','preparing') else row['phase'],error=row['error'],profile_version_id=row['profile_version_id'])
            state=run.state
            record.public={'question':run.question,'turn_count':state.get('turn_count',row['last_complete_turn']),
                           'history':state.get('interview_history',[]),'evaluations':state.get('evaluation_history',[]),
                           'report':state.get('interview_report'),'metrics':state.get('stage_metrics',[])}
            sessions[row['id']]=record

    def session_for(request,id):
        who=owner(request)
        with sessions_lock:
            hydrate(who);record=sessions.get(id)
        if record is None or record.owner!=who:raise HTTPException(404,'会话不存在')
        return record

    def progress(record,event):
        names={'claim_extractor':'理解项目材料','topic_selector':'选择面试主题','question_retrieval':'检索面试线索',
               'question_finalizer':'准备问题','answer_evaluator':'评估回答','report_generator':'整理报告'}
        with record.lock:record.phase=names.get(event.get('stage'),'处理中')

    def publish_snapshot(record):
        state=record.run.state
        public={'question':record.run.question,'turn_count':state.get('turn_count',0),
                'history':state.get('interview_history',[]),'evaluations':state.get('evaluation_history',[]),
                'report':state.get('interview_report'),'metrics':state.get('stage_metrics',[])}
        with record.lock:
            record.public=public;record.busy=False;record.error=''
            record.status='waiting' if public['question'] else 'completed' if public['report'] else 'no_claims'
            record.phase='等待回答' if public['question'] else '面试结束'
            persistence.save(record.id,status=record.status,phase=record.phase,error='',turn=public['turn_count'],pending_answer=None,state=state,
                             question=(public['history'][-1].get('question') if public['history'] else None),
                             answer=(public['history'][-1].get('answer') if public['history'] else None))

    def begin(record,files):
        try:
            with record.lock:record.phase='读取项目材料'
            started=perf_counter()
            documents=[load_uploaded_document(name,content) for name,content in files]
            profile=profiles.save(documents[0],files[0][1])
            persistence.set_profile(record.id,profile.id,profiles.snapshot(profile.id))
            elapsed=round((perf_counter()-started)*1000)
            if interview_factory is None:
                from interview_session import start_interview
                from interview_graph import create_interview_graph
                record.run=start_interview(documents,record.max_turns,
                    graph_factory=lambda: create_interview_graph(checkpointer=checkpoints.saver),
                    profile_version_id=getattr(record,'profile_version_id',None),
                    document_ms=elapsed,on_progress=lambda event:progress(record,event))
            else:
                record.run=interview_factory(documents,record.max_turns,document_ms=elapsed,on_progress=lambda event:progress(record,event))
            publish_snapshot(record)
        except DocumentLoadError as error:
            with record.lock:record.busy=False;record.status='error';record.error=str(error)
        except Exception:
            with record.lock:record.busy=False;record.status='error';record.error='面试准备失败，请检查模型服务和材料后重新开始。'

    def resume(record,text):
        try:
            with record.lock:
                record.busy=True;record.status='processing';record.phase='评估回答'
                persistence.save(record.id,status='processing',phase='评估回答',turn=record.public.get('turn_count',0),pending_answer=text,state=record.run.state)
            record.run.resume(text,on_progress=lambda event:progress(record,event))
            publish_snapshot(record)
        except Exception:
            with record.lock:
                record.busy=False;record.status='interrupted';record.phase='已恢复到上一完整轮次';record.error='本轮处理被中断，回答已保留，可重新提交。'
                persistence.save(record.id,status='interrupted',phase=record.phase,error=record.error,turn=record.public.get('turn_count',0),pending_answer=text,state=record.run.state)

    async def read_upload(file):
        data=await file.read(MAX_FILE_BYTES+1)
        if not data or len(data)>MAX_FILE_BYTES:raise HTTPException(400,'每份文件必须有内容且不超过8MB')
        return data

    def owned_uploads(who):
        with service.queue.connection() as db:
            rows=db.execute('SELECT upload_id FROM web_upload_owners WHERE owner=?',(who,)).fetchall()
        return {row['upload_id'] for row in rows}

    def uploads_for(who):
        allowed=owned_uploads(who)
        output=[]
        for record in store.list(200):
            if record['id'] not in allowed:continue
            job=service.queue.display(record['job_id']) if record['job_id'] else None
            if job: job.update({'id':record['job_id'],'stage':record.get('build_stage','legacy')})
            output.append({'id':record['id'],'name':record['name'],'authorized':bool(record['authorized']),
                           'status':record['status'],'error':record['error'] or (job['error'] if job else ''),
                           'job':job})
        return output

    @app.get('/api/health')
    def health():return {'status':'ok','frontend':'react'}

    @app.get('/api/bootstrap')
    def bootstrap(request:Request,response:Response):
        value=request.cookies.get(COOKIE,'')
        if not re.fullmatch(r'[a-f0-9]{32}',value):value=uuid4().hex
        response.set_cookie(COOKIE,value,httponly=True,samesite='strict',max_age=7*86400)
        who=hashlib.sha256(value.encode()).hexdigest()
        with sessions_lock:
            hydrate(who);records=sorted((r for r in sessions.values() if r.owner==who),key=lambda r:r.created,reverse=True)
        return {'files':file_listing(service.root, get_snapshot(service.root, asynchronous=True)),'groups':[{'name':name,'english':english} for name,english in GROUPS],
                'sessions':[record.snapshot() for record in records],'uploads':uploads_for(who),
                'configured':all(os.getenv(key) for key in ('LLM_MODEL','LLM_API_KEY','LLM_BASE_URL'))}

    @app.get('/api/library')
    def library(request:Request):
        owner(request); snapshot=get_snapshot(service.root, asynchronous=True)
        return {'files':file_listing(service.root, snapshot, asynchronous=True), 'snapshot':snapshot_status(service.root)}

    @app.get('/api/library/file')
    def detail(request:Request,path:str):
        owner(request)
        try:return file_details(service.root,path,get_snapshot(service.root, asynchronous=True),asynchronous=True)
        except (OSError,ValueError):raise HTTPException(404,'该知识文件无法只读查看') from None

    @app.get('/api/knowledge/uploads')
    def upload_status(request:Request):return {'uploads':uploads_for(owner(request))}

    @app.post('/api/knowledge/stage')
    async def stage(request:Request,files:list[UploadFile]=File(...)):
        who=owner(request)
        if not 1<=len(files)<=20:raise HTTPException(400,'一次最多添加20份资料')
        selected=[]
        for file in files:
            data=await read_upload(file)
            try:record=store.receive(file.filename or 'unknown',data)
            except ValueError as error:raise HTTPException(400,str(error)) from None
            with service.queue.connection() as db:
                db.execute('INSERT OR IGNORE INTO web_upload_owners VALUES(?,?)',(who,record['id']))
            selected.append({'id':record['id'],'name':record['name'],'authorized':bool(record['authorized'])})
        return {'selected':selected,'uploads':uploads_for(who)}

    def authorize_core(who, selected):
        allowed=owned_uploads(who)
        selected=list(dict.fromkeys(selected))
        if any(id not in allowed for id in selected):raise HTTPException(404,'本次建库资料不存在')
        try:
            records=[store.get(id) for id in selected]
            for record in records:
                if record.get('build_stage')!='core' and record['job_id']:
                    previous=service.queue.get(record['job_id'])
                    if previous['status'] not in ('completed','failed','cancelled'):
                        raise ValueError('旧版任务尚未结束，请先完成后再按新规则补齐')
            with service.queue.connection() as db:
                db.execute('BEGIN IMMEDIATE')
                for record in records:
                    if record.get('build_stage')!='core' and record['job_id']:
                        db.execute("UPDATE uploads SET job_id=NULL,status='staged',error='',build_stage='core' WHERE id=? AND vault=?",(record['id'],service.queue.vault))
                        # A finished legacy publication remains intact, but cannot retry its old model plan.
                        previous=db.execute('SELECT status,payload FROM jobs WHERE id=? AND vault=?',(record['job_id'],service.queue.vault)).fetchone()
                        if not previous or previous['status'] not in ('completed','failed','cancelled'):
                            raise ValueError('旧版任务状态已变化，请稍后再补齐')
                        payload=json.loads(previous['payload']);payload['automatic']=False
                        db.execute('UPDATE jobs SET payload=? WHERE id=? AND vault=?',(json.dumps(payload,ensure_ascii=False),record['job_id'],service.queue.vault))
                    else:
                        db.execute("UPDATE uploads SET build_stage='core' WHERE id=? AND vault=? AND job_id IS NULL",(record['id'],service.queue.vault))
            for id in selected:store.authorize(id)
            ensure_worker(service,llm_factory,embedding_factory)
        except ValueError as error:raise HTTPException(400,str(error)) from None
        return {'started':len(selected),'uploads':uploads_for(who)}

    @app.post('/api/knowledge/upload',status_code=202)
    async def upload_and_build(request:Request,files:list[UploadFile]=File(...)):
        who=owner(request)
        if not 1<=len(files)<=20:raise HTTPException(400,'一次最多添加20份资料')
        validated=[]
        for file in files:
            content=await read_upload(file)
            name=file.filename or 'unknown'
            if len(name)>255 or Path(name).suffix.lower() not in SUPPORTED_SUFFIXES:
                raise HTTPException(400,'仅支持PDF、Markdown、DOC或DOCX资料')
            validated.append((name,content))
        selected=[]
        try:
            for name,content in validated:
                record=store.receive(name,content)
                with service.queue.connection() as db:
                    db.execute('INSERT OR IGNORE INTO web_upload_owners VALUES(?,?)',(who,record['id']))
                selected.append({'id':record['id'],'name':record['name'],'authorized':True})
            result=authorize_core(who,[r['id'] for r in selected])
        except ValueError as error:raise HTTPException(400,str(error)) from None
        return {**result,'selected':selected}

    @app.post('/api/knowledge/expand',status_code=202)
    def expand(request:Request,data:Expansion):
        return authorize_core(owner(request),data.upload_ids)

    def owned_job(request,id):
        who=owner(request)
        if not any(store.get(upload)['job_id']==id for upload in owned_uploads(who)):
            raise HTTPException(404,'建库任务不存在')
        return service.queue.get(id)

    @app.get('/api/knowledge/jobs/{id}/execution')
    def execution(request:Request,id:str,after:int=0):
        owned_job(request,id)
        return service.execution.report(id,max(0,after))

    @app.post('/api/knowledge/jobs/{id}/budget')
    def budget(request:Request,id:str,data:BudgetChange):
        job=owned_job(request,id)
        if job['status'] not in ('failed','needs_review'):
            raise HTTPException(409,'只能明确调整已停止任务的预算')
        values={k:v for k,v in data.model_dump().items() if v is not None}
        if not values:raise HTTPException(400,'请指定预算调整')
        try:service.execution.extend(id,values)
        except ValueError as error:raise HTTPException(400,str(error)) from None
        return service.execution.report(id)

    @app.get('/api/library/graph')
    def graph(request:Request):
        owner(request)
        return graph_data(service.root, get_snapshot(service.root, asynchronous=True), asynchronous=True)

    @app.get('/api/knowledge/activity')
    def activity(request:Request):
        who=owner(request)
        return {'pending':actions.tracker.scan(),'unlinked':actions.unlinked_pages(),'actions':actions.list(who)}

    @app.post('/api/knowledge/continue',status_code=202)
    def continue_stage(request:Request,data:StageSelection):
        who=owner(request)
        if not any(store.get(id)['job_id']==data.job_id for id in owned_uploads(who)):
            raise HTTPException(404,'建库任务不存在')
        try: return actions.start_stage(who,data.job_id,data.stage)
        except ValueError as error: raise HTTPException(409,str(error)) from None

    @app.post('/api/knowledge/relink',status_code=202)
    def relink(request:Request,data:FileSelection):
        try: return actions.start_links(owner(request),data.paths)
        except ValueError as error: raise HTTPException(409,str(error)) from None

    @app.post('/api/knowledge/relink/weak',status_code=202)
    def weak_relink(request:Request):
        try: return actions.start_weak_scan(owner(request))
        except ValueError as error: raise HTTPException(409,str(error)) from None

    @app.post('/api/knowledge/relink/dismiss')
    def dismiss_files(request:Request,data:FileSelection):
        owner(request)
        try:
            paths=[p['path'] for p in actions.tracker.checked(data.paths)]
            actions.tracker.state(paths,'dismissed')
            return {'dismissed':len(paths)}
        except ValueError as error: raise HTTPException(409,str(error)) from None

    @app.get('/api/knowledge/actions/{id}/execution')
    def action_execution(request:Request,id:str,after:int=0):
        try:actions.get(id,owner(request))
        except ValueError:raise HTTPException(404,'任务不存在') from None
        return service.execution.report(id,max(0,after))

    @app.post('/api/knowledge/actions/{id}/budget')
    def action_budget(request:Request,id:str,data:BudgetChange):
        try:
            row=actions.get(id,owner(request))
            if row['status']!='failed':raise ValueError('只能调整已停止任务的预算')
            service.execution.extend(id,{k:v for k,v in data.model_dump().items() if v is not None})
        except ValueError as error:raise HTTPException(409,str(error)) from None
        return service.execution.report(id)

    @app.post('/api/knowledge/actions/{id}/apply',status_code=202)
    def apply_links(request:Request,id:str,data:LinkSelection):
        try: return actions.apply_links(owner(request),id,data.ids)
        except ValueError as error: raise HTTPException(409,str(error)) from None

    @app.post('/api/knowledge/actions/{id}/dismiss')
    def dismiss_action(request:Request,id:str):
        try: return actions.dismiss(owner(request),id)
        except ValueError as error: raise HTTPException(409,str(error)) from None

    @app.get('/api/interviews')
    def interviews(request:Request):
        who=owner(request)
        with sessions_lock:
            hydrate(who);records=sorted((r for r in sessions.values() if r.owner==who),key=lambda r:r.created,reverse=True)
        return {'sessions':[record.snapshot() for record in records]}

    @app.get('/api/interviews/{id}')
    def interview(request:Request,id:str):return session_for(request,id).snapshot()

    @app.post('/api/interviews',status_code=202)
    async def start(request:Request,resume_file:UploadFile=File(...),project_file:UploadFile=File(...),max_turns:int=Form(5)):
        who=owner(request)
        if not 1<=max_turns<=8:raise HTTPException(400,'轮数须在1至8之间')
        data=[await read_upload(file) for file in (resume_file,project_file)]
        for file in (resume_file,project_file):
            if not file.filename or len(file.filename)>255 or Path(file.filename).suffix.lower() not in SUPPORTED_SUFFIXES:
                raise HTTPException(400,'仅支持PDF、Markdown、DOC或DOCX材料')
        with sessions_lock:
            own=sorted((r for r in sessions.values() if r.owner==who),key=lambda r:r.created)
            if sum(r.busy for r in sessions.values())>=12:raise HTTPException(429,'服务当前繁忙，请稍后再开始')
            if sum(r.busy for r in own)>=2:raise HTTPException(429,'已有两场面试正在处理，请稍后再开始')
            if len(own)>=6:
                idle=next((r for r in own if not r.busy),None)
                if idle:sessions.pop(idle.id,None)
            record=Session(uuid4().hex,who,Path(project_file.filename or '面试').stem[:60],max_turns)
            persistence.create(session_id=record.id,owner=who,name=record.name,max_turns=max_turns,
                               profile_version_id=None,profile_snapshot=None)
            sessions[record.id]=record
        executor.submit(begin,record,[(file.filename or 'unknown',content) for file,content in zip((resume_file,project_file),data)])
        return record.snapshot()

    @app.post('/api/interviews/{id}/answers',status_code=202)
    def answer(request:Request,id:str,data:Answer):
        record=session_for(request,id);text=data.answer.strip()
        if not text:raise HTTPException(400,'请先填写回答')
        key=hashlib.sha256(text.encode()).hexdigest()
        with record.lock:
            old=record.submissions.get(data.request_id)
            if old is not None:
                if old!=key:raise HTTPException(409,'同一提交编号不能对应不同回答')
                duplicate=True
            else:
                duplicate=False
                if record.busy:raise HTTPException(409,'上一轮仍在处理中')
                if record.status not in ('waiting','interrupted') or record.run is None:raise HTTPException(409,'当前没有可回答的问题')
                record.submissions[data.request_id]=key;record.busy=True;record.status='processing';record.phase='评估回答'
        if not duplicate:executor.submit(resume,record,text)
        return record.snapshot()

    @app.delete('/api/interviews/{id}')
    def delete_interview(request:Request,id:str):
        who=owner(request)
        with sessions_lock:
            record=sessions.get(id)
            if record is not None and record.owner!=who: raise HTTPException(404,'会话不存在')
            if not persistence.delete(id,who): raise HTTPException(404,'会话不存在')
            sessions.pop(id,None)
        return {'deleted':id}

    if (dist/'assets').is_dir():app.mount('/assets',StaticFiles(directory=dist/'assets'),name='assets')
    @app.get('/')
    def home():
        if not (dist/'index.html').is_file():return JSONResponse({'detail':'前端尚未构建，请在frontend执行npm run build'},status_code=503)
        return FileResponse(dist/'index.html')
    return app


app=create_app()
