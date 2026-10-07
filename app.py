"""Knowledge-tree workbench. Conversation attachments and library uploads are separate."""

import html
import os
from pathlib import Path
import sys
from time import perf_counter

from dotenv import load_dotenv
import streamlit as st

PROJECT_ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(PROJECT_ROOT/'src'))
load_dotenv(PROJECT_ROOT/'src'/'.env')

from document_loader import DocumentLoadError,load_uploaded_document
from wiki_browser import GROUPS,catalog,preview,safe_file,escape_label
from wiki_ui import IntakeService,expansion_dialog,resume_authorized,render_build_status


PHASE_LABELS={'claim_extractor':'理解项目材料','topic_selector':'选择面试主题','question_retrieval':'检索面试线索',
              'question_finalizer':'准备当前问题','interviewer':'等待回答','answer_evaluator':'评估回答','report_generator':'生成报告'}


def update_status(panel,event):
    label=PHASE_LABELS.get(event.get('stage'),'处理中')
    if event.get('status')=='started': panel.update(label=label,state='running')
    elif event.get('status')=='finished': panel.write(f"{label} · {event.get('elapsed_ms',0)/1000:.1f} 秒")
    elif event.get('status')=='failed': panel.update(label=label+'失败',state='error')


st.set_page_config(page_title='面试工作台',page_icon='💬',layout='wide',initial_sidebar_state='expanded')
st.markdown('''<style>
.stApp {background:#fff;color:#263340;}
[data-testid="stSidebar"] {background:#fafbfc;border-right:1px solid #e3e7eb;min-width:330px;max-width:330px;}
[data-testid="stSidebarContent"] {padding:1.05rem .8rem;}
[data-testid="stMainBlockContainer"] {max-width:1920px;padding:4.5rem 1.6rem 1.2rem;}
h1,h2,h3 {font-family:"Segoe UI","Microsoft YaHei",sans-serif;color:#24313e;letter-spacing:-.015em;}
h3 {font-size:1.12rem;}
[data-testid="stToolbar"] {display:none;}
[data-testid="stHeader"] {background:transparent;}
[data-testid="stSidebar"] [data-testid="stTooltipIcon"] {width:100%;}
[data-testid="stSidebar"] [data-testid="stTooltipHoverTarget"] {justify-content:flex-start!important;}
[data-testid="stSidebar"] .stButton button {width:100%!important;}
[data-testid="stSidebar"] .stButton button > div {width:100%;justify-content:flex-start!important;}
[data-testid="stSidebar"] .stButton button > div > span {width:100%;}
[data-testid="stSidebar"] .stButton [data-testid="stMarkdownContainer"] {width:100%;text-align:left!important;}
[data-testid="stSidebar"] .stButton p {text-align:left!important;}
[data-testid="stChatMessageAvatarAssistant"] {background:#eaf0f5;color:#426780;}
[data-testid="stChatMessageAvatarUser"] {background:#edf0f3;color:#637583;}
@media(max-width:1150px) {
 .st-key-workbench-layout [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] .st-key-knowledge-preview) {flex-direction:column;}
 .st-key-workbench-layout [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] .st-key-knowledge-preview) > [data-testid="stColumn"] {width:100%!important;flex:1 1 100%!important;}
}
[data-testid="stSidebar"] [data-testid="stExpander"] details {border:0!important;border-radius:0;background:transparent;}
[data-testid="stSidebar"] [data-testid="stExpander"] summary {background:transparent!important;border-radius:0;}
[data-testid="stSidebar"] [data-testid="stExpanderDetails"] [data-testid="stVerticalBlock"] {gap:.1rem;}
.st-key-knowledge-preview h1 {font-size:1.35rem;}
.st-key-knowledge-preview h2 {font-size:1.15rem;}
.st-key-knowledge-preview h3 {font-size:1rem;}
.wb-title {font-size:1.08rem;font-weight:650;margin:.2rem 0 1rem;color:#24313e;}
.wb-sub {font-size:.82rem;color:#778390;margin:-.55rem 0 1.1rem;}
.wb-toolbar {border-bottom:1px solid #e3e7eb;padding-bottom:.65rem;margin-bottom:.8rem;}
.wb-empty {padding:6rem .9rem 2.5rem;max-width:490px;}
.wb-empty h2 {font-size:1.55rem;line-height:1.5;font-weight:600;margin-bottom:.6rem;}
.wb-empty p {color:#74818e;font-size:.94rem;line-height:1.85;}
.wb-preview-tag {font-size:.75rem;color:#7c8794;border:1px solid #e1e6ec;display:inline-block;padding:.15rem .55rem;border-radius:4px;}
[data-testid="stSidebar"] .stButton button {border:0;background:transparent;box-shadow:none;text-align:left;justify-content:flex-start;font-size:.87rem;min-height:32px;padding:.35rem .45rem;color:#617081;}
[data-testid="stSidebar"] .stButton button[kind="primary"] {background:#e9edf2;color:#243647;}
[data-testid="stSidebar"] [data-testid="stExpander"] {border:0;background:transparent;}
[data-testid="stSidebar"] [data-testid="stExpanderDetails"] {padding:.15rem .05rem .45rem;}
[data-testid="stSidebar"] [data-testid="stExpander"] summary {padding:.5rem .05rem;font-size:.86rem;}
.stButton button[kind="primary"],.stFormSubmitButton button[kind="primary"] {background:#315f78;border-color:#315f78;}
button:focus-visible {outline:2px solid #768fbb!important;outline-offset:2px;}
[data-testid="stChatMessage"] {background:transparent;padding:.6rem .2rem;}
[data-testid="stChatInput"] {border-color:#dce3e9;border-radius:10px;background:#f8fafc;}
@media(max-width:1100px) {[data-testid="stMainBlockContainer"]{padding:4.5rem .8rem .8rem;} [data-testid="stMain"]>[data-testid="stVerticalBlock"]{gap:.7rem;}}
@media(prefers-reduced-motion:reduce) {*{scroll-behavior:auto!important;}}
</style>''',unsafe_allow_html=True)


