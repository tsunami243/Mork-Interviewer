"""Shared relation records: distinguish structural references from evidence-backed proposals."""
def relation_record(target,kind='reference',reason='',source_evidence=(),target_evidence=(),*,origin='generated',supported=False):
    if kind not in ('reference','source','entity','concept'):raise ValueError('关系类型无效')
    if not isinstance(target,str) or not isinstance(reason,str) or len(reason)>500:raise ValueError('关系描述无效')
    return {'target':target,'kind':kind,'reason':reason,'source_evidence':list(source_evidence),
            'target_evidence':list(target_evidence),'origin':origin,
            'validation':'evidence_supported' if supported else 'structural_only'}


def proposal_relations(pages,data,catalog,aliases):
    from wiki_execution import event
    ids={p['id']:p for p in pages};registry=data.get('evidence_registry',{})
    total=0
    for page in pages:
        declared=page.pop('relation_proposals',[])
        if not isinstance(declared,list) or len(declared)>40:raise ValueError('页面关系规划无效')
        records={}
        for raw in declared:
            target=aliases.get(raw.get('target'),raw.get('target'))
            if target not in page['links']:raise ValueError('关系规划必须对应实际页面链接')
            refs=raw.get('evidence_ids',[])
            if not isinstance(refs,list) or len(refs)>4 or any(not isinstance(e,str) or e.lower() not in registry for e in refs):raise ValueError('关系证据编号无效')
            refs=[e.lower() for e in refs]
            # Existing target text not cited by this source remains a structural-only reference.
            own={e.get('id') for e in page.get('evidence',[])}
            other={e.get('id') for e in ids.get(target,{}).get('evidence',[])}
            shared=own&other&set(refs)
            kind=raw.get('kind','reference')
            records[target]=relation_record(target,kind,raw.get('reason',''),refs,sorted(shared),supported=bool(shared))
        for target in page['links']:
            if target not in records:
                records[target]=relation_record(target,'source' if target.startswith('source-') else 'reference',
                    '来源摘要引用' if target.startswith('source-') else '已解析的Wiki引用；未提供双端语义依据',origin='compatibility')
        page['relations']=list(records.values());total+=len(records)
    event('relationships','validated',relation_count=total,message='结构可解析与双端证据支持分开记录，不把存在链接当成语义已验证')
