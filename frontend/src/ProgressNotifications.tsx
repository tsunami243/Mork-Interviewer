import {useEffect,useId,useRef,useState} from 'react';
import {AlertCircle,CheckCircle2,Circle,FileText,LoaderCircle,RefreshCw,X} from 'lucide-react';
import {ExecutionDetails} from './ExecutionDetails';
import type {UploadEntry} from './types';
import {needsNotification,overallProgress,progressView} from './progress';
import type {TransferEntry} from './progress';

export function ProgressNotifications({uploads,incoming,ids,version,onRetry}:{uploads:UploadEntry[];incoming:TransferEntry[];ids:string[];version:number;onRetry:(id:string)=>Promise<void>}){
 const seen=useRef<Set<string>>(new Set());
 const dock=useRef<HTMLElement>(null),trigger=useRef<HTMLButtonElement>(null),closing=useRef(false),detailsId=useId();
 const [open,setOpen]=useState(false);
 const keyboard=useRef(false),leaveTimer=useRef<ReturnType<typeof setTimeout>|undefined>(undefined);
 const clearLeave=()=>clearTimeout(leaveTimer.current);
 useEffect(()=>()=>clearLeave(),[]);
 useEffect(()=>{if(!open)return;const outside=(event:PointerEvent)=>{if(!dock.current?.contains(event.target as Node))setOpen(false)};document.addEventListener('pointerdown',outside);return()=>document.removeEventListener('pointerdown',outside)},[open]);
 const [retrying,setRetrying]=useState<string|null>(null),[errors,setErrors]=useState<Record<string,string>>({});
 useEffect(()=>{seen.current=new Set(ids);setOpen(false);setErrors({})},[version]);
 for(const row of uploads){if(needsNotification(row))seen.current.add(row.id)}
 const priority=(row:UploadEntry)=>{const view=progressView(row);return view.steps.some(s=>s.state==='running')?0:view.error?1:view.complete?3:2};
 const records=uploads.filter(row=>ids.includes(row.id)||seen.current.has(row.id)||needsNotification(row)).sort((a,b)=>priority(a)-priority(b));
 const rows=[...incoming,...records];
 const summary=overallProgress(rows);
 useEffect(()=>{if(!rows.length)setOpen(false)},[rows.length]);
 function collapse(){clearLeave();keyboard.current=false;setOpen(false);closing.current=true;trigger.current?.focus();closing.current=false}
 async function retry(id:string){setRetrying(id);setErrors(old=>({...old,[id]:''}));try{await onRetry(id)}catch(e){setErrors(old=>({...old,[id]:(e as Error).message}))}finally{setRetrying(null)}}
 if(!rows.length)return null;
 return <aside ref={dock} className="progress-notifications" aria-label="文件处理进度通知"
  onPointerEnter={event=>{if(event.pointerType==='mouse'||event.pointerType==='pen'){clearLeave();keyboard.current=false;setOpen(true)}}}
  onPointerLeave={event=>{if(event.pointerType==='touch'||keyboard.current)return;clearLeave();leaveTimer.current=setTimeout(()=>setOpen(false),150)}}
  onFocusCapture={event=>{if(!closing.current&&event.target.matches(':focus-visible')){clearLeave();keyboard.current=true;setOpen(true)}}}
  onBlurCapture={event=>{if(!dock.current?.contains(event.relatedTarget as Node|null))setOpen(false)}}
  onKeyDown={event=>{if(event.key==='Escape'){event.preventDefault();event.stopPropagation();collapse()}}}>
  <button ref={trigger} className={'progress-summary '+(summary.complete?'is-complete':summary.failed&&!summary.busy?'is-failed':'')} aria-label={`文件处理总进度 ${summary.percent}%，查看详细进度，${summary.count} 份资料`} aria-expanded={open} aria-controls={detailsId} onClick={event=>{clearLeave();keyboard.current=event.detail===0;setOpen(value=>!value)}}>
   <span className="progress-ring" aria-hidden="true"><svg viewBox="0 0 32 32"><circle className="progress-ring-track" cx="16" cy="16" r="13"/><circle className="progress-ring-value" cx="16" cy="16" r="13" strokeDasharray={2*Math.PI*13} strokeDashoffset={2*Math.PI*13*(1-summary.percent/100)}/></svg><span>{summary.complete?<CheckCircle2 size={12}/>:summary.busy?<LoaderCircle size={12} className="spin"/>:summary.failed?<AlertCircle size={12}/>:<Circle size={10}/>}</span></span><strong>{summary.percent}%</strong>
  </button>
  <div id={detailsId} className="progress-details" hidden={!open}><div className="notification-count">{rows.length} 份资料 · 本机知识库</div>{rows.map(row=>{const view=progressView(row);return <section key={row.id} className={'progress-notification '+(view.complete?'is-complete':view.error?'is-failed':'')} aria-label={row.name+'处理进度'} data-upload-id={row.id} data-state={view.complete?'completed':view.error?'failed':'running'}>
  <header className="notification-header" aria-live="polite"><div className="notification-file-icon"><FileText size={19}/></div><div><h3>{view.title}</h3><p>{row.name}</p></div><button className="icon-button" aria-label="收起处理详情" onClick={collapse}><X size={16}/></button></header>
  <div className="notification-progress-label"><span>阶段进度</span><strong>{view.percent}%</strong></div><div className="notification-progress-track" role="progressbar" aria-label={row.name+'阶段进度'} aria-valuemin={0} aria-valuemax={100} aria-valuenow={view.percent}><span style={{width:view.percent+'%'}}/></div>
  <ol className="notification-steps">{view.steps.map(step=><li key={step.label} className={'notification-step '+step.state} data-state={step.state}>{step.state==='completed'?<CheckCircle2 size={17}/>:step.state==='running'?<LoaderCircle size={17} className="spin"/>:step.state==='failed'?<AlertCircle size={17}/>:<Circle size={17}/>}<div><strong>{step.label}</strong><span>{step.detail}</span></div></li>)}</ol>
  <footer className="notification-footer">{view.error?<><p role="alert">{view.error}</p><small>{'authorized' in row?'原件保留，失败步骤不会显示为已完成。':'上传未确认成功，请保留本地原件并重新选择。'}</small>{view.retryable&&<button className="quiet-button" disabled={!!retrying} onClick={()=>void retry(row.id)}><RefreshCw size={14}/>{retrying===row.id?'正在重新启动':'重新处理（可能产生模型费用）'}</button>}</>:<p>{view.complete?'核心知识已就绪；后续可在“知识任务与补链”继续。':'后台正在执行，仍可浏览文件或继续面试。收起详情不会取消任务。'}</p>}{errors[row.id]&&<p role="alert">{errors[row.id]}</p>}{'job' in row&&row.job&&<ExecutionDetails jobId={row.job.id} onResume={view.retryable?()=>onRetry(row.id):undefined}/>}</footer>
 </section>})}</div></aside>
}
