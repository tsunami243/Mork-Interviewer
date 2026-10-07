import {useEffect,useRef,useState} from 'react';
import {Check,FileText,GitBranch,LoaderCircle,X} from 'lucide-react';
import {api,jsonPost} from './api';
import {Modal} from './Modal';
import {ExecutionDetails} from './ExecutionDetails';
import type {UploadEntry} from './types';

interface Suggestion {id:string;source:string;target:string;target_title:string;type:string;reason:string;source_quote:string;target_quote:string;link:string}
interface Action {id:string;kind:'stage'|'links'|'weak_links';status:string;error:string;job_id:string;stage:number;paths:string[];suggestions:Suggestion[];changed_paths:string[];index_status:string;retrieval:string[]}
interface Activity {pending:{path:string;title:string;hash:string}[];unlinked:{path:string;title:string;hash:string}[];actions:Action[]}

export function KnowledgeActivity({uploads,onRefresh,onOpen}:{uploads:UploadEntry[];onRefresh:()=>void;onOpen:(path:string)=>void}){
 const [activity,setActivity]=useState<Activity>({pending:[],unlinked:[],actions:[]}),[open,setOpen]=useState(false);
 const refreshRef=useRef(onRefresh),signature=useRef('');refreshRef.current=onRefresh;
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[selected,setSelected]=useState<Record<string,string[]>>({});
 async function load(){const data=await api<Activity>('/knowledge/activity');setActivity(data);return data}
 useEffect(()=>{let live=true;const poll=async()=>{try{const data=await api<Activity>('/knowledge/activity');if(live){setActivity(old=>JSON.stringify(old)===JSON.stringify(data)?old:data);const next=JSON.stringify(data.actions.map(a=>[a.id,a.status,a.index_status]));if(next!==signature.current){signature.current=next;refreshRef.current()}}}catch{/* Main connection status is already displayed by the workbench. */}};void poll();const timer=setInterval(()=>void poll(),2500);return()=>{live=false;clearInterval(timer)}},[]);
 const core=uploads.filter(u=>u.job?.stage==='core'&&u.job?.status==='completed');
 const working=activity.actions.filter(a=>a.status==='running'||a.status==='applying');
 const reviews=activity.actions.filter(a=>(a.kind==='links'||a.kind==='weak_links')&&a.status==='review');
 const weakReviews=reviews.filter(a=>a.kind==='weak_links');
 const attention=activity.pending.length+activity.unlinked.length+reviews.length;
 async function execute(operation:()=>Promise<unknown>){setBusy(true);setError('');try{await operation();await load();onRefresh()}catch(e){setError((e as Error).message)}finally{setBusy(false)}}
 function choices(action:Action){return selected[action.id]??action.suggestions.map(s=>s.id)}
 function toggle(action:Action,id:string){const current=choices(action);setSelected(old=>({...old,[action.id]:current.includes(id)?current.filter(x=>x!==id):[...current,id]}))}
 return <>
  <div className="knowledge-strip" aria-live="polite"><span>{attention?`发现 ${activity.pending.length} 个新增文件 · ${activity.unlinked.length} 个孤立 Wiki · ${reviews.length} 份方案待确认`:working.length?'知识任务正在处理，可继续面试':core.length?'核心知识已生成，后续阶段需明确启动':'专用上传自动建库，复制文件补链需确认'}</span><button className="quiet-button" onClick={()=>setOpen(true)}><GitBranch size={15}/>{attention?'查看补链建议':'知识任务与补链'}</button></div>
  {activity.pending.length>0&&!open&&<aside className="new-files-notice" role="status"><FileText size={18}/><div><strong>发现新增 Wiki 文件</strong><p>{activity.pending.slice(0,2).map(f=>f.title).join('、')}{activity.pending.length>2?'等':''}</p><small>尚未调用模型，也未修改文件。</small></div><button className="secondary-button" onClick={()=>setOpen(true)}>查看补链建议</button></aside>}
  {open&&<Modal title="知识任务与补链" onClose={()=>{if(!busy)setOpen(false)}}><div className="modal-body knowledge-activity">
   {error&&<div className="error-banner" role="alert">{error}</div>}
    {activity.pending.length>0&&<section className="knowledge-step"><h3>新增文件 · 待授权分析</h3><p>文件复制已完成。点击下方按钮才调用模型及可能的 Embedding 检索；不会直接写入补链。</p><ul>{activity.pending.map(f=><li key={f.path}><button className="quiet-button" onClick={()=>{onOpen(f.path);setOpen(false)}}>{f.title}</button><small>{f.path}</small></li>)}</ul><div className="step-buttons"><button className="primary-button" disabled={busy} onClick={()=>void execute(()=>jsonPost('/knowledge/relink',{paths:activity.pending.map(f=>f.path)}))}>分析并生成补链方案</button><button className="quiet-button" disabled={busy} onClick={()=>void execute(()=>jsonPost('/knowledge/relink/dismiss',{paths:activity.pending.map(f=>f.path)}))}>本次忽略</button></div></section>}
    {activity.unlinked.length>0&&<section className="knowledge-step"><h3>目录弱引用巡检 · 待授权分析</h3><p>这些已审核 Wiki 当前没有知识连接。系统会结合标题、正文、关键词和可用索引提出弱引用建议，不会自动写入，也不会改变检索发布状态。</p><ul>{activity.unlinked.slice(0,12).map(f=><li key={f.path}><button className="quiet-button" onClick={()=>{onOpen(f.path);setOpen(false)}}>{f.title}</button><small>{f.path}</small></li>)}</ul><div className="step-buttons"><button className="primary-button" disabled={busy} onClick={()=>void execute(()=>jsonPost('/knowledge/relink/weak',{}))}>分析孤立文件关系</button></div></section>}
   {working.map(a=><section key={a.id} className="knowledge-step"><h3><LoaderCircle size={16} className="spin"/>{a.kind==='links'?'正在生成补链方案':a.stage===2?'正在整理知识导航':'正在生成综合与参考题'}</h3><p>只执行你已授权的任务，不会自动进入下一阶段。</p><ExecutionDetails jobId={a.id} scope="actions"/></section>)}
    {reviews.map(a=><section key={a.id} className="knowledge-step"><h3>{a.kind==='weak_links'?'弱引用方案 · 待确认写入':'补链方案 · 待确认写入'}</h3><p>{a.kind==='weak_links'?'只追加用户确认的弱引用，并保留弱引用标记；不会把主题相关误认为事实关系。':'只向新增文件追加链接，不覆盖正文，不授予检索发布资格。'}{a.retrieval.includes('semantic+keyword')?'本次使用语义与关键词召回。':'本次使用关键词候选召回。'}</p>
    {!a.suggestions.length&&<p>没有找到充分依据，建议不补链。</p>}
    {a.suggestions.map(s=><label key={s.id} className="link-suggestion"><input type="checkbox" checked={choices(a).includes(s.id)} onChange={()=>toggle(a,s.id)}/><div><strong>{s.source} → {s.target_title}</strong><small>{s.type} · {s.link}</small><p>{s.reason}</p><blockquote><span>新增文件：</span>{s.source_quote}<br/><span>关联文件：</span>{s.target_quote}</blockquote></div></label>)}
    <div className="step-buttons"><button className="primary-button" disabled={busy||!choices(a).length} onClick={()=>void execute(()=>jsonPost(`/knowledge/actions/${a.id}/apply`,{ids:choices(a)}))}><Check size={15}/>确认写入所选链接</button><button className="quiet-button" disabled={busy} onClick={()=>void execute(()=>jsonPost(`/knowledge/actions/${a.id}/dismiss`,{}))}><X size={15}/>取消方案</button></div>
   </section>)}
   {uploads.filter(u=>u.job?.stage==='legacy'&&u.job?.status==='completed').map(upload=><section key={upload.id} className="knowledge-step"><h3>{upload.name} · 旧版知识已保留</h3><p>可按新规则补齐来源、概念与实体。仅点击下方按钮才重新分析原件，可能产生模型费用。</p><button className="secondary-button" disabled={busy} onClick={()=>void execute(()=>jsonPost('/knowledge/expand',{upload_ids:[upload.id]}))}>按新规则补齐核心知识</button></section>)}
   {uploads.filter(upload=>upload.job?.status==='failed').map(upload=><section key={upload.id} className="knowledge-step"><h3>{upload.name} · 已停止</h3><p>{upload.error||upload.job?.error}</p><ExecutionDetails jobId={upload.job!.id} onResume={()=>execute(()=>jsonPost('/knowledge/expand',{upload_ids:[upload.id]}))}/></section>)}
   {core.map(upload=>{const job=upload.job!;const rows=activity.actions.filter(a=>a.job_id===job.id);const second=rows.find(a=>a.stage===2&&a.status==='completed'),third=rows.find(a=>a.stage===3&&a.status==='completed');const processing=rows.some(a=>a.status==='running');return <section key={upload.id} className="knowledge-step"><h3>{upload.name}</h3><p>第一阶段已完成 · {job.pages||0} 张核心页面 · {job.index_status==='ready'?'检索索引就绪':'索引尚未就绪'}。可以结束并使用已有知识。</p><ExecutionDetails jobId={job.id}/>{job.warning&&<p className="index-warning">{job.warning}</p>}<div className="stage-track"><span className="done">1 来源 / 概念 / 实体</span><span className={second?'done':''}>2 导航整理</span><span className={third?'done':''}>3 综合 / 参考题</span></div>{!third&&<button className="secondary-button" disabled={busy||processing} onClick={()=>void execute(()=>jsonPost('/knowledge/continue',{job_id:job.id,stage:second?3:2}))}>{processing?'当前阶段处理中':second?'继续生成 · 综合与参考题':'继续生成 · 知识导航'}</button>}</section>})}
   {activity.actions.filter(a=>a.status==='failed').map(a=><section key={a.id} className="knowledge-step"><h3>任务未完成</h3><p>{a.error}</p><ExecutionDetails jobId={a.id} scope="actions" onResume={a.kind==='stage'?()=>execute(()=>jsonPost('/knowledge/continue',{job_id:a.job_id,stage:a.stage})):undefined}/><button className="quiet-button" disabled={busy} onClick={()=>void execute(()=>jsonPost(`/knowledge/actions/${a.id}/dismiss`,{}))}>关闭记录</button></section>)}
   {activity.actions.filter(a=>a.status==='completed').slice(0,5).map(a=><section key={a.id} className="knowledge-step"><h3>{a.kind==='links'?'补链已完成':a.stage===2?'导航整理已完成':'知识深化已完成'}</h3>{a.index_status==='failed'&&<p className="index-warning">知识已保存，但索引更新失败；未将此状态标记为检索就绪。</p>}<ExecutionDetails jobId={a.id} scope="actions"/><div className="changed-files">{a.changed_paths.map(path=><button key={path} className="quiet-button" onClick={()=>{onOpen(path);setOpen(false)}}>{path}</button>)}</div></section>)}
   {!core.length&&!activity.pending.length&&!activity.unlinked.length&&!activity.actions.length&&<p>暂无任务。通过“拓展现有知识库”添加资料并启动核心阶段，或将 Markdown 复制到知识目录。</p>}
  </div></Modal>}
 </>
}
