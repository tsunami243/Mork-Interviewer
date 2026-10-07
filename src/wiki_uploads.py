"""Automatic private persistence of original uploads, independent of parsing/model success."""

import hashlib
import os
import psutil
from pathlib import Path, PureWindowsPath
import tempfile
import time

from document_loader import MAX_FILE_BYTES, SUPPORTED_SUFFIXES, load_uploaded_document


class UploadStore:
    def __init__(self, service):
        self.service = service
        self.root = service.directory / "uploads"
        self.root.mkdir(parents=True, exist_ok=True)
        with service.queue.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""CREATE TABLE IF NOT EXISTS uploads (
                id TEXT PRIMARY KEY, vault TEXT NOT NULL, name TEXT NOT NULL, path TEXT NOT NULL,
                digest TEXT NOT NULL, job_id TEXT, status TEXT NOT NULL, error TEXT NOT NULL DEFAULT '',
                created REAL NOT NULL, updated REAL NOT NULL
            )""")
            columns = {row['name'] for row in db.execute('PRAGMA table_info(uploads)')}
            if 'owner_pid' not in columns:
                db.execute('ALTER TABLE uploads ADD COLUMN owner_pid INTEGER NOT NULL DEFAULT 0')
            if 'lease_until' not in columns:
                db.execute('ALTER TABLE uploads ADD COLUMN lease_until REAL NOT NULL DEFAULT 0')
            if 'build_stage' not in columns:
                db.execute("ALTER TABLE uploads ADD COLUMN build_stage TEXT NOT NULL DEFAULT 'legacy'")
            if 'authorized' not in columns:
                db.execute('ALTER TABLE uploads ADD COLUMN authorized INTEGER NOT NULL DEFAULT 0')
            db.execute("CREATE TABLE IF NOT EXISTS wiki_settings(vault TEXT NOT NULL,key TEXT NOT NULL,value TEXT NOT NULL,PRIMARY KEY(vault,key))")
            migrated=db.execute("SELECT 1 FROM wiki_settings WHERE vault=? AND key='explicit_build_v4'",(service.queue.vault,)).fetchone()
            if not migrated:
                rows=db.execute("SELECT id,job_id FROM uploads WHERE vault=?",(service.queue.vault,)).fetchall()
                for row in rows:
                    job=db.execute("SELECT status,payload FROM jobs WHERE id=? AND vault=?",(row['job_id'],service.queue.vault)).fetchone() if row['job_id'] else None
                    if job and job['status']=='completed':
                        db.execute('UPDATE uploads SET authorized=1 WHERE id=?',(row['id'],))
                    else:
                        db.execute("UPDATE uploads SET authorized=0,status='staged',lease_until=0 WHERE id=?",(row['id'],))
                        if job:
                            import json
                            payload=json.loads(job['payload']); payload['automatic']=False
                            status='queued' if job['status']=='running' else job['status']
                            db.execute('UPDATE jobs SET payload=?,status=? WHERE id=?',(json.dumps(payload,ensure_ascii=False),status,row['job_id']))
                db.execute("INSERT INTO wiki_settings(vault,key,value) VALUES(?,'explicit_build_v4','1')",(service.queue.vault,))

    def persist(self, filename: str, data: bytes):
        if not isinstance(filename, str) or not filename.strip() or not isinstance(data, bytes) or not data:
            raise ValueError("请上传有内容的文件")
        name = PureWindowsPath(filename).name
        suffix = Path(name).suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES or len(data) > MAX_FILE_BYTES:
            raise ValueError("仅支持8MB内的PDF、MD、DOC、DOCX")
        digest = hashlib.sha256(data).hexdigest()
        file_id = hashlib.sha256((name + ':' + digest).encode()).hexdigest()
        path = self.root / f"{digest}{suffix}"
        if not path.resolve().is_relative_to(self.root.resolve()):
            raise ValueError("上传文件路径无效")
        if path.exists():
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError("原文件快照损坏，未覆盖它")
        else:
            fd, temporary = tempfile.mkstemp(prefix='.upload-', dir=self.root)
            try:
                with os.fdopen(fd, 'wb') as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        now = time.time()
        with self.service.queue.connection() as db:
            db.execute("""INSERT OR IGNORE INTO uploads(id,vault,name,path,digest,status,created,updated)
                       VALUES(?,?,?,?,?,'staged',?,?)""", (file_id, self.service.queue.vault, name, str(path), digest, now, now))
        return self.get(file_id)

    def get(self, file_id):
        with self.service.queue.connection() as db:
            row = db.execute("SELECT * FROM uploads WHERE id=? AND vault=?", (file_id, self.service.queue.vault)).fetchone()
        if row is None:
            raise ValueError("上传记录不存在")
        return dict(row)

    def update(self, file_id, *, status, error='', job_id=None):
        with self.service.queue.connection() as db:
            db.execute("UPDATE uploads SET status=?,error=?,job_id=COALESCE(?,job_id),updated=? WHERE id=? AND vault=?",
                       (status, error, job_id, time.time(), file_id, self.service.queue.vault))
        return self.get(file_id)

    def list(self, limit=30):
        with self.service.queue.connection() as db:
            rows = db.execute("SELECT * FROM uploads WHERE vault=? ORDER BY updated DESC LIMIT ?", (self.service.queue.vault, limit)).fetchall()
        return [dict(row) for row in rows]

    def receive(self, filename, data, *, authorize=False):
        record = self.persist(filename,data)
        return self.authorize(record['id']) if authorize else record

    def authorize(self, file_id):
        record = self.get(file_id)
        with self.service.queue.connection() as db:
            db.execute("UPDATE uploads SET authorized=1,status=CASE WHEN job_id IS NULL THEN 'saved' ELSE status END WHERE id=? AND vault=?",(file_id,self.service.queue.vault))
        record = self.get(file_id)
        if record['status'] == 'staged':
            with self.service.queue.connection() as db:
                db.execute("UPDATE uploads SET job_id=NULL,status='saved',error='' WHERE id=?",(file_id,))
        if record['job_id']:
            job = self.service.queue.get(record['job_id'])
            if job['status']=='failed' and job['payload'].get('stage')=='core':
                self.service.queue.retry(job['id'])
                self.update(file_id,status='processing')
            elif job['status'] == 'failed' and job['attempts'] >= 3:
                self.service.queue.cancel(job['id'])
                with self.service.queue.connection() as db:
                    db.execute("UPDATE uploads SET job_id=NULL,status='saved',error='' WHERE id=?", (record['id'],))
            elif job['status'] == 'completed' and job['payload'].get('receipt', {}).get('index_status') != 'ready':
                payload = job['payload']
                payload['receipt']['index_attempts'], payload['receipt']['index_status'] = 0, 'pending'
                self.service.queue.save(job['id'], payload=payload, status='completed')
        elif record['status'] == 'failed':
            self.update(record['id'], status='saved')
        return self.get(record['id'])

    def recover(self):
        with self.service.queue.connection() as db:
            rows = db.execute("SELECT id,owner_pid,lease_until FROM uploads WHERE vault=? AND status='parsing' AND authorized=1", (self.service.queue.vault,)).fetchall()
            for row in rows:
                if row['lease_until'] < time.time() or not psutil.pid_exists(row['owner_pid']):
                    db.execute("UPDATE uploads SET status='saved',owner_pid=0 WHERE id=?", (row['id'],))

    def pending(self):
        with self.service.queue.connection() as db:
            return db.execute("SELECT 1 FROM uploads WHERE vault=? AND status='saved' AND authorized=1 LIMIT 1", (self.service.queue.vault,)).fetchone() is not None

    def claim_pending(self):
        self.recover()
        with self.service.queue.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT * FROM uploads WHERE vault=? AND status='saved' AND authorized=1 ORDER BY created LIMIT 1", (self.service.queue.vault,)).fetchone()
            if row is None:
                return None
            db.execute("UPDATE uploads SET status='parsing',owner_pid=?,lease_until=?,updated=? WHERE id=?",
                       (os.getpid(), time.time()+900, time.time(), row['id']))
        return self.get(row['id'])

    def parse_record(self, record):
        try:
            record = self.get(record["id"])
            if not record["authorized"]:
                raise ValueError("资料尚未被明确授权用于建库")
            path = Path(record['path'])
            if not path.resolve().is_relative_to(self.root.resolve()):
                raise ValueError('原文件路径无效')
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != record['digest']:
                raise ValueError('原文件快照损坏，未处理')
            document = load_uploaded_document(record['name'], data)
            job, reused = self.service.enqueue(document, automatic=True, stage=record.get('build_stage','legacy'), upload={
                'id': record['id'], 'path': record['path'], 'digest': record['digest'], 'name': record['name']})
            self.update(record['id'], status='processing', job_id=job['id'])
            return job, reused
        except ValueError as error:
            self.update(record['id'], status='failed', error=str(error))
            raise
        except Exception:
            self.update(record['id'], status='failed', error='资料解析或保存失败；原文件已保留')
            raise

    def submit(self, filename, data):
        return self.parse_record(self.receive(filename, data, authorize=True))
