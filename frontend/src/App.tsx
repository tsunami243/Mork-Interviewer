import {lazy,Suspense,useEffect,useRef,useState} from 'react';
import type {ChangeEvent,FormEvent} from 'react';
import {ArrowLeft,ArrowUp,ArrowUpRight,BookOpen,Check,ChevronDown,ChevronRight,FileText,Folder,GitBranch,Layers,LoaderCircle,MessageSquare,Plus,ShieldCheck,Upload,X} from 'lucide-react';
import {api,jsonPost} from './api';
import {Modal} from './Modal';
const KnowledgeGraph=lazy(()=>import('./KnowledgeGraph').then(module=>({default:module.KnowledgeGraph})));

import {KnowledgeActivity} from './KnowledgeActivity';
import {ProgressNotifications} from './ProgressNotifications';
import type {TransferEntry} from './progress';
import {Markdown} from './Markdown';
import type {Bootstrap,FileDetail,LibraryFile,Session,UploadEntry} from './types';

const groupOrder=['概览','实体','概念','资料','参考题','综合'];
function uploadStatus(row:UploadEntry){
 if(!row.authorized)return '仅本机暂存';
 if(row.status==='failed')return '解析未完成，原件保留';
 if(!row.job)return '正在读取资料';
 if(row.job.status==='completed')return row.job.index_status==='ready'?`已完成 · ${row.job.pages||0} 张知识页`:'知识已保存，索引准备中';
 if(row.job.status==='failed')return row.job.attempts<3?'自动重试中':'处理未完成，资料保留';
 return '正在拓展知识库';
}

