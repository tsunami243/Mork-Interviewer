"""Reviewed multi-file publication with durable journal and optimistic edit protection."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from threading import RLock, Lock
from functools import wraps

_LOCKS = {}
_LOCKS_GUARD = Lock()

def vault_lock(root):
    key = str(root.resolve())
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, RLock())

def serialized(fn):
    @wraps(fn)
    def call(root, *args, **kwargs):
        with vault_lock(root):
            return fn(root, *args, **kwargs)
    return call

from wiki_store import _frontmatter, list_pages


DIRECTORIES = {"concept": "concepts", "entity": "entities", "source": "sources", "question": "questions", "synthesis": "synthesis"}


class PublishConflict(ValueError):
    """An external edit or unsafe change requires a new review rather than overwriting."""


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def file_digest(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def safe_path(root: Path, relative: str) -> Path:
    path = root / relative
    if Path(relative).is_absolute() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("发布路径不能离开知识库")
    return path


def atomic_write(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".wiki-write-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _journal_path(directory: Path, job_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
        raise ValueError("任务 ID 无效")
    return directory / "transactions" / f"{job_id}.json"


def _save_journal(path: Path, journal: dict):
    atomic_write(path, json.dumps(journal, ensure_ascii=False, indent=2))


def rollback(root: Path, journal_path: Path, journal: dict):
    blocked = []
    for entry in reversed(journal["files"]):
        path = safe_path(root, entry["path"])
        current = file_digest(path)
        if current == entry["before_hash"]:
            continue
        if current != entry["after_hash"]:
            blocked.append(entry["path"])
            continue
        if entry["before"] is None:
            path.unlink()  # Validated single file, never a recursive deletion.
        else:
            atomic_write(path, entry["before"])
    journal["state"] = "blocked" if blocked else "rolled_back"
    journal["blocked_paths"] = blocked
    _save_journal(journal_path, journal)
    if blocked:
        raise PublishConflict("发布期间文件被外部修改，已停止回滚；请核对事务备份：" + ", ".join(blocked))


def recover_publication(root: Path, directory: Path, job_id: str):
    path = _journal_path(directory, job_id)
    if not path.exists():
        return None
    journal = json.loads(path.read_text(encoding="utf-8"))
    if journal.get("vault") != str(root.resolve()):
        raise PublishConflict("事务所属 Vault 不匹配")
    if journal["state"] == "committed":
        marker = root / ".wiki-publishing.json"
        if marker.exists() and json.loads(marker.read_text(encoding="utf-8")).get("job_id") == job_id:
            marker.unlink()
        return journal["receipt"]
    if journal["state"] in ("prepared", "committing", "blocked"):
        rollback(root, path, journal)
    marker = root / ".wiki-publishing.json"
    if marker.exists() and json.loads(marker.read_text(encoding="utf-8")).get("job_id") == job_id:
        marker.unlink()
    return None


@serialized
def commit_files(root: Path, directory: Path, job_id: str, writes: dict[str, str], expected: dict, receipt: dict):
    if (root / ".wiki-publishing.json").exists():
        raise PublishConflict("存在尚未恢复的发布事务")
    # The queue serializes internal writers; hashes guard edits made in Obsidian since generation.
    for relative, before_hash in expected.items():
        if file_digest(safe_path(root, relative)) != before_hash:
            raise PublishConflict(f"页面已被修改，须重新分析审核：{relative}")
    entries = []
    for relative, after in writes.items():
        path = safe_path(root, relative)
        before = path.read_bytes().decode("utf-8") if path.exists() else None
        if before == after:
            continue
        entries.append({"path": relative, "before": before, "before_hash": file_digest(path),
                        "after": after, "after_hash": digest(after)})
    journal_path = _journal_path(directory, job_id)
    journal = {"vault": str(root.resolve()), "state": "prepared", "receipt": receipt, "files": entries}
    _save_journal(journal_path, journal)
    marker = root / ".wiki-publishing.json"
    atomic_write(marker, json.dumps({"job_id": job_id}))
    try:
        journal["state"] = "committing"
        _save_journal(journal_path, journal)
        for entry in entries:
            path = safe_path(root, entry["path"])
            if file_digest(path) != entry["before_hash"]:
                raise PublishConflict(f"发布过程中页面被修改：{entry['path']}")
            atomic_write(path, entry["after"])
        journal["state"] = "committed"
        _save_journal(journal_path, journal)
        marker.unlink()
        return receipt
    except Exception:
        rollback(root, journal_path, journal)
        marker.unlink(missing_ok=True)
        raise


@serialized
def publish_proposal(root: Path, directory: Path, job_id: str, proposal: dict):
    from wiki_execution import event
    event("publication","started",count=len(proposal["pages"]))
    existing = {page.id: page for page in list_pages(root)}
    pages = proposal["pages"]
    ids = {page["id"] for page in pages}
    if len(ids) != len(pages):
        raise ValueError("发布页 ID 重复")
    for page in pages:
        if not isinstance(page.get("id"), str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", page["id"]) or page.get("type") not in DIRECTORIES:
            raise ValueError("发布页面类型或 ID 无效")
        old = existing.get(page["id"])
        allowed_path = old.path.relative_to(root).as_posix() if old else f"{DIRECTORIES[page['type']]}/{page['id']}.md"
        if not old and page["type"]=="source" and page["path"]==f"summaries/{page['id']}.md":
            allowed_path=page["path"]  # Legacy source summaries remain supported.
        if page["path"] != allowed_path or (old and old.type != page["type"]):
            raise ValueError("发布页面路径不符合类型目录，拒绝覆盖其他文件")
        if any(link not in ids and link not in existing for link in page["links"]):
            raise ValueError("知识链接必须指向已审核或同批待发布页面")
    source_rel = proposal["source_path"]
    if not re.fullmatch(r"sources/user-[a-f0-9]{24}\.md", source_rel):
        raise ValueError("原始资料快照路径无效")
    source_path = safe_path(root, source_rel)
    source_content = proposal["source_content"]
    if source_path.exists() and source_path.read_text(encoding="utf-8") != source_content:
        raise PublishConflict("原始资料快照不可被覆盖")
    writes, expected = {source_rel: source_content}, {}
    catalog = {id: {"title": page.title, "type": page.type} for id, page in existing.items()}
    for page in pages:
        relative = page["path"]
        expected[relative] = page["base_hash"]
        old = existing.get(page["id"])
        sources = list(dict.fromkeys([*(old.sources if old else ()), *page.get("inherited_sources", []), source_rel]))
        for source in sources:
            if source != source_rel and (not source.startswith("sources/") or not safe_path(root, source).is_file()):
                raise ValueError("待发布页引用的来源无效")
        evidence = list(old.evidence if old else ()) + page.get("evidence", [])
        from wiki_browser import metadata as parse_metadata
        old_relations=parse_metadata(old.path.read_text(encoding='utf-8-sig'))[0].get('relations',[]) if old else []
        merged_relations={r['target']:r for r in old_relations if isinstance(r,dict) and isinstance(r.get('target'),str) and r['target'] in page['links']} if isinstance(old_relations,list) else {}
        for relation in page.get('relations',[]):
            previous=merged_relations.get(relation['target'])
            if not previous or previous.get('validation')!='evidence_supported' or relation.get('validation')=='evidence_supported':
                merged_relations[relation['target']]=relation
        metadata = {"id": page["id"], "title": page["title"], "type": page["type"], "tags": page["tags"],
                    "sources": sources, "status": "approved", "evidence": evidence,
                    "aliases":list(dict.fromkeys([*(old.aliases if old else ()), *page.get("aliases",[])])),
                    "review_status": proposal.get("review_status", "manual"),
                    "relations":list(merged_relations.values()),
                    "conflicts": list(dict.fromkeys([*(old.conflicts if old else ()), *page["conflicts"]]))}
        related = "\n\n## 相关知识\n" + "\n".join(f"- [[{link}]]" for link in page["links"]) if page["links"] else ""
        quotes = "\n\n## 本次来源证据\n" + "\n\n".join(
            f"> {item['quote'].replace(chr(10), chr(10) + '> ')}\n\n来源：[[{source_rel[:-3]}]]" for item in page.get("evidence", [])
        ) if page.get("evidence") else ""
        disputes = "\n\n## 已审核的资料差异\n" + "\n".join(f"- {item}" for item in page["conflicts"]) if page["conflicts"] else ""
        writes[relative] = _frontmatter(metadata) + f"# {page['title']}\n\n{page['body'].strip()}\n\n<!-- WIKI REFERENCES START -->{disputes}{related}{quotes}\n"
        catalog[page["id"]] = {"title": page["title"], "type": page["type"]}
    begin, end = "<!-- WIKI AUTO INDEX START -->", "<!-- WIKI AUTO INDEX END -->"
    index_path = root / "index.md"
    index = index_path.read_text(encoding="utf-8") if index_path.exists() else "# 面试 Wiki\n"
    block = begin + "\n\n## 已发布知识目录\n\n" + "\n".join(
        f"- [[{id}|{page['title']}]] ({page['type']})" for id, page in sorted(catalog.items())
    ) + "\n\n" + end
    if begin in index and end in index:
        left, tail = index.split(begin, 1)
        _, right = tail.split(end, 1)
        index = left + block + right
    else:
        index = index.rstrip() + "\n\n" + block + "\n"
    writes["index.md"] = index
    if not proposal.get("core_only"):
        writes["overview.md"] = "# 知识概览\n\n本页是已审核知识的导航，不产生新的技术事实。\n\n" + "\n".join(f"- [[{id}|{p['title']}]]" for id, p in sorted(catalog.items())) + "\n"
    log_path = root / "log.md"
    log = log_path.read_text(encoding="utf-8") if log_path.exists() else "# 建库操作记录\n"
    writes["log.md"] = log.rstrip() + f"\n\n- {datetime.now(timezone.utc).isoformat()} 发布任务 {job_id}：" + ", ".join(sorted(ids)) + "\n"
    receipt = {"job_id": job_id, "page_ids": sorted(ids), "source_path": source_rel,
               "changed_paths": list(writes), "index_status": "pending"}
    result=commit_files(root,directory,job_id,writes,expected,receipt)
    event("publication","finished",count=len(pages),message="事务提交完成，来源、目录与操作记录已保存")
    return result
