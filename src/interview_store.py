"""SQLite persistence for interview sessions, event checkpoints and messages."""
from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = Path(os.getenv("INTERVIEW_DB", str(ROOT / "data" / "private" / "interviews.db"))).expanduser()

SCHEMA = """
CREATE TABLE IF NOT EXISTS interview_sessions (
  id TEXT PRIMARY KEY, owner TEXT NOT NULL, name TEXT NOT NULL, max_turns INTEGER NOT NULL,
  status TEXT NOT NULL, phase TEXT NOT NULL, error TEXT NOT NULL DEFAULT '',
  profile_version_id TEXT, profile_snapshot TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL,
  last_complete_turn INTEGER NOT NULL DEFAULT 0, pending_answer TEXT
);
CREATE INDEX IF NOT EXISTS idx_interview_sessions_owner ON interview_sessions(owner, updated_at DESC);
CREATE TABLE IF NOT EXISTS interview_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, event_type TEXT NOT NULL,
  payload TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS interview_messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, turn_no INTEGER NOT NULL,
  question TEXT NOT NULL, answer TEXT NOT NULL, status TEXT NOT NULL, created_at REAL NOT NULL,
  UNIQUE(session_id, turn_no)
);
"""


class InterviewStore:
    def __init__(self, database: Path | None = None):
        self.database = Path(database or DEFAULT_DB).expanduser()
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.database, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        try:
            yield db
        finally:
            db.close()
    def _initialize(self) -> None:
        with self.connection() as db:
            db.executescript(SCHEMA)
            db.commit()

    def create(self, *, session_id: str, owner: str, name: str, max_turns: int,
               profile_version_id: str | None, profile_snapshot: dict | None) -> None:
        now = time.time()
        with self.connection() as db:
            db.execute("""INSERT INTO interview_sessions(id,owner,name,max_turns,status,phase,error,profile_version_id,profile_snapshot,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (session_id, owner, name, max_turns, "preparing", "准备面试", "", profile_version_id,
                 json.dumps(profile_snapshot, ensure_ascii=False) if profile_snapshot else None, now, now))
            self._event(db, session_id, "created", {"profile_version_id": profile_version_id})
            db.commit()

    def set_profile(self, session_id: str, version_id: str, snapshot: dict | None) -> None:
        with self.connection() as db:
            db.execute("UPDATE interview_sessions SET profile_version_id=?,profile_snapshot=?,updated_at=? WHERE id=?",
                       (version_id, json.dumps(snapshot, ensure_ascii=False) if snapshot else None, time.time(), session_id))
            db.commit()

    def save(self, session_id: str, *, status: str, phase: str, error: str = "", turn: int = 0,
             pending_answer: str | None = None, state: dict | None = None,
             question: str | None = None, answer: str | None = None) -> None:
        now = time.time()
        with self.connection() as db:
            db.execute("UPDATE interview_sessions SET status=?,phase=?,error=?,updated_at=?,last_complete_turn=?,pending_answer=? WHERE id=?",
                       (status, phase, error, now, turn, pending_answer, session_id))
            event_type = {"processing": "answer_submitted", "waiting": "checkpoint", "completed": "completed", "interrupted": "recovered"}.get(status, "state_changed")
            self._event(db, session_id, event_type, {"status": status, "phase": phase, "turn": turn, "state": state or {}})
            if question is not None and answer is not None:
                db.execute("""INSERT INTO interview_messages(session_id,turn_no,question,answer,status,created_at) VALUES(?,?,?,?,?,?)
                    ON CONFLICT(session_id,turn_no) DO UPDATE SET question=excluded.question,answer=excluded.answer,status=excluded.status,created_at=excluded.created_at""",
                    (session_id, turn, question, answer, "complete" if status == "waiting" else "pending", now))
            db.commit()

    def _event(self, db: sqlite3.Connection, session_id: str, event_type: str, payload: dict) -> None:
        db.execute("INSERT INTO interview_events(session_id,event_type,payload,created_at) VALUES(?,?,?,?)",
                   (session_id, event_type, json.dumps(payload, ensure_ascii=False), time.time()))

    def get(self, session_id: str, owner: str | None = None) -> dict | None:
        with self.connection() as db:
            query = "SELECT * FROM interview_sessions WHERE id=?"
            args: list[Any] = [session_id]
            if owner is not None:
                query += " AND owner=?"; args.append(owner)
            row = db.execute(query, args).fetchone()
            if row is None:
                return None
            messages = [dict(item) for item in db.execute("SELECT turn_no,question,answer,status,created_at FROM interview_messages WHERE session_id=? ORDER BY turn_no", (session_id,)).fetchall()]
            events = [dict(item) for item in db.execute("SELECT event_type,payload,created_at FROM interview_events WHERE session_id=? ORDER BY id DESC LIMIT 30", (session_id,)).fetchall()]
            result = dict(row)
            result["profile_snapshot"] = json.loads(result["profile_snapshot"]) if result["profile_snapshot"] else None
            result["messages"] = messages
            result["events"] = events
            return result

    def list(self, owner: str) -> list[dict]:
        with self.connection() as db:
            ids = [row["id"] for row in db.execute("SELECT id FROM interview_sessions WHERE owner=? ORDER BY updated_at DESC", (owner,)).fetchall()]
        return [self.get(session_id, owner) for session_id in ids]

    def delete(self, session_id: str, owner: str) -> bool:
        with self.connection() as db:
            row = db.execute("SELECT 1 FROM interview_sessions WHERE id=? AND owner=?", (session_id, owner)).fetchone()
            if row is None:
                return False
            db.execute("DELETE FROM interview_messages WHERE session_id=?", (session_id,))
            db.execute("DELETE FROM interview_events WHERE session_id=?", (session_id,))
            db.execute("DELETE FROM interview_sessions WHERE id=?", (session_id,))
            db.commit()
            return True

    def recoverable(self, owner: str | None = None) -> list[dict]:
        rows = self.list(owner) if owner is not None else []
        for row in rows:
            if row["status"] in ("preparing", "processing"):
                row["status"] = "interrupted"
                row["phase"] = "已恢复到上一完整轮次"
        return rows
