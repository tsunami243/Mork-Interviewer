"""Shared read-only Wiki snapshot used by the workbench catalog and graph APIs.

The snapshot is derived from Markdown files. It is never the source of truth and
can be discarded/rebuilt at any time. The important invariant is that all
read-only views for one vault consume the same parsed snapshot.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
from threading import Lock, Thread
import time
from typing import Any

from wiki_browser import DIRECTORIES, ROOT_FILES, VaultFile, scan_vault


@dataclass(frozen=True)
class VaultSnapshot:
    root: Path
    fingerprint: tuple[tuple[str, int, int], ...]
    files: tuple[VaultFile, ...] = ()
    docs: dict[str, dict[str, Any]] = field(default_factory=dict)
    summaries: dict[str, str] = field(default_factory=dict)
    source_aliases: dict[str, str] = field(default_factory=dict)
    graph: dict[str, Any] = field(default_factory=lambda: {"nodes": [], "links": [], "source_aliases": {}})
    status: str = "ready"
    error: str = ""
    version: int = 0
    built_at: float = 0.0

    @property
    def files_by_path(self) -> dict[str, VaultFile]:
        return {item.relative: item for item in self.files}


class _SnapshotEntry:
    def __init__(self, root: Path):
        self.root = root
        self.lock = Lock()
        self.snapshot: VaultSnapshot | None = None
        self.building = False
        self.dirty = False
        self.target_fingerprint: tuple[tuple[str, int, int], ...] | None = None
        self.error = ""


_ENTRIES: dict[str, _SnapshotEntry] = {}
_ENTRIES_LOCK = Lock()


def _root(root: Path | None) -> Path:
    return (root or Path(__file__).resolve().parents[1] / "data" / "wiki").resolve()


def _entry(root: Path) -> _SnapshotEntry:
    key = str(root)
    with _ENTRIES_LOCK:
        item = _ENTRIES.get(key)
        if item is None:
            item = _SnapshotEntry(root)
            _ENTRIES[key] = item
        return item


def _markdown_paths(root: Path) -> list[Path]:
    paths: list[Path] = []
    for name in sorted(ROOT_FILES):
        path = root / name
        if path.is_file():
            paths.append(path)
    for directory in sorted(DIRECTORIES):
        base = root / directory
        if not base.is_dir() or not base.resolve().is_relative_to(root):
            continue
        for current, dirs, names in os.walk(base, followlinks=False):
            dirs[:] = sorted(item for item in dirs if not (Path(current) / item).is_symlink())
            for name in sorted(names):
                if name.lower().endswith(".md"):
                    paths.append(Path(current) / name)
    return paths


def lightweight_fingerprint(root: Path | None = None) -> tuple[tuple[str, int, int], ...]:
    root = _root(root)
    result = []
    for path in _markdown_paths(root):
        try:
            stat = path.stat()
            result.append((path.relative_to(root).as_posix(), stat.st_size, stat.st_mtime_ns))
        except OSError:
            continue
    return tuple(result)


def _build(root: Path, fingerprint: tuple[tuple[str, int, int], ...], version: int) -> VaultSnapshot:
    # Imports are local to keep the dependency direction: relation building can
    # consume a snapshot, while snapshot construction uses its pure builder.
    from wiki_relations import build_graph_data, documents, source_aliases

    scanned_files, parsed_docs = scan_vault(root)
    files = tuple(scanned_files)
    aliases = source_aliases(root, parsed_docs)
    graph = build_graph_data(parsed_docs, files, aliases)
    summaries = {path: doc['body'][:500] for path, doc in parsed_docs.items()}
    return VaultSnapshot(root=root, fingerprint=fingerprint, files=files, docs=parsed_docs, summaries=summaries,
                         source_aliases=aliases, graph=graph, status="ready",
                         version=version, built_at=time.time())


def _build_worker(item: _SnapshotEntry, fingerprint: tuple[tuple[str, int, int], ...]) -> None:
    target = fingerprint
    while True:
        try:
            previous = item.snapshot
            next_snapshot = _build(item.root, target, (previous.version + 1) if previous else 1)
            current = lightweight_fingerprint(item.root)
            with item.lock:
                # Do not publish a snapshot known to be stale. Coalesce changes that
                # happened while parsing and build only the newest observed version.
                requested = item.target_fingerprint
                if current != target or item.dirty or (requested is not None and requested != target):
                    target = current if current != target else requested
                    item.dirty = False
                    item.target_fingerprint = target
                    continue
                item.snapshot = next_snapshot
                item.error = ""
                item.building = False
                item.dirty = False
                item.target_fingerprint = None
                return
        except Exception as error:
            with item.lock:
                item.error = f"{type(error).__name__}: {error}"
                item.building = False
                item.dirty = False
                item.target_fingerprint = None
            return


def _schedule(item: _SnapshotEntry, fingerprint: tuple[tuple[str, int, int], ...], *, asynchronous: bool) -> None:
    if item.building:
        item.dirty = True
        item.target_fingerprint = fingerprint
        return
    item.building = True
    item.dirty = False
    item.target_fingerprint = fingerprint
    if asynchronous:
        Thread(target=_build_worker, args=(item, fingerprint), name="wiki-snapshot-build", daemon=True).start()
    else:
        _build_worker(item, fingerprint)


def get_snapshot(root: Path | None = None, *, asynchronous: bool = False) -> VaultSnapshot:
    root = _root(root)
    item = _entry(root)
    fingerprint = lightweight_fingerprint(root)
    should_build = False
    wait_for_build = False
    with item.lock:
        current = item.snapshot
        if current is not None and current.fingerprint == fingerprint and not item.dirty:
            return current
        if item.building:
            if item.target_fingerprint != fingerprint:
                item.dirty = True
                item.target_fingerprint = fingerprint
            if asynchronous:
                return current or VaultSnapshot(root=root, fingerprint=fingerprint, status="initializing",
                                               error=item.error, version=0)
            # A synchronous caller must join the in-flight build rather than
            # starting a second worker for the same vault.
            wait_for_build = True
        else:
            item.building = True
            item.dirty = False
            item.target_fingerprint = fingerprint
            should_build = True
    if should_build and asynchronous:
        Thread(target=_build_worker, args=(item, fingerprint), name="wiki-snapshot-build", daemon=True).start()
        return current or VaultSnapshot(root=root, fingerprint=fingerprint, status="initializing",
                                       error=item.error, version=0)
    if should_build:
        _build_worker(item, fingerprint)
    elif wait_for_build:
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            with item.lock:
                if not item.building:
                    break
            time.sleep(0.01)
        # The worker may have coalesced a change that happened during its
        # build. Re-enter through the normal path to synchronously obtain the
        # newest published snapshot, without ever running two workers.
        with item.lock:
            still_building = item.building
        if not still_building:
            return get_snapshot(root, asynchronous=False)
    with item.lock:
        return item.snapshot or VaultSnapshot(root=root, fingerprint=fingerprint, status="initializing",
                                               error=item.error, version=0)


def invalidate(root: Path | None = None) -> None:
    root = _root(root)
    item = _entry(root)
    fingerprint = lightweight_fingerprint(root)
    with item.lock:
        item.dirty = True
        item.target_fingerprint = fingerprint
        if not item.building:
            _schedule(item, fingerprint, asynchronous=True)


def snapshot_status(root: Path | None = None) -> dict[str, Any]:
    root = _root(root)
    item = _entry(root)
    with item.lock:
        snapshot = item.snapshot
        return {
            "status": snapshot.status if snapshot else ("building" if item.building else "initializing"),
            "version": snapshot.version if snapshot else 0,
            "error": item.error or (snapshot.error if snapshot else ""),
            "building": item.building,
            "dirty": item.dirty,
        }


def clear_snapshots() -> None:
    with _ENTRIES_LOCK:
        _ENTRIES.clear()

