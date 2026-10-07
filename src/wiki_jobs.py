"""Private SQLite intake queue: deduplication, leases, cancellation and checkpoints."""

from contextlib import contextmanager
import json
import os
import psutil
from pathlib import Path
import sqlite3
import time
from uuid import uuid4


STATE_DIR = Path(__file__).resolve().parents[1] / "data" / "private" / "wiki-ingest"
MAX_ATTEMPTS = 3
LEASE_SECONDS = 900


class JobQueue:
    def __init__(self, directory: Path, vault: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "jobs.sqlite3"
        self.vault = str(vault.resolve())
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, vault TEXT NOT NULL, dedup_key TEXT NOT NULL,
                status TEXT NOT NULL, phase TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                cancel_requested INTEGER NOT NULL DEFAULT 0, lease_until REAL NOT NULL DEFAULT 0,
                payload TEXT NOT NULL, error TEXT NOT NULL DEFAULT '', created REAL NOT NULL, updated REAL NOT NULL
            )""")
            columns = {row["name"] for row in db.execute("PRAGMA table_info(jobs)")}
            if "progress" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN progress TEXT NOT NULL DEFAULT '{}'")
            if "owner_pid" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN owner_pid INTEGER NOT NULL DEFAULT 0")
            db.execute("CREATE INDEX IF NOT EXISTS jobs_vault_status ON jobs(vault,status,created)")

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def decode(row):
        if row is None:
            return None
        result = dict(row)
        result["payload"] = json.loads(result["payload"])
        result["progress"] = json.loads(result.get("progress","{}"))
        result["cancel_requested"] = bool(result["cancel_requested"])
        return result

    def get(self, job_id: str):
        with self.connection() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=? AND vault=?", (job_id, self.vault)).fetchone()
        if row is None:
            raise ValueError("建库任务不存在")
        return self.decode(row)

    def display(self, job_id):
        with self.connection() as db:
            row = db.execute("""SELECT status,phase,attempts,error,progress,
                json_extract(payload,'$.receipt.index_status') AS index_status,
                json_extract(payload,'$.receipt.index_attempts') AS index_attempts,
                json_array_length(json_extract(payload,'$.receipt.page_ids')) AS pages,
                json_extract(payload,'$.proposal.coverage_warning') AS warning
                FROM jobs WHERE id=? AND vault=?""", (job_id,self.vault)).fetchone()
        if not row:return None
        result=dict(row);result["progress"]=json.loads(result["progress"])
        return result

    def set_progress(self, job_id, stage, completed=0, total=1):
        if stage not in ('analysis','planning','generation','integration','index'):
            raise ValueError('未知进度阶段')
        if type(completed) is not int or type(total) is not int or not 0<=completed<=total:
            raise ValueError('进度计数无效')
        progress={'stage':stage,'completed':completed,'total':total}
        with self.connection() as db:
            db.execute('UPDATE jobs SET progress=?,updated=? WHERE id=? AND vault=?',
                (json.dumps(progress),time.time(),job_id,self.vault))

    def list(self, limit=30):
        with self.connection() as db:
            rows = db.execute("SELECT * FROM jobs WHERE vault=? ORDER BY created DESC LIMIT ?", (self.vault, limit)).fetchall()
        return [self.decode(row) for row in rows]

    def enqueue(self, dedup_key: str, payload: dict, *, force=False):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT * FROM jobs WHERE vault=? AND dedup_key=? AND status!='cancelled' ORDER BY created DESC LIMIT 1", (self.vault, dedup_key)).fetchone()
            if old and (not force or old["status"] != "completed"):
                return self.decode(old), True
            now, job_id = time.time(), uuid4().hex
            db.execute("INSERT INTO jobs(id,vault,dedup_key,status,phase,payload,created,updated) VALUES(?,?,?,'queued','analysis',?,?,?)",
                       (job_id, self.vault, dedup_key, json.dumps(payload, ensure_ascii=False), now, now))
        return self.get(job_id), False

    def automatic_jobs(self, limit=100):
        with self.connection() as db:
            rows = db.execute("""SELECT * FROM jobs WHERE vault=?
                AND json_extract(payload,'$.automatic')=1
                AND (status IN ('queued','running','publishing','needs_review')
                     OR (status='failed' AND attempts<3 AND COALESCE(json_extract(payload,'$.stage'),'legacy')!='core')
                     OR (status='completed' AND json_extract(payload,'$.receipt.index_status')!='ready'
                         AND COALESCE(json_extract(payload,'$.receipt.index_attempts'),0)<3))
                ORDER BY created LIMIT ?""", (self.vault, limit)).fetchall()
        return [self.decode(row) for row in rows]

    def claim(self, job_id: str | None = None):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            now = time.time()
            # Even an expired worker must be explicitly recovered before another starts.
            if db.execute("SELECT 1 FROM jobs WHERE vault=? AND status IN ('running','publishing')", (self.vault,)).fetchone():
                return None
            sql = "SELECT * FROM jobs WHERE vault=? AND status='queued'"
            args = [self.vault]
            if job_id:
                sql += " AND id=?"
                args.append(job_id)
            row = db.execute(sql + " ORDER BY created LIMIT 1", args).fetchone()
            if row is None:
                return None
            if row["attempts"] >= MAX_ATTEMPTS and json.loads(row["payload"]).get("stage")!="core":
                db.execute("UPDATE jobs SET status='failed',error='已达到三次处理上限',updated=? WHERE id=?", (now, row["id"]))
                return None
            db.execute("UPDATE jobs SET status='running',attempts=attempts+1,lease_until=?,updated=?,owner_pid=? WHERE id=?", (now + LEASE_SECONDS, now, os.getpid(), row["id"]))
            claimed_id = row["id"]
        return self.get(claimed_id)

    def save(self, job_id: str, *, payload=None, status=None, phase=None, error=None):
        fields, values = ["updated=?"], [time.time()]
        for key, value in (("payload", payload), ("status", status), ("phase", phase), ("error", error)):
            if value is not None:
                fields.append(key + "=?")
                values.append(json.dumps(value, ensure_ascii=False) if key == "payload" else value)
        fields.append("lease_until=?")
        values.append(time.time() + LEASE_SECONDS if status in (None, "running", "publishing") else 0)
        with self.connection() as db:
            db.execute("UPDATE jobs SET " + ",".join(fields) + " WHERE id=? AND vault=?", (*values, job_id, self.vault))
        return self.get(job_id)

    def begin_publish(self, job_id: str, *, payload=None):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE id=? AND vault=?", (job_id, self.vault)).fetchone()
            if row is None or row["status"] != "needs_review":
                raise ValueError("只有待审核任务可以发布")
            if db.execute("SELECT 1 FROM jobs WHERE vault=? AND status IN ('running','publishing')", (self.vault,)).fetchone():
                raise ValueError("当前有任务正在处理，请稍后再发布")
            if payload is not None:
                db.execute("UPDATE jobs SET payload=? WHERE id=?", (json.dumps(payload, ensure_ascii=False), job_id))
            now = time.time()
            db.execute("UPDATE jobs SET status='publishing',phase='publish',lease_until=?,updated=?,owner_pid=? WHERE id=?", (now + LEASE_SECONDS, now, os.getpid(), job_id))
        return self.get(job_id)

    def cancel(self, job_id: str):
        job = self.get(job_id)
        if job["status"] == "completed":
            raise ValueError("已发布的知识不能通过取消任务删除")
        with self.connection() as db:
            db.execute("UPDATE jobs SET cancel_requested=1,status=CASE WHEN status='running' THEN status ELSE 'cancelled' END,updated=? WHERE id=? AND vault=? AND status NOT IN ('publishing','completed')",
                       (time.time(), job_id, self.vault))
        return self.get(job_id)

    def retry(self, job_id: str):
        job = self.get(job_id)
        if job["status"] != "failed" or (job["attempts"] >= MAX_ATTEMPTS and job["payload"].get("stage")!="core"):
            raise ValueError("只能重试尚未达到三次上限的失败任务")
        return self.save(job_id, status="queued", error="")

    def regenerate(self, job_id: str):
        job = self.get(job_id)
        if job["status"] != "needs_review":
            raise ValueError("只能重新分析待审核任务")
        data = job["payload"]
        for key in ("analyses", "proposal", "context", "rules"):
            data.pop(key, None)
        with self.connection() as db:
            db.execute("UPDATE jobs SET attempts=0 WHERE id=? AND vault=?", (job_id, self.vault))
        return self.save(job_id, payload=data, status="queued", phase="analysis", error="")

    def expired(self):
        with self.connection() as db:
            rows = db.execute("SELECT * FROM jobs WHERE vault=? AND status IN ('running','publishing')", (self.vault,)).fetchall()
        return [self.decode(row) for row in rows if row["lease_until"] < time.time() or (row["owner_pid"] > 0 and not psutil.pid_exists(row["owner_pid"]))]