def choose_file(relative):
    st.session_state['wiki_preview_file']=relative


def activate_session(id):
    record=st.session_state.get('workspace_sessions',{}).get(id)
    if record: st.session_state['interview_run']=record['run']


def render_sidebar(service):
    with st.sidebar:
        render_sidebar_contents(service)


@st.fragment(run_every='3s')
def render_sidebar_contents(service):
    files=catalog(service.root)
    rail,tree=st.columns([.65,4],gap='small')
    with rail:
        st.markdown('<div class="wb-title">▦</div>',unsafe_allow_html=True)
        for icon,label,value in [('💬','当前会话','sessions'),('📖','知识文件','knowledge'),('📂','来源资料','sources')]:
            if st.button(icon,help=label,key='nav-'+value,type='primary' if st.session_state.get('wb_nav','knowledge')==value else 'secondary'):
                st.session_state['wb_nav']=value
                st.rerun()
    with tree:
        nav=st.session_state.get('wb_nav','knowledge')
        st.markdown('<div class="wb-title">'+('面试会话' if nav=='sessions' else '面试知识库')+'</div>',unsafe_allow_html=True)
        st.markdown('<div class="wb-sub">'+('仅在本次运行保存' if nav=='sessions' else '文件与来源 · 只读浏览')+'</div>',unsafe_allow_html=True)
        if nav=='sessions':
            sessions=st.session_state.get('workspace_sessions',{})
            if not sessions: st.caption('开始面试后，会话会显示在这里。')
            for id,record in reversed(list(sessions.items())):
                if st.button(escape_label(record['name']),key='session-'+id,width='stretch'):
                    activate_session(id);st.rerun()
            return
        selected=st.session_state.get('wiki_preview_file')
        for name,english in GROUPS:
            if nav=='sources' and name!='资料': continue
            group=[item for item in files if item.group==name]
            with st.expander(f"{'📁' if name in ('概览','资料') else '◇'} {name} · {len(group)}",expanded=bool(group)):
                if not group: st.caption('暂无文件')
                folders={str(Path(item.relative).parent) for item in group}
                for folder in sorted(folders):
                    if folder!='.' and (name=='资料' or '/' in folder): st.caption(folder+'/')
                    for item in [file for file in group if str(Path(file.relative).parent)==folder]:
                        if st.button(escape_label(item.title),key='file-'+item.relative,
                                     type='primary' if selected==item.relative else 'secondary',width='stretch',help=item.relative):
                            choose_file(item.relative);st.rerun()


