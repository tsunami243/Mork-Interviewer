"""Namespace-aware LanceDB persistence for the approved Markdown Wiki."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from functools import wraps
import json
import math
import os
from pathlib import Path
import re
from uuid import uuid4

import lancedb
from lancedb.index import FTS

from embedding_index import DEFAULT_BASE_URL, DIMENSIONS, _embed, create_embedding_client, embedding_settings
from question_bank import load_question_bank
from wiki_publish import atomic_write, digest, vault_lock
from wiki_store import WIKI_ROOT, WikiPage, list_pages



INDEX_DIR = Path(os.getenv("WIKI_INDEX_DIR", str(Path(__file__).resolve().parents[1] / "data" / ".wiki-index"))).expanduser()
NAMESPACES = ("personal", "public", "benchmark", "session")
DEFAULT_NAMESPACE = "personal"
CHUNKING = {
    "entity": (850, 100),
    "concept": (900, 120),
    "question": (1600, 0),
    "synthesis": (1200, 160),
    "source": (1200, 100),
}


@dataclass(frozen=True)
class IndexUpdate:
    directory: Path
    pages: int
    embedded_chunks: int
    embedded_queries: int
    reused_chunks: int
    no_op: bool = False
    namespace: str = DEFAULT_NAMESPACE
    table: str | None = None


def validate_namespace(namespace: str) -> str:
    if namespace not in NAMESPACES:
        raise ValueError(f"不支持的知识库 namespace：{namespace}")
    return namespace


def _fingerprint(pages: list[WikiPage], model: str, namespace: str) -> str:
    content = [(page.id, page.path.read_text(encoding="utf-8-sig")) for page in pages]
    return hashlib.sha256(json.dumps([namespace, model, DIMENSIONS, content], ensure_ascii=False).encode("utf-8")).hexdigest()


def _chunks(text: str, page_type: str = "concept") -> list[str]:
    size, overlap = CHUNKING.get(page_type, CHUNKING["concept"])
    text = text.strip()
    if not text:
        return []
    if overlap == 0:
        return [text]
    result = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            boundary = text.rfind("\n\n", start + size // 2, end)
            if boundary > start:
                end = boundary
        result.append(text[start:end].strip())
        if end == len(text):
            break
        start = max(end - overlap, start + 1)
    return [item for item in result if item]


def _query_tags(pages):
    return sorted({tag for question in load_question_bank() for tag in question.tags} | {tag for page in pages for tag in page.tags})


def _provider_hash():
    return digest(embedding_settings()[1].rstrip("/"))


def _valid_vector(vector):
    return isinstance(vector, list) and len(vector) == DIMENSIONS and all(
        isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) for value in vector
    ) and any(value != 0 for value in vector)


def _read_manifest(directory: Path) -> dict | None:
    try:
        return json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _compatible(manifest, model):
    if not isinstance(manifest, dict) or manifest.get("model") != model or manifest.get("dimensions") != DIMENSIONS:
        return False
    provider = manifest.get("provider_hash")
    return provider == _provider_hash() if provider else embedding_settings()[1].rstrip("/") == DEFAULT_BASE_URL.rstrip("/")


def _table_name(manifest, namespace=DEFAULT_NAMESPACE):
    tables = manifest.get("namespaces", {}) if isinstance(manifest, dict) else {}
    value = tables.get(namespace, {}) if isinstance(tables, dict) else {}
    name = value.get("active_table") if isinstance(value, dict) else None
    if not name and namespace == DEFAULT_NAMESPACE and isinstance(manifest, dict):
        name = manifest.get("table")
    return name if isinstance(name, str) and re.fullmatch(r"(?:wiki|knowledge_chunks)_[a-f0-9]{32}", name) else None


def _page_metadata(page: WikiPage) -> dict:
    aliases = list(page.aliases)
    entity_ids = [page.id] if page.type == "entity" else []
    return {
        "aliases": " ".join(aliases),
        "entities": " ".join(entity_ids),
        "search_text": " ".join((page.title, *aliases, *page.tags, page.body)),
    }


def _indexable_pages(root: Path) -> list[WikiPage]:
    pages = [page for page in list_pages(root) if page.type != "source"]
    try:
        from knowledge_metadata import page_statuses
        statuses = page_statuses(root=root)
        if statuses is not None:
            pages = [page for page in pages if statuses.get(page.id, "approved") == "approved"]
    except (OSError, RuntimeError, ValueError, TypeError):
        pass
    return pages


def _manifest_namespace(manifest: dict, namespace: str) -> dict:
    namespaces = manifest.setdefault("namespaces", {})
    entry = namespaces.setdefault(namespace, {})
    entry.setdefault("active_table", None)
    entry.setdefault("previous_table", None)
    return entry


def index_locked(fn):
    @wraps(fn)
    def call(*args, **kwargs):
        with vault_lock(kwargs.get("root") or WIKI_ROOT):
            return fn(*args, **kwargs)
    return call


@index_locked
def sync_wiki_index(client=None, *, root: Path | None = None, directory: Path | None = None,
                    model: str | None = None, force=False, namespace: str = DEFAULT_NAMESPACE) -> IndexUpdate:
    root, directory = root or WIKI_ROOT, directory or INDEX_DIR
    namespace = validate_namespace(namespace)
    model = model or embedding_settings()[2]
    if (root / ".wiki-publishing.json").exists():
        raise ValueError("知识页正在发布，请稍后更新索引")
    pages = _indexable_pages(root) if namespace == DEFAULT_NAMESPACE else []
    manifest = _read_manifest(directory) or {}
    entry = _manifest_namespace(manifest, namespace)
    fingerprint = _fingerprint(pages, model, namespace)
    if not force and load_wiki_index(root=root, directory=directory, model=model, namespace=namespace) is not None:
        chunks = sum(len(_chunks(page.body, page.type)) for page in pages)
        return IndexUpdate(directory, len(pages), 0, 0, chunks, True, namespace, entry.get("active_table"))

    rows, texts = [], {}
    for page in pages:
        metadata = _page_metadata(page)
        for number, chunk in enumerate(_chunks(page.body, page.type)):
            content_hash = digest(f"{page.id}:{number}:{chunk}")
            text = f"{page.title} {metadata['aliases']} {' '.join(page.tags)} {chunk}"
            texts[content_hash] = text
            rows.append({
                "id": f"{page.id}:{number}", "page_id": page.id, "chunk_id": f"chunk-{number + 1}",
                "namespace": namespace, "title": page.title, "type": page.type, "text": chunk,
                "search_text": metadata["search_text"], "tags": " ".join(page.tags),
                "aliases": metadata["aliases"], "entities": metadata["entities"],
                "source_ids": json.dumps(list(page.sources), ensure_ascii=False),
                "content_hash": content_hash, "review_status": page.review_status,
                "is_active": True, "embedding_model": model, "embedding_dimensions": DIMENSIONS,
            })

    old_table = entry.get("active_table")
    cache = {}
    if not force and _compatible(manifest, model) and old_table:
        try:
            table = lancedb.connect(str(directory)).open_table(old_table)
            for row in table.to_arrow().to_pylist():
                vector = row.get("vector")
                if _valid_vector(vector):
                    cache[row.get("content_hash")] = vector
        except (OSError, ValueError, RuntimeError, KeyError, TypeError):
            cache = {}
    needed = [key for key in texts if key not in cache]
    tags = _query_tags(pages)
    old_queries = entry.get("query_vectors", {}) if isinstance(entry.get("query_vectors"), dict) else {}
    queries = {tag: vector for tag, vector in old_queries.items() if _valid_vector(vector)} if _compatible(manifest, model) else {}
    needed_tags = [tag for tag in tags if tag not in queries]
    if needed or needed_tags:
        client = client or create_embedding_client()
        vectors = _embed(client, model, [texts[key] for key in needed] + needed_tags)
        if len(vectors) != len(needed) + len(needed_tags):
            raise ValueError("Embedding 结果数量错误，索引未提交")
        cache.update(zip(needed, vectors[:len(needed)]))
        queries.update(zip(needed_tags, vectors[len(needed):]))

    if _fingerprint(_indexable_pages(root) if namespace == DEFAULT_NAMESPACE else [], model, namespace) != fingerprint or (root / ".wiki-publishing.json").exists():
        raise ValueError("生成向量期间知识页发生变化，请重新更新索引")

    directory.mkdir(parents=True, exist_ok=True)
    new_table = None
    fts_ready = False
    if rows:
        new_table = "knowledge_chunks_" + uuid4().hex
        db = lancedb.connect(str(directory))
        table = db.create_table(new_table, data=[{**row, "vector": cache[row["content_hash"]]} for row in rows])
        try:
            table.create_index("search_text", config=FTS(), replace=True, name="knowledge_text")
            fts_ready = True
        except (AttributeError, OSError, RuntimeError, ValueError):
            pass

    next_manifest = {
        "version": 3, "model": model, "dimensions": DIMENSIONS,
        "provider_hash": _provider_hash(), "namespaces": manifest.get("namespaces", {}),
    }
    next_entry = _manifest_namespace(next_manifest, namespace)
    next_entry.update({
        "active_table": new_table, "previous_table": old_table, "fingerprint": fingerprint,
        "tags_hash": digest(json.dumps(tags, ensure_ascii=False)),
        "query_vectors": {tag: queries[tag] for tag in tags if tag in queries},
        "page_hashes": {page.id: digest(page.path.read_text(encoding="utf-8-sig")) for page in pages},
        "fts_ready": fts_ready,
    })
    if namespace == DEFAULT_NAMESPACE:
        next_manifest["table"] = new_table
        next_manifest["query_vectors"] = next_entry["query_vectors"]
        next_manifest["fingerprint"] = fingerprint
        next_manifest["tags_hash"] = next_entry["tags_hash"]
    atomic_write(directory / "manifest.json", json.dumps(next_manifest, ensure_ascii=False))
    try:
        from knowledge_metadata import WIKI_ROOT as METADATA_WIKI_ROOT, sync_metadata
        if root.resolve() == METADATA_WIKI_ROOT.resolve():
            sync_metadata(root=root, approve_new=True)
    except (OSError, RuntimeError, ValueError, TypeError):
        pass


    return IndexUpdate(directory, len(pages), len(needed), len(needed_tags), len(rows) - len(needed), False, namespace, new_table)
def build_wiki_index(client=None, *, root: Path | None = None, directory: Path | None = None,
                     model: str | None = None, namespace: str = DEFAULT_NAMESPACE) -> Path:
    if namespace == DEFAULT_NAMESPACE and not _indexable_pages(root or WIKI_ROOT):
        raise ValueError("Wiki 没有已审核的知识页，无法建立索引")
    return sync_wiki_index(client, root=root, directory=directory, model=model, force=True, namespace=namespace).directory


class WikiVectorIndex:
    def __init__(self, directory: Path, vectors: dict[str, list[float]], table="wiki", namespace=DEFAULT_NAMESPACE, fts_ready=False):
        self.directory, self.vectors, self.table, self.namespace, self.fts_ready = directory, vectors, table, namespace, fts_ready

    def _page_hits(self, hits, top_k):
        best = {}
        for hit in hits:
            page_id = hit.get("page_id")
            if page_id and (page_id not in best or hit.get("_distance", 1) < best[page_id].get("_distance", 1)):
                best[page_id] = hit
        return [item[0] for item in sorted(best.items(), key=lambda pair: (pair[1].get("_distance", 1), pair[0]))[:top_k]]

    def search(self, tags: list[str], top_k: int = 3, keywords: list[str] | None = None) -> list[str]:
        if type(top_k) is not int or not 1 <= top_k <= 10:
            raise ValueError("top_k 必须在1至10之间")
        table = lancedb.connect(str(self.directory)).open_table(self.table)
        hits = []
        for query in [self.vectors[tag] for tag in tags if tag in self.vectors][:4]:
            hits.extend(table.search(query, query_type="vector").limit(top_k * 3).to_list())
        for keyword in (keywords or tags)[:5]:
            if self.fts_ready and isinstance(keyword, str) and keyword.strip():
                try:
                    hits.extend(table.search(keyword, query_type="fts").limit(top_k * 3).to_list())
                except (OSError, RuntimeError, ValueError):
                    pass
        return self._page_hits(hits, top_k)


def load_wiki_index(*, root: Path | None = None, directory: Path | None = None, model: str | None = None,
                    namespace: str = DEFAULT_NAMESPACE) -> WikiVectorIndex | None:
    root, directory = root or WIKI_ROOT, directory or INDEX_DIR
    namespace = validate_namespace(namespace)
    model = model or embedding_settings()[2]
    manifest = _read_manifest(directory)
    if manifest is None:
        return None
    try:
        pages = _indexable_pages(root) if namespace == DEFAULT_NAMESPACE else []
        entry = manifest.get("namespaces", {}).get(namespace, {})
        if not _compatible(manifest, model) or entry.get("fingerprint") != _fingerprint(pages, model, namespace):
            return None
        vectors = entry.get("query_vectors", {})
        legacy_vectors = manifest.get("query_vectors") if namespace == DEFAULT_NAMESPACE else None
        if isinstance(legacy_vectors, dict) and pages and any(not _valid_vector(vector) for vector in legacy_vectors.values()):
            return None
        if not isinstance(vectors, dict) or (pages and any(not _valid_vector(vector) for vector in vectors.values())):
            return None
        name = _table_name(manifest, namespace)
        if name is None or name not in lancedb.connect(str(directory)).list_tables().tables:
            return None
        return WikiVectorIndex(directory, vectors, name, namespace, bool(entry.get("fts_ready")))
    except (OSError, ValueError, KeyError, TypeError):
        return None
