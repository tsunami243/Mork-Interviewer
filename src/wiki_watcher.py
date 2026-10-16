"""Small polling watcher for externally edited Markdown Wiki files."""
from __future__ import annotations

import hashlib
import time
from pathlib import Path

from knowledge_metadata import sync_metadata
from wiki_store import WIKI_ROOT
from wiki_snapshot import invalidate


def wiki_fingerprint(root: Path | None = None) -> str:
    root = root or WIKI_ROOT
    files = sorted(path for directory in ("concepts", "entities", "sources", "questions", "synthesis", "summaries")
                   for path in (root / directory).glob("*.md"))
    digest = hashlib.sha256()
    for path in files:
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


class WikiChangeWatcher:
    def __init__(self, root: Path | None = None, debounce_seconds: float = 7.0):
        self.root = root or WIKI_ROOT
        self.debounce_seconds = debounce_seconds
        self.last_fingerprint = wiki_fingerprint(self.root)
        self.changed_at: float | None = None

    def poll(self) -> dict | None:
        current = wiki_fingerprint(self.root)
        now = time.monotonic()
        if current != self.last_fingerprint:
            self.last_fingerprint, self.changed_at = current, now
            invalidate(self.root)
            return {"status": "changed", "debounce_seconds": self.debounce_seconds}
        if self.changed_at is not None and now - self.changed_at >= self.debounce_seconds:
            self.changed_at = None
            invalidate(self.root)
            return {"status": "ready", "metadata": sync_metadata(self.root, approve_new=False)}
        return None
