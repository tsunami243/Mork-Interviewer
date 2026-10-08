"""Rebuildable SQLite metadata derived from the Markdown Wiki."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path

from wiki_store import WIKI_ROOT, WikiPage, list_pages

DEFAULT_DB = Path(os.getenv("WIKI_METADATA_DB", str(Path(__file__).resolve().parents[1] / "data" / "private" / "knowledge.db"))).expanduser()

SCHEMA = """
CREATE TABLE IF NOT EXISTS wiki_pages (
  page_id TEXT PRIMARY KEY, page_type TEXT NOT NULL, title TEXT NOT NULL,
  path TEXT NOT NULL, namespace TEXT NOT NULL, content_hash TEXT NOT NULL,
  approved_hash TEXT NOT NULL DEFAULT '', status TEXT NOT NULL,
  review_status TEXT NOT NULL, updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS wiki_sources (
  source_path TEXT PRIMARY KEY, page_id TEXT NOT NULL, source_hash TEXT NOT NULL,
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS wiki_relations (
  from_page_id TEXT NOT NULL, to_page_id TEXT NOT NULL, relation_type TEXT NOT NULL,
  PRIMARY KEY (from_page_id, to_page_id, relation_type)
);
CREATE TABLE IF NOT EXISTS embedding_records (
  page_id TEXT NOT NULL, chunk_id TEXT NOT NULL, namespace TEXT NOT NULL,
  content_hash TEXT NOT NULL, embedding_model TEXT NOT NULL, dimensions INTEGER NOT NULL,
  vector_store TEXT NOT NULL, vector_record_id TEXT, status TEXT NOT NULL,
  updated_at REAL NOT NULL, PRIMARY KEY (page_id, chunk_id, namespace)
);
CREATE TABLE IF NOT EXISTS retrieval_events (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, turn_id INTEGER,
  query TEXT NOT NULL, namespaces TEXT NOT NULL, retrieved_ids TEXT NOT NULL,
  selected_ids TEXT NOT NULL, strategy TEXT NOT NULL, created_at REAL NOT NULL
);
"""


def database_for_root(root: Path | None = None, database: Path | None = None) -> Path:
    if database is not None:
        return Path(database).expanduser()
    root = (root or WIKI_ROOT).resolve()
    if root == WIKI_ROOT.resolve():
        return DEFAULT_DB
    return root.parent / "private" / "knowledge.db"


def _hash_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _hash_page(page: WikiPage) -> str:
    return _hash_path(page.path)


def _namespace(page: WikiPage) -> str:
    return "personal"


def _prepare(db: sqlite3.Connection) -> None:
    db.executescript(SCHEMA)
    columns = {row[1] for row in db.execute("PRAGMA table_info(wiki_pages)")}
    if "approved_hash" not in columns:
        db.execute("ALTER TABLE wiki_pages ADD COLUMN approved_hash TEXT NOT NULL DEFAULT ''")


def sync_metadata(root: Path | None = None, database: Path | None = None, *, approve_new: bool = True) -> dict:
    root = (root or WIKI_ROOT).resolve()
    database = database_for_root(root, database)
    database.parent.mkdir(parents=True, exist_ok=True)
    pages = list_pages(root)
    now = time.time()
    with sqlite3.connect(database) as db:
        _prepare(db)
        previous = {
            row[0]: row
            for row in db.execute("SELECT page_id,content_hash,approved_hash,status FROM wiki_pages")
        }
        current = {page.id: page for page in pages}
        db.execute("DELETE FROM wiki_relations")
        db.execute("DELETE FROM wiki_sources")
        for page in pages:
            content_hash = _hash_page(page)
            old = previous.get(page.id)
            if old is None:
                status = "approved" if approve_new else "pending_review"
                approved_hash = content_hash if approve_new else ""
            elif old[2] and old[2] == content_hash:
                status = old[3] if old[3] in ("approved", "pending_review") else "approved"
                approved_hash = old[2]
            else:
                status = "pending_review"
                approved_hash = old[2]
            db.execute(
                """INSERT INTO wiki_pages(page_id,page_type,title,path,namespace,content_hash,approved_hash,status,review_status,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(page_id) DO UPDATE SET page_type=excluded.page_type,title=excluded.title,
                path=excluded.path,namespace=excluded.namespace,content_hash=excluded.content_hash,
                approved_hash=excluded.approved_hash,status=excluded.status,review_status=excluded.review_status,
                updated_at=excluded.updated_at""",
                (page.id, page.type, page.title, str(page.path), _namespace(page), content_hash,
                 approved_hash, status, page.review_status, now),
            )
            for source in page.sources:
                source_path = root / source
                source_hash = _hash_path(source_path) if source_path.is_file() else ""
                db.execute(
                    "INSERT OR REPLACE INTO wiki_sources(source_path,page_id,source_hash,updated_at) VALUES(?,?,?,?)",
                    (source, page.id, source_hash, now),
                )
            for link in page.links:
                db.execute(
                    "INSERT OR IGNORE INTO wiki_relations(from_page_id,to_page_id,relation_type) VALUES(?,?,?)",
                    (page.id, link, "wikilink"),
                )
        if current:
            marks = ",".join("?" for _ in current)
            db.execute(f"DELETE FROM wiki_pages WHERE page_id NOT IN ({marks})", tuple(current))
        else:
            db.execute("DELETE FROM wiki_pages")
        db.commit()
    return {"database": str(database), "pages": len(pages), "relations": sum(len(page.links) for page in pages)}


def page_statuses(database: Path | None = None, root: Path | None = None) -> dict[str, str] | None:
    database = database_for_root(root, database)
    if not database.is_file():
        return None
    try:
        with sqlite3.connect(database) as db:
            _prepare(db)
            rows = db.execute("SELECT page_id,path,status,approved_hash FROM wiki_pages").fetchall()
            if root is not None:
                root = Path(root).resolve()
                rows = [row for row in rows if Path(row[1]).resolve().is_relative_to(root)]
            statuses = {}
            for page_id, path, status, approved_hash in rows:
                # A direct Obsidian edit is considered pending even before the
                # polling watcher gets a chance to update SQLite.
                if status == "approved" and approved_hash and Path(path).is_file() and _hash_path(Path(path)) != approved_hash:
                    statuses[page_id] = "pending_review"
                else:
                    statuses[page_id] = status
            return statuses
    except (OSError, sqlite3.Error):
        return None


def approve_page(page_id: str, database: Path | None = None, root: Path | None = None) -> None:
    database = database_for_root(root, database)
    with sqlite3.connect(database) as db:
        _prepare(db)
        row = db.execute("SELECT content_hash FROM wiki_pages WHERE page_id=?", (page_id,)).fetchone()
        if row is None:
            raise ValueError("Wiki 页面元数据不存在")
        db.execute("UPDATE wiki_pages SET status='approved',approved_hash=?,updated_at=? WHERE page_id=?", (row[0], time.time(), page_id))
        db.commit()


def record_retrieval(query: str, namespaces: list[str], retrieved_ids: list[str], selected_ids: list[str],
                     strategy: str = "hybrid", session_id: str | None = None, turn_id: int | None = None,
                     database: Path | None = None) -> None:
    database = database_for_root(database=database)
    database.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database) as db:
        _prepare(db)
        db.execute(
            "INSERT INTO retrieval_events(session_id,turn_id,query,namespaces,retrieved_ids,selected_ids,strategy,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (session_id, turn_id, query, json.dumps(namespaces, ensure_ascii=False),
             json.dumps(retrieved_ids, ensure_ascii=False), json.dumps(selected_ids, ensure_ascii=False), strategy, time.time()),
        )
        db.commit()
