"""Read-only file metadata and resolved relation cards for the web workbench."""
from datetime import datetime,timezone
from pathlib import Path
import re

from wiki_browser import safe_file


KINDS={'concept':'概念','entity':'实体','source':'来源摘要','question':'参考题','synthesis':'综合'}


def file_listing(root, snapshot=None, *, asynchronous=False):
    if snapshot is None:
        from wiki_snapshot import get_snapshot
        snapshot = get_snapshot(root, asynchronous=asynchronous)
    aliases = snapshot.source_aliases
    return [{'path': item.relative, 'title': item.title, 'group': item.group,
             'kind': KINDS.get(item.type, item.group)}
            for item in snapshot.files if item.relative not in aliases]


def file_details(root,relative,snapshot=None, *, asynchronous=False):
    if snapshot is None:
        from wiki_snapshot import get_snapshot
        snapshot = get_snapshot(root, asynchronous=asynchronous)
    item = snapshot.files_by_path.get(relative)
    if item is None:
        # A newly approved/copied page can exist on disk while the shared
        # snapshot is still serving the previous revision. Do not make the
        # normal list/graph APIs wait for a rebuild, but a direct request for
        # an existing file must not incorrectly return 404. Validate the
        # path first so invalid requests cannot trigger a rebuild.
        path = safe_file(root, relative)
        if path.is_file():
            from wiki_snapshot import get_snapshot
            snapshot = get_snapshot(root, asynchronous=False)
            item = snapshot.files_by_path.get(relative)
    if item is None:
        raise ValueError('知识文件不存在或尚未进入快照')
    path = safe_file(root, relative)
    fields = item.metadata
    if path.stat().st_size > 1024 * 1024:
        raise ValueError('文件超过1MB，只读预览暂不加载')
    raw = path.read_text(encoding='utf-8-sig')
    if raw.startswith('---\n'):
        _, separator, raw = raw[4:].partition('\n---\n')
        if not separator:
            raise ValueError('Wiki 页面 frontmatter 无效')
    body = raw
    truncated = len(body) > 120000
    body = body[:120000]
    body = re.sub(r"<!--[\s\S]*?-->", "", body)
    body = re.sub(r'!\[([^\]]*)\]\([^)]*\)', r'[图片：\1；预览不加载外部资源]', body)
    body = re.sub(r'!\[([^\]]*)\]\[[^\]]*\]', r'[图片：\1；预览不加载外部资源]', body)
    body = re.sub(r'\[([^\]]+)\]\((?:javascript|data):[^)]*\)', r'\1', body, flags=re.I)
    from wiki_relations import relation_cards
    relations = relation_cards(root, relative, snapshot)
    aliases = snapshot.source_aliases
    original_sources=[{'path':raw,'title':'原文快照','filename':Path(raw).name} for raw,summary in aliases.items() if summary==relative]
    summary_path=aliases.get(relative)
    source_summary={'path':summary_path,'title':'返回来源摘要'} if summary_path else None
    tags=fields.get('tags',[])
    return {'path':relative,'filename':path.name,'title':item.title,'group':item.group,'kind':KINDS.get(item.type,item.group),
            'format':'Markdown','status':item.status or 'unmarked','review_status':fields.get('review_status','') if isinstance(fields.get('review_status',''),str) else '',
            'tags':[tag for tag in tags if isinstance(tag,str)] if isinstance(tags,list) else [],
            'bytes':path.stat().st_size,'modified_at':datetime.fromtimestamp(path.stat().st_mtime,timezone.utc).isoformat(),
            'body':body,'truncated':truncated,'relations':relations,'original_sources':original_sources,'source_summary':source_summary}
