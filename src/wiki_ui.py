"""Explicit knowledge-expansion dialog. Upload is staging, not authorization."""

import hashlib
import os

import streamlit as st

from wiki_automatic import ensure_worker
from wiki_intake import IntakeService
from wiki_uploads import UploadStore


UPLOAD_KEY='wiki_expand_files_v4'


from model_config import configured_llm


def stage_uploads(service):
    files=st.session_state.get(UPLOAD_KEY) or []
    store=UploadStore(service)
    selected,errors=[],[]
    for file in files:
        try:
            record=store.receive(file.name,file.getvalue())  # No parse, task, model or index.
            selected.append(record['id'])
        except ValueError as error:
            errors.append(str(error))
        except Exception:
            errors.append('文件暂存失败，请重新添加')
    st.session_state['wiki_staged_ids_v4']=list(dict.fromkeys(selected))
    st.session_state['wiki_stage_errors_v4']=errors


def start_expansion(service, selected):
    if not selected:
        raise ValueError('请先添加用于知识库拓展的资料')
    store=UploadStore(service)
    for id in selected: store.get(id)  # Validate the whole selection before granting any permission.
    for id in selected:
        store.authorize(id)
    ensure_worker(service,configured_llm)
    return len(selected)


def resume_authorized(service):
    service.recover()
    UploadStore(service).recover()
    # Worker only sees authorized uploads / already authorized durable tasks.
    return ensure_worker(service,configured_llm)


def status_text(record,job):
    if not record.get('authorized'):
        return '已暂存，尚未用于建库'
    if job is None:
        return '正在读取资料' if record['status']!='failed' else '读取失败，原文件已保存'
    if job['status']=='completed':
        return f"已完成 · {job.get('pages') or 0} 张知识页" if job['index_status']=='ready' else '知识已保存，索引准备中'
    if job['status']=='failed':
        return '自动重试中' if job['attempts']<3 else '处理未完成，原文件仍保留'
    return '正在拓展知识库'


@st.fragment(run_every='2s')
def render_build_status(service):
    store=UploadStore(service)
    records=[record for record in store.list() if record.get('authorized')]
    if not records: return
    rows=[]
    for record in records:
        job=service.queue.display(record['job_id']) if record['job_id'] else None
        rows.append({'资料':record['name'],'状态':status_text(record,job)})
        if record['status']=='failed' and record['error']:
            st.warning(f"{record['name']}：{record['error']}")
        elif job and job['status']=='failed' and job['attempts']>=3:
            st.warning(f"{record['name']}：{job['error']}。可再次明确启动该资料的拓展。")
    st.dataframe(rows,hide_index=True,width='stretch')


def dismiss_expansion():
    st.session_state.pop('workbench_dialog',None)


@st.dialog('拓展现有知识库',width='large',on_dismiss=dismiss_expansion)
def expansion_dialog(service=None):
    service=service or IntakeService()
    st.markdown('添加**明确用于知识库**的资料。普通面试附件不会被带入这里。')
    st.file_uploader('添加建库资料',type=['pdf','md','doc','docx'],max_upload_size=8,
                     accept_multiple_files=True,key=UPLOAD_KEY,on_change=stage_uploads,args=(service,))
    st.caption('添加仅本机暂存；点击“开始拓展”后才会解析、调用模型、写入知识和更新索引，可能产生费用。')
    for error in st.session_state.get('wiki_stage_errors_v4',[]): st.error(error)
    selected=st.session_state.get('wiki_staged_ids_v4',[])
    store=UploadStore(service)
    names=[]
    for id in selected:
        try: names.append(store.get(id)['name'])
        except ValueError: pass
    if names: st.write('本次资料：'+'、'.join(names))
    if st.button('开始拓展',type='primary',key='wiki_authorize_v4'):
        try:
            count=start_expansion(service,selected)
            st.session_state['wiki_started_notice_v4']=f'已启动 {count} 份资料的知识库拓展；可继续当前面试。'
            st.session_state['wiki_staged_ids_v4']=[]
            st.session_state.pop('workbench_dialog',None)
            st.rerun()
        except ValueError as error: st.error(str(error))
        except Exception: st.error('拓展暂未启动，文件仍在本机，请稍后重试')
    render_build_status(service)
