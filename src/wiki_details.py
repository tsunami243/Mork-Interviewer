"""Read-only file metadata and resolved relation cards for the web workbench."""
from datetime import datetime,timezone
from pathlib import Path
import re

from wiki_browser import catalog,preview,safe_file,metadata


KINDS={'concept':'概念','entity':'实体','source':'来源摘要','question':'参考题','synthesis':'综合'}


def file_listing(root):
    from wiki_relations import source_aliases
    aliases=source_aliases(root)
    return [{'path':item.relative,'title':item.title,'group':item.group,'kind':KINDS.get(item.type,item.group)} for item in catalog(root) if item.relative not in aliases]


def file_details(root,relative):
    item,body,truncated=preview(root,relative)
    path=safe_file(root,relative)
    fields,_=metadata(path.read_text(encoding='utf-8-sig'))
    from wiki_relations import relation_cards,source_aliases
    relations=relation_cards(root,relative)
    aliases=source_aliases(root)
    original_sources=[{'path':raw,'title':'原文快照','filename':Path(raw).name} for raw,summary in aliases.items() if summary==relative]
    summary_path=aliases.get(relative)
    source_summary={'path':summary_path,'title':'返回来源摘要'} if summary_path else None
    tags=fields.get('tags',[])
    return {'path':relative,'filename':path.name,'title':item.title,'group':item.group,'kind':KINDS.get(item.type,item.group),
            'format':'Markdown','status':item.status or 'unmarked','review_status':fields.get('review_status','') if isinstance(fields.get('review_status',''),str) else '',
            'tags':[tag for tag in tags if isinstance(tag,str)] if isinstance(tags,list) else [],
            'bytes':path.stat().st_size,'modified_at':datetime.fromtimestamp(path.stat().st_mtime,timezone.utc).isoformat(),
            'body':body,'truncated':truncated,'relations':relations,'original_sources':original_sources,'source_summary':source_summary}
