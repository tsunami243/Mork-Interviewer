"""Automatic publication: additive old-page changes, provenance labels and bounded retries."""

import json
from threading import Lock, Thread
import time
from wiki_execution import event

from embedding_index import create_embedding_client
from wiki_index import sync_wiki_index
from wiki_jobs import MAX_ATTEMPTS
from wiki_publish import DIRECTORIES, PublishConflict, digest
from wiki_uploads import UploadStore


_MAX_AUTOMATIC_BODY = 60000


def additive_proposal(job):
    data = job['payload']
    proposal = json.loads(json.dumps(data['proposal'], ensure_ascii=False))
    snapshots = {item['id']:item for item in data.get('context',{}).get('pages',[])}
    marker_id = data['content_hash'][:12] + '-' + data['rules_hash'][:8]
    renamed = {}
    for page in proposal['pages']:
        snapshot = snapshots.get(page['id'])
        if page['action'] == 'update' and snapshot:
            page['title'] = snapshot['title']
            page['tags'] = list(dict.fromkeys([*snapshot['tags'], *page['tags']]))
            known = {item['id'] for item in data['context']['catalog']}
            page['links'] = list(dict.fromkeys([*(link for link in snapshot['links'] if link in known), *page['links']]))
        old, new = page['before_body'].strip(), page['body'].strip()
        if page['action'] != 'update' or not old:
            continue
        marker = f'<!-- WIKI CONTRIBUTION {marker_id} START -->'
        if marker in old or new == old or (new and new in old):
            page['body'] = old
            continue
        extra = new[len(old):].strip() if new.startswith(old) else new
        merged = old + f'\n\n{marker}\n## 新资料补充\n\n' + extra + f'\n<!-- WIKI CONTRIBUTION {marker_id} END -->'
        if len(merged) <= _MAX_AUTOMATIC_BODY:
            page['body'] = merged
        else:
            # Do not truncate old facts when a page grows too large: publish a linked supplement.
            previous = page['id']
            page['id'] = page['type'] + '-' + digest(previous + marker_id)[:24]
            page['path'] = f"{DIRECTORIES[page['type']]}/{page['id']}.md"
            page['title'] = page['title'][:92] + '（补充）'
            page['before_body'], page['base_hash'], page['action'] = '', None, 'create'
            page['links'] = list(dict.fromkeys([*page['links'], previous]))
            page['versioned_from'] = previous
            renamed[previous] = page['id']
    for page in proposal['pages']:
        parent = page.get('versioned_from')
        page['links'] = list(dict.fromkeys(link if link == parent else renamed.get(link, link) for link in page['links']))
        for old, new in renamed.items():
            if old != parent:
                page['body'] = page['body'].replace(f'[[{old}]]', f'[[{new}]]').replace(f'[[{old}|', f'[[{new}|')
    proposal['review_status'] = 'automatic'
    return proposal


