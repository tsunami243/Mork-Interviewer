"""Incremental Embedding reuse with atomically published LanceDB snapshots."""

from dataclasses import dataclass
import hashlib
from functools import wraps
import json
import math
from pathlib import Path
import re
from uuid import uuid4

import lancedb

from embedding_index import DEFAULT_BASE_URL, DIMENSIONS, _embed, create_embedding_client, embedding_settings
from question_bank import load_question_bank
from wiki_publish import atomic_write, digest, vault_lock
from wiki_store import WIKI_ROOT, WikiPage, list_pages


INDEX_DIR = Path(__file__).resolve().parents[1] / "data" / ".wiki-index"


@dataclass(frozen=True)
class IndexUpdate:
    directory: Path
    pages: int
    embedded_chunks: int
    embedded_queries: int
    reused_chunks: int
    no_op: bool = False


def _fingerprint(pages: list[WikiPage], model: str) -> str:
    # Retain the v1 fingerprint so prebuilt indexes can still be read/migrated.
    content = [(page.id, page.path.read_text(encoding="utf-8-sig")) for page in pages]
    return hashlib.sha256(json.dumps([model, DIMENSIONS, content], ensure_ascii=False).encode("utf-8")).hexdigest()


def _chunks(text: str, size: int = 900, overlap: int = 100) -> list[str]:
    text = text.strip()
    return [text[i:i + size] for i in range(0, len(text), size - overlap)] if text else []


def _query_tags(pages):
    return sorted({tag for question in load_question_bank() for tag in question.tags} | {tag for page in pages for tag in page.tags})


def _provider_hash():
    return digest(embedding_settings()[1].rstrip("/"))


def _valid_vector(vector):
    return isinstance(vector, list) and len(vector) == DIMENSIONS and all(
        isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) for value in vector
    ) and any(value != 0 for value in vector)


def _read_manifest(directory):
    try:
        return json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _compatible(manifest, model):
    if not isinstance(manifest, dict) or manifest.get("model") != model or manifest.get("dimensions") != DIMENSIONS:
        return False
    provider = manifest.get("provider_hash")
    return provider == _provider_hash() if provider else embedding_settings()[1].rstrip("/") == DEFAULT_BASE_URL.rstrip("/")


def _table_name(manifest):
    name = manifest.get("table", "wiki")
    return name if isinstance(name, str) and re.fullmatch(r"wiki(?:_[a-f0-9]{32})?", name) else None


def index_locked(fn):
    @wraps(fn)
    def call(*args, **kwargs):
        with vault_lock(kwargs.get("root") or WIKI_ROOT):
            return fn(*args, **kwargs)
    return call