export default function App(){
 const [files,setFiles]=useState<LibraryFile[]>([]),[sessions,setSessions]=useState<Session[]>([]),[uploads,setUploads]=useState<UploadEntry[]>([]);
 const [ready,setReady]=useState(false),[configured,setConfigured]=useState(true),[tab,setTab]=useState<'interview'|'file'|'graph'>('interview');
 const [graphVisited,setGraphVisited]=useState(false);
 const [fromGraph,setFromGraph]=useState(false);
 const [sidebarOpen,setSidebarOpen]=useState(false);
 const [nav,setNav]=useState<'library'|'sessions'|'sources'|'graph'>('sessions'),[openGroups,setOpenGroups]=useState<Record<string,boolean>>({});
 const [selectedPath,setSelectedPath]=useState<string|null>(null),[detail,setDetail]=useState<FileDetail|null>(null),[fileLoading,setFileLoading]=useState(false),[fileError,setFileError]=useState('');
 const [activeId,setActiveId]=useState<string|null>(null),[drafts,setDrafts]=useState<Record<string,string>>({}),[sending,setSending]=useState(false);
 const [modal,setModal]=useState<'interview'|'expand'|null>(null),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const [resumeFile,setResumeFile]=useState<File|null>(null),[projectFile,setProjectFile]=useState<File|null>(null),[turns,setTurns]=useState(5),[starting,setStarting]=useState(false);
 const [staging,setStaging]=useState(false),[modalError,setModalError]=useState('');
 const [incoming,setIncoming]=useState<TransferEntry[]>([]),[notificationIds,setNotificationIds]=useState<string[]>([]),[notificationVersion,setNotificationVersion]=useState(0);
 const [refresh,setRefresh]=useState(0);
 const uploadRevision=useRef(0);
 const uploadSignature=useRef(''),pendingSubmit=useRef<Record<string,{text:string;id:string}>>({});
 const active=sessions.find(session=>session.id===activeId)||null;
 const draft=activeId?drafts[activeId]||'':'';
 const chatEnd=useRef<HTMLDivElement>(null),composer=useRef<HTMLTextAreaElement>(null);
 useEffect(()=>{let live=true;api<Bootstrap>('/bootstrap').then(data=>{if(!live)return;setFiles(data.files);setSessions(data.sessions);setUploads(data.uploads);setConfigured(data.configured);setActiveId(data.sessions[0]?.id||null);setReady(true)}).catch(e=>setError(e.message));return()=>{live=false}},[]);
 useEffect(()=>{
  if(!ready)return;
  let live=true;
  const poll=async()=>{try{const revision=uploadRevision.current;const [library,conversations,intake]=await Promise.all([api<{files:LibraryFile[]}>('/library'),api<{sessions:Session[]}>('/interviews'),api<{uploads:UploadEntry[]}>('/knowledge/uploads')]);if(!live)return;setFiles(old=>JSON.stringify(old)===JSON.stringify(library.files)?old:library.files);setSessions(conversations.sessions);if(revision===uploadRevision.current)setUploads(intake.uploads);const signature=JSON.stringify(intake.uploads.map(row=>[row.id,row.job?.status,row.job?.index_status]));if(signature!==uploadSignature.current){uploadSignature.current=signature;setRefresh(value=>value+1)}}catch{/* Keep valid existing UI data through a temporary polling error. */}};
  const interval=setInterval(poll,2200);return()=>{live=false;clearInterval(interval)};
 },[ready]);
 useEffect(()=>{
  if(!selectedPath)return;
  const controller=new AbortController();setFileLoading(true);setFileError('');
  api<FileDetail>('/library/file?path='+encodeURIComponent(selectedPath),{signal:controller.signal}).then(setDetail).catch(e=>{if(!controller.signal.aborted)setFileError(e.message)}).finally(()=>{if(!controller.signal.aborted)setFileLoading(false)});
  return()=>controller.abort();
 },[selectedPath,refresh]);
 useEffect(()=>{if(tab==='interview')chatEnd.current?.scrollIntoView({behavior:'smooth',block:'end'})},[active?.history?.length,active?.question?.question,tab]);
 useEffect(()=>{if(!notice)return;const timer=setTimeout(()=>setNotice(''),5000);return()=>clearTimeout(timer)},[notice]);
 function openFile(path:string){setSelectedPath(path);setTab('file');setNav(current=>current==='sources'?'sources':'library');setSidebarOpen(false)}
 function showModal(value:'interview'|'expand'){setModalError('');setModal(value)}
 async function beginInterview(event:FormEvent){
  event.preventDefault();if(!resumeFile||!projectFile){setModalError('请添加简历和项目材料');return}
  setStarting(true);setModalError('');const form=new FormData();form.append('resume_file',resumeFile);form.append('project_file',projectFile);form.append('max_turns',String(turns));
  try{const session=await api<Session>('/interviews',{method:'POST',body:form});setSessions(old=>[session,...old]);setActiveId(session.id);setTab('interview');setSidebarOpen(false);setModal(null);setResumeFile(null);setProjectFile(null)}catch(e){setModalError((e as Error).message)}finally{setStarting(false)}
 }
 async function sendAnswer(){
  if(!activeId||!active||active.busy||!draft.trim()||sending)return;
  const text=draft.trim();const previous=pendingSubmit.current[activeId];const request=previous?.text===text?previous:{text,id:crypto.randomUUID()};pendingSubmit.current[activeId]=request;
  setSending(true);setError('');
  try{const session=await jsonPost<Session>(`/interviews/${activeId}/answers`,{answer:text,request_id:request.id});setSessions(old=>old.map(row=>row.id===session.id?session:row));setDrafts(old=>({...old,[activeId]:''}));delete pendingSubmit.current[activeId]}
  catch(e){setError((e as Error).message)}finally{setSending(false)}
 }
 async function stageFiles(event:ChangeEvent<HTMLInputElement>){
  const added=Array.from(event.target.files||[]);if(!added.length)return;
  if(added.some(file=>file.size===0||file.size>8*1024*1024)){setModalError('每份资料须有内容且在8MB内');return}
  const input=event.target;
  const transfers:TransferEntry[]=added.map(file=>({id:crypto.randomUUID(),name:file.name,status:'uploading',error:''}));
  uploadRevision.current+=1;
  setStaging(true);setModalError('');setIncoming(transfers);setNotificationIds([]);setNotificationVersion(value=>value+1);setModal(null);
  const form=new FormData();added.forEach(file=>form.append('files',file));
  try{
   const data=await api<{selected:{id:string;name:string}[];uploads:UploadEntry[]}>('/knowledge/upload',{method:'POST',body:form});
   uploadRevision.current+=1;setUploads(data.uploads);setNotificationIds(data.selected.map(row=>row.id));setIncoming([]);
  }catch(e){uploadRevision.current+=1;setIncoming(transfers.map(row=>({...row,status:'failed',error:(e as Error).message})))}
  finally{setStaging(false);input.value=''}
 }
 async function retryKnowledge(id:string){
  uploadRevision.current+=1;
  const data=await jsonPost<{uploads:UploadEntry[]}>('/knowledge/expand',{upload_ids:[id]});
  uploadRevision.current+=1;setUploads(data.uploads);setNotificationIds(old=>Array.from(new Set([...old,id])));setNotificationVersion(value=>value+1);
 }
 const groupFiles=files.filter(file=>nav!=='sources'||file.group==='资料');
 return <div className="workbench-shell">
  <nav className="navigation-rail" aria-label="工作台导航"><div className="brand-mark"><Layers size={23}/></div><div className="rail-actions">
   <button className={tab==='interview'?'rail-button active':'rail-button'} aria-label="模拟面试场景" onClick={()=>{setNav('sessions');setTab('interview');setSidebarOpen(true)}}><MessageSquare size={21}/></button>
   <button className={tab==='file'&&nav==='library'?'rail-button active':'rail-button'} aria-label="知识文件" onClick={()=>{setNav('library');setTab('file');setSidebarOpen(true)}}><BookOpen size={21}/></button>
   <button className={tab==='file'&&nav==='sources'?'rail-button active':'rail-button'} aria-label="来源资料" onClick={()=>{setNav('sources');setTab('file');setSidebarOpen(true)}}><Folder size={21}/></button>
   <button className={tab==='graph'?'rail-button active':'rail-button'} aria-label="知识图谱场景" onClick={()=>{setGraphVisited(true);setNav('graph');setTab('graph');setSidebarOpen(false)}}><GitBranch size={21}/></button>
  </div><div className="rail-foot"><ShieldCheck size={18}/><span>本机</span></div></nav>
  <button className="sidebar-scrim" hidden={!sidebarOpen} aria-label="收起侧栏" onClick={()=>setSidebarOpen(false)}/><aside className={sidebarOpen?"library-sidebar mobile-open":"library-sidebar"}><header className="sidebar-heading"><h1>{nav==='sessions'?'面试会话':'面试知识库'}</h1><p>{nav==='sessions'?'会话保留至服务重启':'只读浏览 · 面试附件不入库'}</p></header><div className="sidebar-content">
   {nav==='sessions'?<div className="session-list">{sessions.length?sessions.map(session=><button key={session.id} className={activeId===session.id?'session-item selected':'session-item'} onClick={()=>{setActiveId(session.id);setTab('interview');setSidebarOpen(false)}}><MessageSquare size={16}/><span>{session.name}<small>{session.busy?'处理中':`${session.turn_count||0} / ${session.max_turns} 轮`}</small></span></button>):<p className="sidebar-empty">开始面试后，会话显示在这里。</p>}</div>:
   groupOrder.filter(group=>nav!=='sources'||group==='资料').map(group=>{const members=groupFiles.filter(file=>file.group===group),open=openGroups[group]??members.length>0;return <section className="file-group" key={group}><button className="group-heading" aria-expanded={open} onClick={()=>setOpenGroups(old=>({...old,[group]:!open}))}>{open?<ChevronDown size={14}/>:<ChevronRight size={14}/>}<Folder size={16} className={'group-icon '+(group==='概念'?'concept-icon':'')}/><span>{group}</span><small>{members.length}</small></button>{open&&<div className="group-children">{members.length?Array.from(new Set(members.map(file=>file.path.includes('/')?file.path.slice(0,file.path.lastIndexOf('/')):''))).sort().map(folder=><div key={folder} className="tree-subfolder">{folder&&(group==='资料'||folder.includes('/'))&&<div className="tree-folder-label">{folder}/</div>}{members.filter(file=>(file.path.includes('/')?file.path.slice(0,file.path.lastIndexOf('/')):'')===folder).map(file=><button key={file.path} className={selectedPath===file.path?'file-item selected':'file-item'} onClick={()=>openFile(file.path)} aria-label={'打开'+file.title}><FileText size={15}/><span>{file.title}</span></button>)}</div>):<p className="sidebar-empty">暂无文件</p>}</div>}</section>})}
  </div><div className="sidebar-footer"><span className="status-dot"/>已有文件只读 · 专用上传自动建库</div></aside>
  <main className="workspace-main"><header className="workspace-header"><div className="workspace-heading"><span className="workspace-kicker">项目复盘</span><h2>面试工作台</h2></div><div className="workspace-actions"><button className="secondary-button expand-trigger" disabled={!ready||staging} onClick={()=>showModal('expand')}><Plus size={16}/>拓展现有知识库</button><ProgressNotifications uploads={uploads} incoming={incoming} ids={notificationIds} version={notificationVersion} onRetry={retryKnowledge}/></div></header>
   {ready&&<KnowledgeActivity uploads={uploads} onRefresh={()=>setRefresh(value=>value+1)} onOpen={openFile}/>}
   {notice&&<div className="toast" role="status"><Check size={17}/>{notice}<button className="icon-button" aria-label="关闭提示" onClick={()=>setNotice('')}><X size={15}/></button></div>}
   {error&&<div className="error-banner" role="alert">{error}<button className="icon-button" aria-label="关闭错误提示" onClick={()=>setError('')}><X size={15}/></button></div>}
   {!ready&&<div className="connecting"><LoaderCircle size={18} className="spin"/>正在连接本机工作台</div>}
   <section id="interview-panel" aria-label="模拟面试工作区" hidden={tab!=='interview'} className="interview-workspace"><div className="conversation-toolbar"><span>{active?`${active.name} · 已完成 ${active.turn_count||0} / ${active.max_turns} 轮`:'材料仅用于当前会话，不写入知识库'}</span><button className="quiet-button" onClick={()=>showModal('interview')}><Plus size={15}/>新建面试</button></div><div className="conversation-scroll">
    {!active?<div className="welcome-state"><div className="welcome-icon"><MessageSquare size={28}/></div><h3>从你的项目经历，开始一次面试。</h3><p>添加简历和项目材料，先讲清职责与整体方案，<br/>再沿着回答逐步深入。</p><button className="primary-button" onClick={()=>showModal('interview')}><Plus size={17}/>新建面试</button>{!configured&&<small>请先在本机配置模型服务。</small>}</div>:<div className="conversation-thread">
     {(active.history||[]).map((turn,index)=><div className="turn" key={index}><div className="chat-row assistant"><div className="avatar"><MessageSquare size={17}/></div><div className="chat-copy">{turn.question}</div></div><div className="chat-row user"><div className="avatar user-avatar">我</div><div className="chat-copy">{turn.answer}</div></div>{active.evaluations?.[index]&&<details className="feedback"><summary>第 {index+1} 轮反馈 · {active.evaluations[index].evaluation.rating}</summary><p>{active.evaluations[index].evaluation.feedback}</p>{active.evaluations[index].evaluation.missing_points?.map((point,j)=><p key={j}>可补充：{point}</p>)}</details>}</div>)}
     {active.question&&<div className="chat-row assistant current-question"><div className="avatar"><MessageSquare size={17}/></div><div className="chat-copy"><small>第 {(active.turn_count||0)+1} 轮 · 当前问题</small><p>{active.question.question}</p>{(active.question.technology?.length||0)>0&&<span className="technology-label">技术方向：{active.question.technology?.join('、')}</span>}</div></div>}
     {active.busy&&<div className="processing-note" role="status"><LoaderCircle size={16} className="spin"/>{active.phase}</div>}
     {active.error&&<div className="error-banner">{active.error}</div>}
     {active.report&&<article className="interview-report"><span className="report-label"><Check size={16}/>本次面试已完成</span><h3>面试报告</h3><p>{active.report.overall_summary}</p><h4>表现较好的地方</h4><ul>{active.report.strengths.map((line,i)=><li key={i}>{line}</li>)}</ul><h4>需要补强</h4>{active.report.weaknesses.map((item,i)=><div key={i}><strong>{item.topic}</strong><p>{item.reason}</p><small>建议：{item.study_action}</small></div>)}<h4>下一轮练习</h4><ul>{active.report.next_practice_focus.map((line,i)=><li key={i}>{line}</li>)}</ul></article>}
     <div ref={chatEnd}/></div>}
   </div><div className="composer-wrap"><div className="composer"><textarea ref={composer} aria-label="回答草稿" placeholder="结合真实经历回答当前问题" value={draft} maxLength={3000} disabled={!active?.question||active.busy||active.status==='error'} onChange={event=>activeId&&setDrafts(old=>({...old,[activeId]:event.target.value}))} onKeyDown={event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.nativeEvent.isComposing){event.preventDefault();void sendAnswer()}}}/><button className="send-button" aria-label="提交回答" disabled={!active?.question||active.busy||!draft.trim()||sending} onClick={()=>void sendAnswer()}>{sending||active?.busy?<LoaderCircle size={19} className="spin"/>:<ArrowUp size={20}/>}</button></div><p>Enter 提交 · Shift + Enter 换行 · 切换文件不会清空草稿</p></div></section>
   <section id="file-panel" aria-label="文件详情工作区" hidden={tab!=='file'} className="file-workspace">{fromGraph&&<button className="quiet-button return-graph" onClick={()=>{setTab('graph');setNav('graph')}}><ArrowLeft size={15}/>返回知识图谱</button>}{fileLoading?<div className="file-loading"><LoaderCircle className="spin" size={20}/>正在打开文件</div>:fileError?<div className="error-banner">{fileError}</div>:detail&&detail.path===selectedPath?<article className="document-layout"><header className="document-card"><div className="document-card-top"><div className="document-symbol"><FileText size={24}/></div><div><div className="document-category">{detail.kind} <span>· 只读</span></div><h3>{detail.title}</h3></div></div><div className="document-meta"><div><span>文件名称</span><strong>{detail.filename}</strong></div><div><span>文件类型</span><strong>{detail.format} · {(detail.bytes/1024).toFixed(1)} KB</strong></div><div><span>本地修改时间</span><strong>{new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',dateStyle:'medium',timeStyle:'short'}).format(new Date(detail.modified_at))}</strong></div></div><div className="document-path"><Folder size={14}/><span>{detail.path}</span></div>{detail.tags.length>0&&<div className="tag-row">{detail.tags.map(tag=><span key={tag}>{tag}</span>)}</div>}
     {((detail.original_sources?.length||0)>0||detail.source_summary)&&<div className="source-access">{detail.original_sources?.map(source=><button key={source.path} className="quiet-button" onClick={()=>openFile(source.path)}><FileText size={14}/>查看原文快照</button>)}{detail.source_summary&&<button className="quiet-button" onClick={()=>openFile(detail.source_summary!.path)}><ArrowLeft size={14}/>返回来源摘要</button>}<span>同一资料在图谱中只显示一个来源节点</span></div>}
     {detail.relations.length>0&&<div className="relation-section"><div className="relation-heading">关联文件 <span>{detail.relations.length}</span></div><div className="relation-grid">{detail.relations.map(file=><button className="relation-card" key={file.path} onClick={()=>openFile(file.path)} aria-label={'查看关联文件'+file.title}><div className="mini-file-icon"><FileText size={17}/></div><span><strong>{file.title}</strong><small>{file.relations.join(' / ')} · {file.kind}</small></span><ArrowUpRight size={15}/></button>)}</div></div>}
    </header>{detail.review_status==='automatic'&&<div className="document-note"><ShieldCheck size={15}/>此文件由系统编译，未经过人工审核；技术判断请核对来源。</div>}{detail.truncated&&<div className="document-note">仅预览前120000字符，原文件未修改。</div>}<div className="document-body"><Markdown body={detail.body.trimStart().replace(new RegExp('^# '+detail.title.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+'\\s*\\n'),'')}/></div></article>:<div className="file-empty"><div className="welcome-icon"><FileText size={28}/></div><h3>选择一个知识文件</h3><p>左侧文件树打开正文、基本信息与关联文件。<br/>文件查看区不会改动你的面试会话。</p></div>}</section>
   {graphVisited&&<Suspense fallback={<div className="connecting">正在加载图谱</div>}><KnowledgeGraph active={tab==='graph'} onOpen={path=>{setFromGraph(true);openFile(path)}}/></Suspense>}

  </main>
  {modal==='interview'&&<Modal title="新建面试" onClose={()=>!starting&&setModal(null)}><form className="modal-body" onSubmit={beginInterview}><p className="modal-description">两份材料只用于本场面试，不会自动加入知识库。</p><label className="upload-slot"><FileText size={21}/><span>简历<small>{resumeFile?.name||'PDF、Markdown、Word · 每份8MB内'}</small></span><input type="file" aria-label="简历文件" accept=".pdf,.md,.doc,.docx" onChange={event=>setResumeFile(event.target.files?.[0]||null)}/></label><label className="upload-slot"><Folder size={21}/><span>项目材料<small>{projectFile?.name||'README、设计文档 · 每份8MB内'}</small></span><input type="file" aria-label="项目材料文件" accept=".pdf,.md,.doc,.docx" onChange={event=>setProjectFile(event.target.files?.[0]||null)}/></label><label className="turn-selector">最多问答轮数<select value={turns} onChange={event=>setTurns(Number(event.target.value))}>{[1,2,3,5,8].map(value=><option key={value}>{value}</option>)}</select></label><p className="privacy-copy">开始后，解析文字与回答会发送给配置的模型服务，可能产生费用。会话暂不支持跨重启恢复。</p>{modalError&&<div className="error-banner" role="alert">{modalError}</div>}<button className="primary-button modal-submit" disabled={starting}>{starting?<LoaderCircle size={17} className="spin"/>:<ArrowUpRight size={17}/>}{starting?'正在准备':'开始模拟面试'}</button></form></Modal>}
  {modal==='expand'&&<Modal title="拓展现有知识库" onClose={()=>setModal(null)}><div className="modal-body"><p className="modal-description">此入口上传即开始建库；简历与项目面试附件仍不会入库。</p><div className="upload-consent"><ShieldCheck size={17}/><p>选择文件即授权解析、调用 LLM／Embedding 并生成核心知识，可能产生模型费用。后续导航与深化阶段仍需单独确认。</p></div><label className="drop-zone"><Upload size={25}/><strong>{staging?'正在上传':'上传资料并开始建库'}</strong><span>PDF、Markdown、Word · 多文件 · 每份8MB内</span><input type="file" aria-label="建库资料文件" multiple accept=".pdf,.md,.doc,.docx" disabled={staging} onChange={event=>void stageFiles(event)}/></label><p className="privacy-copy">选择文件后弹窗自动关闭，右上角通知展示真实阶段进度。资料文本会发送至你配置的模型服务。</p>{modalError&&<div className="error-banner" role="alert">{modalError}</div>}{uploads.some(row=>row.authorized)&&<div className="upload-history"><h4>近期处理资料</h4>{uploads.filter(row=>row.authorized).slice(0,8).map(row=><div key={row.id}><span>{row.name}</span><small>{uploadStatus(row)}</small></div>)}</div>}</div></Modal>}

 </div>;
}