def render_history(state):
    evaluations=state.get('evaluation_history',[])
    for index,turn in enumerate(state.get('interview_history',[])):
        with st.chat_message('assistant',avatar=':material/forum:'): st.write(turn['question'])
        with st.chat_message('user',avatar=':material/person:'): st.write(turn['answer'])
        if index<len(evaluations):
            value=evaluations[index]['evaluation']
            with st.expander(f"第 {index+1} 轮反馈 · {value['rating']}"):
                st.write(value['feedback'])
                for point in value.get('missing_points',[]): st.write('可补充：'+point)


def render_report(report):
    st.success('本次面试已完成')
    st.subheader('面试报告')
    st.write(report['overall_summary'])
    if report['strengths']:
        st.markdown('**表现较好的地方**')
        for item in report['strengths']: st.write('• '+item)
    st.markdown('**需要补强**')
    for item in report['weaknesses']:
        with st.expander(item['topic']):
            st.write(item['reason']);st.caption('建议：'+item['study_action'])
    st.markdown('**下一轮练习重点**')
    for item in report['next_practice_focus']: st.write('• '+item)


def dismiss_interview():
    st.session_state.pop('workbench_dialog',None)


@st.dialog('新建面试',width='large',on_dismiss=dismiss_interview)
def interview_dialog():
    st.write('添加简历和项目材料。它们只用于本场面试，不会写入知识库。')
    nonce=st.session_state.get('interview_setup_nonce',0)
    resume=st.file_uploader('简历（仅当前会话）',type=['pdf','md','doc','docx'],max_upload_size=8,key=f'wb-resume-{nonce}')
    project=st.file_uploader('项目材料（仅当前会话）',type=['pdf','md','doc','docx'],max_upload_size=8,key=f'wb-project-{nonce}')
    turns=st.select_slider('最多提问轮数',options=[1,2,3,5,8],value=5,key='wb-turns')
    st.caption('开始面试后，解析文字和回答会发送给配置的LLM，可能产生费用；当前会话暂不支持跨重启恢复。')
    if st.button('开始模拟面试',type='primary',key='wb-start-interview'):
        if resume is None or project is None:
            st.error('请同时上传简历和项目材料。');return
        if not all(os.getenv(key) for key in ('LLM_MODEL','LLM_API_KEY','LLM_BASE_URL')):
            st.error('请先配置模型服务。');return
        from interview_session import start_interview
        try:
            with st.status('读取材料并准备第一题',expanded=True) as progress:
                started=perf_counter()
                documents=[load_uploaded_document(file.name,file.getvalue()) for file in (resume,project)]
                elapsed=round((perf_counter()-started)*1000)
                run=start_interview(documents,turns,document_ms=elapsed,on_progress=lambda event:update_status(progress,event))
            sessions=st.session_state.setdefault('workspace_sessions',{})
            sessions[run.thread_id]={'run':run,'name':Path(project.name).stem[:35]}
            while len(sessions)>6: sessions.pop(next(iter(sessions)))
            st.session_state['interview_run']=run
            st.session_state['interview_setup_nonce']=nonce+1
            st.session_state.pop('workbench_dialog',None)
            st.rerun()
        except DocumentLoadError as error: st.error(str(error))
        except Exception: st.error('面试启动失败，原会话保留。请检查模型服务或文件内容。')


