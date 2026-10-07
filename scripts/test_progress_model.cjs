// Pure progress model checks. Node 24+ executes erasable TypeScript without extra dependencies.
const assert=require('node:assert/strict');const {pathToFileURL}=require('node:url');const path=require('node:path');
(async()=>{
 const {progressView,needsNotification,overallProgress}=await import(pathToFileURL(path.resolve('frontend/src/progress.ts')).href);
 const base={id:'test',name:'public.md',authorized:true,status:'processing',error:'',job:{id:'job',stage:'core',status:'running',phase:'compile',attempts:1,index_status:null,pages:null,error:'',progress:{stage:'generation',completed:1,total:4}}};
 let view=progressView(base);assert.equal(view.percent,45);assert.deepEqual(view.steps.map(s=>s.state),['completed','completed','running','pending','pending']);assert.equal(view.complete,false);
 view=progressView({...base,job:{...base.job,status:'completed',index_status:'ready',progress:{stage:'index',completed:1,total:1}}});assert.equal(view.percent,100);assert.ok(view.steps.every(s=>s.state==='completed'));assert.equal(needsNotification({...base,job:{...base.job,status:'completed',index_status:'ready'}}),false);
 view=progressView({...base,error:'provider failed',job:{...base.job,status:'failed',attempts:3}});assert.equal(view.steps[2].state,'failed');assert.equal(view.error,'provider failed');assert.ok(view.percent<100);
 view=progressView({...base,job:{...base.job,status:'completed',index_status:'failed',index_attempts:3,progress:{stage:'index',completed:0,total:1}}});assert.equal(view.percent,80);assert.equal(view.steps[4].state,'failed');assert.equal(view.complete,false);
 view=progressView({...base,status:'failed',error:'parse failed',job:null});assert.equal(view.steps[0].state,'failed');assert.equal(view.percent,0);assert.ok(view.steps.slice(1).every(s=>s.state==='pending'));
 view=progressView({id:'transfer',name:'file.md',status:'uploading',error:''});assert.equal(view.steps[0].state,'running');assert.equal(view.percent,0);
 view=progressView({...base,job:{...base.job,status:'queued',phase:'analysis',progress:{}}});assert.equal(view.steps[0].state,'completed');assert.ok(view.steps.slice(1).every(s=>s.state==='pending'));
 const ready={...base,job:{...base.job,status:'completed',index_status:'ready',progress:{stage:'index',completed:1,total:1}}};
 let aggregate=overallProgress([base,ready]);assert.equal(aggregate.percent,73);assert.equal(aggregate.complete,false);assert.equal(aggregate.busy,true);
 aggregate=overallProgress([ready,ready]);assert.equal(aggregate.percent,100);assert.equal(aggregate.complete,true);
 const almost={...base,job:{...base.job,status:'completed',index_status:'updating',progress:{stage:'index',completed:1,total:1}}};
 aggregate=overallProgress([...Array(100).fill(ready),almost]);assert.equal(aggregate.percent,99);assert.equal(aggregate.complete,false);
 assert.equal(overallProgress([]).percent,0);
 console.log('REAL_PROGRESS_STATES_AND_FAILURES: PASS');
})().catch(error=>{console.error(error.stack);process.exitCode=1});
