"""Safe read-only knowledge-vault catalog and Markdown preview; no ingestion side effects."""
from dataclasses import dataclass, field
import hashlib
from pathlib import Path
import os
import re

import yaml

ROOT_FILES = {'index.md','overview.md','purpose.md','schema.md','log.md'}
DIRECTORIES = {'entities','concepts','summaries','sources','questions','synthesis'}
GROUPS = [('概览','Overview'),('实体','Entities'),('概念','Concepts'),('资料','Sources'),('参考题','Questions'),('综合','Synthesis')]
DIRECTORY_GROUP = {'entities':'实体','concepts':'概念','summaries':'资料','sources':'资料','questions':'参考题','synthesis':'综合'}
MAX_BYTES = 1024 * 1024
MAX_CHARS = 120000


@dataclass(frozen=True)
class VaultFile:
    relative: str
    title: str
    group: str
    sources: tuple[str,...] = ()
    status: str = ''
    type: str = ''
    metadata: dict = field(default_factory=dict)


def safe_file(root: Path, relative: str):
    path = Path(relative)
    if path.is_absolute() or '..' in path.parts or path.suffix.lower() != '.md':
        raise ValueError('只能预览知识库内的Markdown文件')
    if any(part.startswith('.') for part in path.parts) or (len(path.parts)>1 and path.parts[0] not in DIRECTORIES):
        raise ValueError('该文件不属于知识库预览范围')
    target = root/path
    if not target.resolve().is_relative_to(root.resolve()) or not target.is_file():
        raise ValueError('知识文件不存在或路径离开了知识库')
    return target


def metadata(text):
    fields={}
    body=text
    if text.startswith('---\n'):
        head, sep, remainder=text[4:].partition('\n---\n')
        if sep:
            try:
                parsed=yaml.safe_load(head)
                fields=parsed if isinstance(parsed,dict) else {}
                body=remainder
            except yaml.YAMLError:
                pass
    return fields,body


def _make_entry(relative, fields, body):
    title=fields.get('title')
    if not isinstance(title,str) or not title.strip():
        source_name=next((line.partition('：')[2].strip() for line in body.splitlines()[:8] if line.startswith('文件：')),None)
        heading=next((line.lstrip('# ').strip() for line in body.splitlines() if line.startswith('# ')),None)
        title=source_name or heading or Path(relative).stem
    title=' '.join(title.split())[:110]
    sources=fields.get('sources',[])
    sources=tuple(dict.fromkeys(source for source in sources if isinstance(source,str))) if isinstance(sources,list) else ()
    first=Path(relative).parts[0]
    return VaultFile(relative,title,DIRECTORY_GROUP.get(first,'概览'),sources,
                     fields.get('status','') if isinstance(fields.get('status',''),str) else '',
                     fields.get('type','') if isinstance(fields.get('type',''),str) else '', dict(fields))


def entry(root,relative):
    target=safe_file(root,relative)
    with target.open(encoding='utf-8-sig') as handle:
        sample=handle.read(16000)
    fields,body=metadata(sample)
    return _make_entry(relative,fields,body)


def _paths(root: Path):
    for name in sorted(set(ROOT_FILES) | {p.name for p in root.glob('*.md') if not p.name.startswith('.')}):
        if (root/name).is_file():
            yield name
    for directory in sorted(DIRECTORIES):
        base=root/directory
        if not base.is_dir() or not base.resolve().is_relative_to(root.resolve()):
            continue
        for current,dirs,names in os.walk(base,followlinks=False):
            dirs[:]=sorted(d for d in dirs if not (Path(current)/d).is_symlink())
            for name in sorted(names):
                if name.lower().endswith('.md'):
                    yield (Path(current)/name).relative_to(root).as_posix()


def catalog(root: Path):
    files=[]
    for relative in _paths(root):
        try:
            item=entry(root,relative)
            if len(Path(relative).parts)>1 and item.status in ('draft','pending','needs_review'):
                continue
            files.append(item)
            if len(files)>=1000: return files
        except (OSError,ValueError):
            pass
    return files


def scan_vault(root: Path):
    """One-pass bootstrap: parse each Markdown frontmatter once for files and graph docs."""
    files=[]
    docs={}
    for relative in _paths(root):
        try:
            path=safe_file(root,relative)
            size=path.stat().st_size
            if size>MAX_BYTES:
                with path.open(encoding='utf-8-sig') as handle: raw=handle.read(16000)
                fields,body=metadata(raw)
            else:
                raw=path.read_text(encoding='utf-8-sig')
                fields,body=metadata(raw)
            item=_make_entry(relative,fields,body)
            if len(Path(relative).parts)>1 and item.status in ('draft','pending','needs_review'):
                continue
            files.append(item)
            if size<=MAX_BYTES:
                normalized=raw.replace('\\r\\n','\\n')
                docs[relative]={'path':relative,'title':item.title,'group':item.group,
                    'kind':{'concept':'概念','entity':'实体','source':'来源摘要','question':'参考题','synthesis':'综合'}.get(item.type,item.group),
                    'type':item.type or 'other','fields':fields,'text':normalized,'body':body,
                    'sources':item.sources,'hash':hashlib.sha256(raw.encode('utf-8')).hexdigest()}
            if len(files)>=1000: break
        except (OSError,ValueError,UnicodeError):
            continue
    return files,docs


def preview(root,relative: str):
    path=safe_file(root,relative)
    if path.stat().st_size>MAX_BYTES:
        raise ValueError('文件超过1MB，只读预览暂不加载')
    text=path.read_text(encoding='utf-8-sig')
    fields,body=metadata(text)
    truncated=len(body)>MAX_CHARS
    body=body[:MAX_CHARS]
    body=re.sub(r"<!--[\s\S]*?-->","",body)
    body=re.sub(r'!\[([^\]]*)\]\([^)]*\)',r'[图片：\1；预览不加载外部资源]',body)
    body=re.sub(r'!\[([^\]]*)\]\[[^\]]*\]',r'[图片：\1；预览不加载外部资源]',body)
    body=re.sub(r'\[([^\]]+)\]\((?:javascript|data):[^)]*\)',r'\1',body,flags=re.I)
    return _make_entry(relative,fields,body),body,truncated


def escape_label(text):
    return re.sub(r'([\\`*_{}\[\]<>#!|])',r'\\\1',text)