class AutomaticWorker:
    def __init__(self, service, llm_factory, embedding_factory=create_embedding_client, *, retry_delay=2):
        self.service, self.llm_factory, self.embedding_factory = service, llm_factory, embedding_factory
        self.retry_delay = retry_delay
        self.thread = None
        self.lock = Lock()

    def start(self):
        with self.lock:
            if self.thread and self.thread.is_alive():
                return self.thread
            self.thread = Thread(target=self.run, name='wiki-intake-auto', daemon=True)
            self.thread.start()
            return self.thread

    def upload_state(self,job,status,error=''):
        id=job['payload'].get('upload',{}).get('id') if isinstance(job['payload'].get('upload'),dict) else None
        if id:
            UploadStore(self.service).update(id,status=status,error=error,job_id=job['id'])

    def sync_index(self,job):
        with self.service.execution.scope(job['id']):
            return self._sync_index(job)

    def _sync_index(self, job):
        payload = job['payload']
        receipt = payload['receipt']
        receipt['index_attempts'] = receipt.get('index_attempts', 0) + 1
        receipt['index_status'] = 'updating'
        self.service.queue.set_progress(job['id'],'index',0,1)
        self.service.queue.save(job['id'], payload=payload, status='completed', phase='index')
        started=time.monotonic();event('index','started',message='仅更新已发布知识索引；不重新生成正文')
        try:
            update = sync_wiki_index(self.embedding_factory(), root=self.service.root, directory=self.service.index_directory)
            receipt['index_status'] = 'ready'
            self.service.queue.set_progress(job['id'],'index',1,1)
            receipt['index_update'] = {'embedded_chunks':update.embedded_chunks,'embedded_queries':update.embedded_queries,'reused_chunks':update.reused_chunks}
            receipt.pop('index_error', None)
            self.upload_state(job,'completed')
            event('index','finished',elapsed_ms=round((time.monotonic()-started)*1000),embedded_chunks=update.embedded_chunks,reused_chunks=update.reused_chunks)
        except Exception as error:
            receipt['index_status'] = 'failed'
            receipt['index_error'] = type(error).__name__  # Never persist API exception text.
            event('index','failed',error_type=type(error).__name__,elapsed_ms=round((time.monotonic()-started)*1000))
            if receipt['index_attempts']>=3:self.upload_state(job,'failed','知识已保存，但索引更新失败：'+type(error).__name__)
        return self.service.queue.save(job['id'], payload=payload, status='completed', phase='published')

    def run_once(self):
        self.service.recover()
        uploads = UploadStore(self.service)
        record = uploads.claim_pending()
        if record:
            try:
                return uploads.parse_record(record)[0]
            except Exception:
                return {'status':'failed','payload':{},'upload_only':True}
        jobs = self.service.queue.automatic_jobs()
        for job in jobs:
            if job['payload'].get('auto_blocked'):
                continue
            if job['status'] in ('running','publishing'):
                continue
            if job['status'] == 'completed':
                return self.sync_index(job)
            if job['status'] == 'failed':
                self.service.queue.retry(job['id'])
                job = self.service.queue.get(job['id'])
            if job['status'] == 'queued':
                try:
                    llm = self.llm_factory()
                except Exception as error:
                    claimed = self.service.queue.claim(job['id'])
                    if claimed:
                        return self.service.queue.save(job['id'], status='failed', error='模型服务配置不可用：'+type(error).__name__)
                    return None
                job = self.service.process_next(llm, job_id=job['id'])
                if job is None or job['status'] != 'needs_review':
                    if job and job['status']=='failed' and job['payload'].get('stage')=='core':self.upload_state(job,'failed',job['error'])
                    return job
            if job['status'] == 'needs_review':
                try:
                    payload = job['payload']
                    payload['proposal'] = additive_proposal(job)
                    self.service.queue.save(job['id'], payload=payload, status='needs_review')
                    completed = self.service.approve(job['id'], acknowledge_conflicts=True)
                    return self.sync_index(completed)
                except Exception as error:
                    current = self.service.queue.get(job['id'])
                    payload = current['payload']
                    if (self.service.root / '.wiki-publishing.json').exists():
                        payload['auto_blocked'] = True
                    elif isinstance(error, PublishConflict):
                        for key in ('context','rules','proposal'):
                            payload.pop(key,None)
                        payload['analyses'] = []
                    message = str(error) if isinstance(error, ValueError) else '知识发布失败：'+type(error).__name__
                    failed=self.service.queue.save(job['id'],payload=payload,status='failed',error=message)
                    if payload.get('stage')=='core':self.upload_state(failed,'failed',message)
                    return failed
        return None

    def run(self):
        while True:
            try:
                job = self.run_once()
            except Exception:
                # A corrupt local queue should not terminate the Streamlit application.
                return
            if job is None:
                return
            if job['status'] == 'failed' or job['payload'].get('receipt',{}).get('index_status') == 'failed':
                time.sleep(self.retry_delay)


_WORKERS = {}
_WORKERS_LOCK = Lock()


def ensure_worker(service, llm_factory, embedding_factory=create_embedding_client):
    key = (str(service.root.resolve()), str(service.directory.resolve()), str(service.index_directory.resolve()))
    uploads = UploadStore(service)
    uploads.recover()
    if not service.queue.automatic_jobs() and not uploads.pending():
        return None
    with _WORKERS_LOCK:
        worker = _WORKERS.get(key)
        if worker is None:
            worker = AutomaticWorker(service, llm_factory, embedding_factory)
            _WORKERS[key] = worker
        worker.start()
    return worker


def wait_worker(service,timeout=5):
    key=(str(service.root.resolve()),str(service.directory.resolve()),str(service.index_directory.resolve()))
    with _WORKERS_LOCK:worker=_WORKERS.get(key)
    if worker and worker.thread and worker.thread.is_alive():worker.thread.join(timeout=timeout)