def render_conversation(service):
    run=st.session_state.get('interview_run')
    heading,new,expand=st.columns([2.4,1.1,1.8])
    with heading:
        st.markdown('<div class="wb-title">面试工作台</div>',unsafe_allow_html=True)
    with new:
        if st.button('新建面试',key='open-interview',width='stretch'): st.session_state['workbench_dialog']='interview'
    with expand:
        if st.button('拓展现有知识库',key='open-knowledge-expansion',width='stretch'): st.session_state['workbench_dialog']='knowledge'
    st.markdown('<div class="wb-toolbar"></div>',unsafe_allow_html=True)
    notice=st.session_state.pop('wiki_started_notice_v4',None)
    if notice: st.toast(notice)
    if run:
        state=run.state
        st.caption(f"当前面试 · 已完成 {state.get('turn_count',0)} / {state.get('max_turns',5)} 轮")
    else:
        state={}
        st.caption('面试材料仅用于当前会话；知识库只接收明确授权的拓展资料。')
    question=run.question if run else None
    with st.container(height=540,border=False,key='conversation-canvas'):
        if run:
            render_history(state)
            if question:
                with st.chat_message('assistant',avatar=':material/forum:'):
                    st.caption(f"第 {state.get('turn_count',0)+1} 轮 · 当前问题")
                    st.write(question['question'])
                    if question.get('technology'): st.caption('技术方向：'+'、'.join(question['technology']))
                if state.get('retrieval_status') not in (None,'ok'):
                    st.caption('本轮已使用可用参考资料；部分检索能力未完整执行。')
            elif isinstance(state.get('interview_report'),dict): render_report(state['interview_report'])
            elif state.get('interview_status')=='no_claims': st.warning('材料中没有可追问的技术主张，请补充项目材料。')
            else: st.info('当前没有待回答的问题。')
        else:
            st.markdown('<div class="wb-empty"><h2>从你的项目经历，开始一次面试。</h2><p>先添加简历和项目材料，再按自己的节奏回答。<br>左侧可以浏览已有知识；需要补充知识时，使用上方专用入口。</p></div>',unsafe_allow_html=True)
    answer=st.chat_input('结合真实经历回答当前问题',max_chars=3000,disabled=question is None,key='workbench-answer')
    if answer and run:
        try:
            with st.status('评估回答并准备下一步',expanded=True) as progress:
                run.resume(answer,on_progress=lambda event:update_status(progress,event))
            st.rerun()
        except ValueError as error: st.error(str(error))
        except Exception: st.error('回答处理失败，本轮状态保留，请稍后重试。')
    if run and state.get('stage_metrics'):
        metrics=state['stage_metrics']; elapsed=getattr(run,'document_ms',0)+sum(item.get('elapsed_ms',0) for item in metrics)
        st.caption(f"阶段累计 {elapsed/1000:.1f} 秒 · 模型调用 {sum(item.get('model_calls',0) for item in metrics)} 次")


@st.fragment(run_every='3s')
def render_preview(service):
    files=catalog(service.root)
    st.markdown('<span class="wb-preview-tag">只读预览</span>',unsafe_allow_html=True)
    selected=st.session_state.get('wiki_preview_file')
    if not selected:
        selected=next((file.relative for file in files if file.relative=='overview.md'),None) or next((file.relative for file in files if file.relative=='index.md'),None)
        if selected: st.session_state['wiki_preview_file']=selected
    with st.container(height=750,border=True,key='knowledge-preview'):
        if not selected:
            st.subheader('选择一个知识文件');st.caption('从左侧文件树打开正文与来源。不会改变当前对话。');return
        try: item,body,truncated=preview(service.root,selected)
        except (OSError,ValueError) as error: st.warning(str(error));return
        st.subheader(escape_label(item.title));st.caption(item.relative)
        if item.status and item.status!='approved': st.caption('仅预览，未作为已发布知识参与检索。')
        if truncated: st.info('仅显示前120000字符，原文件未修改。')
        first,*rest=body.lstrip().splitlines() if body.strip() else [""]
        if first.strip()=="# "+item.title: body="\n".join(rest).lstrip()
        st.markdown(body,unsafe_allow_html=False)
        if item.sources:
            st.divider();st.markdown('**来源文件**')
            by_path={file.relative:file for file in files}
            for source in item.sources:
                try: safe_file(service.root,source)
                except ValueError: st.caption('来源不可用：'+source);continue
                label=by_path[source].title if source in by_path else Path(source).name
                st.button('↗ '+escape_label(label),key='preview-source-'+source,on_click=choose_file,args=(source,),width='stretch')


def main():
    service=IntakeService()
    try: resume_authorized(service)
    except Exception: pass  # Optional library recovery must not block the interview workbench.
    render_sidebar(service)
    with st.container(key='workbench-layout'):
        conversation,document=st.columns([1.85,1.05],gap='medium')
        with conversation: render_conversation(service)
        with document: render_preview(service)
    if st.session_state.get('workbench_dialog')=='interview': interview_dialog()
    elif st.session_state.get('workbench_dialog')=='knowledge': expansion_dialog(service)


if __name__=='__main__': main()
