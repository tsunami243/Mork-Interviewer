"""Single safe resolver for graph edges, relation cards and link proposals."""
from collections import defaultdict
from pathlib import PurePosixPath
import posixpath
import re
import hashlib
from wiki_browser import catalog, safe_file, metadata, MAX_BYTES

KINDS = {'concept':'概念','entity':'实体','source':'来源摘要','question':'参考题','synthesis':'综合'}
LINK = re.compile(r'\[\[([^\]|]+)(?:\|[^\]]+)?\]\]')


def link_targets(body):
    text=re.sub(r'<!--.*?-->', '', body, flags=re.S)
    text=re.sub(r'(?m)^\s*(```|~~~)[^\n]*\n.*?^\s*\1[^\n]*$', '', text, flags=re.S)
    text=re.sub(r'`[^`\n]*`', '', text)
    return LINK.findall(text)


def documents(root):
    result = {}
    for item in catalog(root):
        try:
            path = safe_file(root, item.relative)
            if path.stat().st_size > MAX_BYTES:
                continue
            raw=path.read_bytes()
            text=raw.decode('utf-8-sig').replace('\r\n','\n')
            fields, body = metadata(text)
            result[item.relative] = {'path':item.relative,'title':item.title,'group':item.group,
                'kind':KINDS.get(item.type,item.group),'type':item.type or 'other',
                'fields':fields,'text':text,'body':body,'sources':item.sources,'hash':hashlib.sha256(raw).hexdigest()}
        except (OSError, ValueError, UnicodeError):
            continue
    return result


class Resolver:
    def __init__(self, docs):
        self.docs = docs
        self.names = defaultdict(set)
        for path, doc in docs.items():
            for name in (PurePosixPath(path).stem, doc['fields'].get('id'), doc['title']):
                if isinstance(name,str): self.names[name].add(path)
            aliases = doc['fields'].get('aliases',[])
            if isinstance(aliases,list):
                for alias in aliases:
                    if isinstance(alias,str): self.names[alias].add(path)

    def resolve(self, value, origin=''):
        if not isinstance(value,str): return None
        value = value.split('#',1)[0].strip().replace('\\','/')
        if not value or value.startswith(('/', 'http:', 'https:', 'file:')): return None
        if '/' not in value and len(self.names.get(value.removesuffix('.md'),set()))>1:
            return None
        candidates = [value, value if value.endswith('.md') else value+'.md']
        folder = posixpath.dirname(origin)
        candidates += [posixpath.normpath(posixpath.join(folder,p)) for p in candidates]
        for path in candidates:
            if path in self.docs: return path
        matches = self.names.get(value, set())
        return next(iter(matches)) if len(matches)==1 else None


def source_aliases(root,docs=None):
    """Fold only uniquely paired generated snapshots; never infer identity from a title."""
    if docs is None:
        docs={}
        # Library polling needs only summary metadata and a small snapshot header, not all bodies.
        for item in catalog(root):
            if item.type=='source':
                docs[item.relative]={'type':item.type,'fields':{'status':item.status},'sources':item.sources}
            elif re.fullmatch(r'sources/user-[a-f0-9]{16,64}\.md',item.relative):
                try:
                    with safe_file(root,item.relative).open(encoding='utf-8-sig') as handle:sample=handle.read(2048)
                    fields,body=metadata(sample)
                    docs[item.relative]={'type':'other','fields':fields,'body':body,'sources':()}
                except (OSError,ValueError,UnicodeError):continue
    owners=defaultdict(set)
    for path,doc in docs.items():
        if doc['type']!='source' or doc['fields'].get('status')!='approved' or len(doc['sources'])!=1:continue
        source=doc['sources'][0]
        match=re.fullmatch(r'sources/user-([a-f0-9]{16,64})\.md',source)
        raw=docs.get(source)
        if not match or not raw or raw['fields'] or not raw['body'].startswith('# 原始资料快照\n'):continue
        if PurePosixPath(path).stem!='source-'+match.group(1):continue
        owners[source].add(path)
    return {source:next(iter(paths)) for source,paths in owners.items() if len(paths)==1}


def graph_data(root):
    docs = documents(root); resolver = Resolver(docs); edges = {}; aliases=source_aliases(root,docs)
    for path, doc in docs.items():
        origin=aliases.get(path,path)
        references = [(s,'来源引用') for s in doc['sources']]
        references += [(s,'知识引用') for s in link_targets(doc['body'])]
        relations=doc['fields'].get('relations',[])
        if isinstance(relations,list):
            references += [(r['target'], '弱引用' if r.get('origin')=='weak_relink' else
                            '有依据的'+r.get('kind','reference')+'关联' if r.get('validation')=='evidence_supported' else '知识引用')
                           for r in relations if isinstance(r,dict) and isinstance(r.get('target'),str)]
        for reference, kind in references:
            target = resolver.resolve(reference,path)
            if not target:continue
            target=aliases.get(target,target)
            if target==origin:continue
            key = tuple(sorted((origin,target)))
            edge = edges.setdefault(key,{'source':key[0],'target':key[1],'types':[],'directions':[]})
            if kind not in edge['types']: edge['types'].append(kind)
            if [origin,target] not in edge['directions']: edge['directions'].append([origin,target])
    # Large safe Markdown files still appear as isolated nodes; never read their bodies.
    nodes = [{'id':f.relative,'path':f.relative,'title':f.title,'group':f.group,
              'type':f.type or 'other','kind':KINDS.get(f.type,f.group)} for f in catalog(root) if f.relative not in aliases]
    return {'nodes':nodes,'links':list(edges.values()),'source_aliases':aliases}


def relation_cards(root, relative):
    graph = graph_data(root)
    relative=graph['source_aliases'].get(relative,relative)
    nodes = {n['id']:n for n in graph['nodes']}
    result = []
    for edge in graph['links']:
        if relative not in (edge['source'],edge['target']): continue
        target = edge['target'] if edge['source']==relative else edge['source']
        node = nodes[target]
        labels = []
        if any(a==relative for a,b in edge['directions']): labels.extend(edge['types'])
        if any(b==relative for a,b in edge['directions']): labels.append('引用本页')
        result.append({'path':target,'title':node['title'],'kind':node['kind'],'relations':list(dict.fromkeys(labels))})
    return result