@index_locked
def sync_wiki_index(client=None, *, root: Path | None = None, directory: Path | None = None,
                    model: str | None = None, force=False) -> IndexUpdate:
    root, directory = root or WIKI_ROOT, directory or INDEX_DIR
    model = model or embedding_settings()[2]
    if (root / ".wiki-publishing.json").exists():
        raise ValueError("知识页正在发布，请稍后更新索引")
    pages = list_pages(root)
    if not force and load_wiki_index(root=root, directory=directory, model=model) is not None:
        chunks = sum(len(_chunks(page.body)) for page in pages)
        return IndexUpdate(directory, len(pages), 0, 0, chunks, True)
    fingerprint = _fingerprint(pages, model)
    old = _read_manifest(directory)
    rows = []
    texts = {}
    for page in pages:
        for number, chunk in enumerate(_chunks(page.body)):
            text = f"{page.title} {' '.join(page.tags)} {chunk}"
            key = digest(text)
            texts[key] = text
            rows.append({"id": f"{page.id}:{number}", "page_id": page.id, "title": page.title,
                         "type": page.type, "text": chunk, "tags": " ".join(page.tags), "content_hash": key})
    cache, queries = {}, {}
    if not force and _compatible(old, model):
        old_queries = old.get("query_vectors", {})
        queries = {tag: vector for tag, vector in old_queries.items() if _valid_vector(vector)} if isinstance(old_queries, dict) else {}
        table_name = _table_name(old)
        try:
            if table_name:
                table = lancedb.connect(str(directory)).open_table(table_name)
                for row in table.to_arrow().to_pylist():
                    vector = row.get("vector")
                    if _valid_vector(vector):
                        key = row.get("content_hash") or digest(f"{row['title']} {row['tags']} {row['text']}")
                        cache[key] = vector
        except (OSError, ValueError, RuntimeError, KeyError, TypeError):
            cache = {}
    needed = [key for key in texts if key not in cache]
    tags = _query_tags(pages)
    needed_tags = [tag for tag in tags if tag not in queries] if pages else []
    if needed or needed_tags:
        client = client or create_embedding_client()
        vectors = _embed(client, model, [texts[key] for key in needed] + needed_tags)
        if len(vectors) != len(needed) + len(needed_tags):
            raise ValueError("Embedding 结果数量错误，索引未提交")
        cache.update(zip(needed, vectors[:len(needed)]))
        queries.update(zip(needed_tags, vectors[len(needed):]))
    if _fingerprint(list_pages(root), model) != fingerprint or (root / ".wiki-publishing.json").exists():
        raise ValueError("生成向量期间知识页发生变化，请重新更新索引")
    directory.mkdir(parents=True, exist_ok=True)
    table_name = None
    if rows:
        table_name = "wiki_" + uuid4().hex
        db = lancedb.connect(str(directory))
        db.create_table(table_name, data=[{**row, "vector": cache[row["content_hash"]]} for row in rows])
    manifest = {"version": 2, "table": table_name, "model": model, "dimensions": DIMENSIONS,
                "provider_hash": _provider_hash(), "fingerprint": fingerprint,
                "tags_hash": digest(json.dumps(tags, ensure_ascii=False)),
                "query_vectors": {tag: queries[tag] for tag in tags if tag in queries},
                "page_hashes": {page.id: digest(page.path.read_text(encoding="utf-8-sig")) for page in pages}}
    # Failed API calls or table writes never replace the last successful manifest.
    atomic_write(directory / "manifest.json", json.dumps(manifest, ensure_ascii=False))
    return IndexUpdate(directory, len(pages), len(needed), len(needed_tags), len(rows) - sum(row["content_hash"] in needed for row in rows))


def build_wiki_index(client=None, *, root: Path | None = None, directory: Path | None = None, model: str | None = None) -> Path:
    if not list_pages(root or WIKI_ROOT):
        raise ValueError("Wiki 没有已审核的知识页，无法建立索引")
    return sync_wiki_index(client, root=root, directory=directory, model=model, force=True).directory


class WikiVectorIndex:
    def __init__(self, directory: Path, vectors: dict[str, list[float]], table="wiki"):
        self.directory, self.vectors, self.table = directory, vectors, table

    def search(self, tags: list[str], top_k: int = 3) -> list[str]:
        if type(top_k) is not int or not 1 <= top_k <= 5:
            raise ValueError("top_k 必须在1至5之间")
        queries = [self.vectors[tag] for tag in tags if tag in self.vectors]
        if not queries:
            return []
        table = lancedb.connect(str(self.directory)).open_table(self.table)
        hits = []
        for query in queries[:4]:
            hits.extend(table.search(query).metric("cosine").limit(top_k).to_list())
        ordered = sorted(hits, key=lambda hit: hit.get("_distance", float("inf")))
        return list(dict.fromkeys(hit["page_id"] for hit in ordered))[:top_k]


def load_wiki_index(*, root: Path | None = None, directory: Path | None = None, model: str | None = None) -> WikiVectorIndex | None:
    root, directory = root or WIKI_ROOT, directory or INDEX_DIR
    model = model or embedding_settings()[2]
    manifest = _read_manifest(directory)
    if manifest is None:
        return None
    try:
        pages = list_pages(root)
        if not pages or not _compatible(manifest, model) or manifest["fingerprint"] != _fingerprint(pages, model):
            return None
        if manifest.get("version") == 2 and manifest.get("tags_hash") != digest(json.dumps(_query_tags(pages), ensure_ascii=False)):
            return None
        vectors = manifest["query_vectors"]
        if not isinstance(vectors, dict) or not vectors or any(not _valid_vector(vector) for vector in vectors.values()):
            return None
        name = _table_name(manifest)
        if name is None or name not in lancedb.connect(str(directory)).list_tables().tables:
            return None
        return WikiVectorIndex(directory, vectors, name)
    except (OSError, ValueError, KeyError, TypeError):
        return None
