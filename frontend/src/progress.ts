import type {UploadEntry} from './types';

export type StepState='pending'|'running'|'completed'|'failed';
export interface ProgressStep {label:string;state:StepState;detail:string}
export interface ProgressView {steps:ProgressStep[];percent:number;title:string;error:string;busy:boolean;complete:boolean;retryable:boolean}
export interface TransferEntry {id:string;name:string;status:'uploading'|'failed';error:string}
const labels=['接收与解析','分析资料','生成 Wiki 页面','整合与发布','更新检索索引'];
const positions:Record<string,number>={analysis:1,planning:2,generation:2,integration:3,index:4};

export function progressView(row:UploadEntry|TransferEntry):ProgressView{
 const transfer='authorized' in row?null:row;
 const record='authorized' in row?row:null;
 const job=record?.job;
 const complete=job?.status==='completed'&&job.index_status==='ready';
 const indexFailure=job?.status==='completed'&&job.index_status==='failed';
 const retrying=(job?.status==='failed'&&job.stage!=='core'&&job.attempts<3)||(indexFailure&&(job?.index_attempts||0)<3);
 const failed=transfer?.status==='failed'||record?.status==='failed'||(job?.status==='failed'&&!retrying)||(indexFailure&&!retrying);
 const waiting=!transfer&&(job?.status==='queued'||!job&&record?.status==='saved');
 const stopped=job?.status==='cancelled';
 const busy=!complete&&!failed&&!stopped;
 const detail=job?.progress;
 let current=0;
 if(job){
  if(job.status==='completed')current=4;
  else if(detail?.stage&&positions[detail.stage]!==undefined)current=positions[detail.stage];
  else if(job.phase==='compile')current=2;
  else if(['review','publish','published'].includes(job.phase))current=3;
  else current=1;
 }
 let ratio=detail&&detail.total>0?Math.max(0,Math.min(1,detail.completed/detail.total)):0;
 if(job?.status==='completed'&&detail?.stage!=='index')ratio=0;
 let percent=complete?100:Math.min(99,Math.round((current+ratio)*20));
 if(indexFailure)percent=80;
 if(transfer||!job)percent=0;
 let runningDetail=transfer?'正在上传至本机':!job?record?.status==='parsing'?'正在解析文件':'已接收，等待解析':detail?.stage==='planning'?'正在规划知识页面':detail?.stage==='generation'?`${detail.completed} / ${detail.total} 张页面已生成`:detail?.stage==='analysis'?`${detail.completed} / ${detail.total} 个分段已分析`:current===3?'正在校验来源、关联并整合发布':current===4?'正在更新 Embedding 检索索引':'等待分析资料';
 if(retrying)runningDetail=`正在重试 · ${current===4?job?.index_attempts:job?.attempts} / 3 次`;
 const steps=labels.map((label,i):ProgressStep=>{
  let state:StepState=complete||i<current?'completed':i===current?'running':'pending';
  if(i===current&&waiting)state='pending';
  if(i===current&&(failed||stopped))state='failed';
  else if(i===current&&!retrying&&detail?.total&&detail.completed>=detail.total&&i<4)state='completed';
  return {label,state,detail:state==='completed'?'已完成':state==='pending'?i===current&&waiting?'等待处理':'未开始':state==='failed'?stopped?'已取消':'处理失败':runningDetail};
 });
 return {steps,percent,title:complete?'核心知识处理完成':waiting?'已接收，等待处理':failed?'文件处理异常':stopped?'处理已取消':retrying?'正在重试处理':current===3?'正在整合知识':current===4?'正在更新索引':'正在处理资料',
  error:failed?(transfer?.error||record?.error||job?.error||'处理未完成，请检查资料或模型配置'):'',busy:!!busy,complete:!!complete,retryable:!!failed&&!!record?.authorized};
}

export function needsNotification(row:UploadEntry){
 if(!row.authorized)return false;
 return row.status==='saved'||row.status==='parsing'||row.status==='failed'||!!row.job&&!(row.job.status==='completed'&&row.job.index_status==='ready')&&row.job.status!=='cancelled';
}


export function overallProgress(rows:(UploadEntry|TransferEntry)[]){
 const views=rows.map(progressView);
 const complete=views.length>0&&views.every(view=>view.complete);
 const failed=views.some(view=>view.error||view.steps.some(step=>step.state==='failed'));
 const busy=views.some(view=>view.busy);
 const average=views.length?Math.round(views.reduce((total,view)=>total+view.percent,0)/views.length):0;
 return {percent:complete?100:Math.min(99,average),complete,failed,busy,count:views.length};
}
