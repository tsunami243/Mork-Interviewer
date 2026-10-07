// Runs synthetic uploads only after verifying the isolated server uses fake providers.
const path=require('path'),fs=require('fs');
const {chromium}=require(path.join(process.env.USERPROFILE,'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
const url=process.argv[2],out=process.argv[3];
if(!url||!out)throw Error('Pass an isolated fake-provider URL and output directory.');
async function eventually(fn){for(let i=0;i<200;i++){if(await fn())return;await new Promise(r=>setTimeout(r,120));}throw Error('Progress timed out');}
(async()=>{const browser=await chromium.launch({headless:true,executablePath:path.join(process.env.LOCALAPPDATA,'ms-playwright/chromium_headless_shell-1243/chrome-headless-shell-win64/chrome-headless-shell.exe')});try{
 const page=await browser.newPage({viewport:{width:1600,height:1050}});page.setDefaultTimeout(25000);const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const check=await(await page.request.get(url+'/__qa/health')).json();if(check.providers!=='fake')throw Error('Refusing non-fake server');
 await page.goto(url);await page.getByRole('button',{name:'拓展现有知识库',exact:true}).click();let dialog=page.getByRole('dialog');
 if(await dialog.getByRole('button',{name:'开始拓展',exact:true}).count())throw Error('Manual start remains');
 const good='进度演示.md';await dialog.getByLabel('建库资料文件',{exact:true}).setInputFiles({name:good,mimeType:'text/markdown',buffer:Buffer.from('Meituan operates a platform. Redis stores cached data. TTL expires keys.')});
 await eventually(async()=>await page.getByRole('dialog').count()===0);
 const overall=page.getByRole('button',{name:/文件处理总进度/});await overall.waitFor();if(await overall.getAttribute('aria-expanded')!=='false')throw Error('Progress details must default collapsed');await overall.hover();
 let card=page.getByRole('region',{name:good+'处理进度',exact:true});await card.waitFor();
 await eventually(async()=>await card.getByText('1 / 2 张页面已生成',{exact:true}).count()===1);
 if(await card.locator('.notification-step.completed').count()!==2||await card.locator('.notification-step.running').count()!==1||await card.locator('.notification-step.pending').count()!==2)throw Error('Wrong step states');
 if(await card.getByRole('progressbar').getAttribute('aria-valuenow')!=='50')throw Error('Wrong partial progress');
 await page.screenshot({path:path.join(out,'notification-processing.png'),fullPage:true});
 await card.getByRole('button',{name:'收起处理详情',exact:true}).click();await eventually(async()=>await card.count()===0);
 const pending=await(await page.request.get(url+'/api/knowledge/uploads')).json();if(pending.uploads[0].job.status!=='running')throw Error('Hiding cancelled task');
 await page.reload();await overall.hover();card=page.getByRole('region',{name:good+'处理进度',exact:true});await card.waitFor();
 await page.setViewportSize({width:390,height:844});await overall.hover();await page.waitForTimeout(400);if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth))throw Error('Mobile overflow');
 await page.screenshot({path:path.join(out,'notification-mobile.png'),fullPage:true});await page.setViewportSize({width:1600,height:1050});await overall.hover();
 await page.request.post(url+'/__qa/release');await eventually(async()=>await card.getAttribute('data-state')==='completed');
 if(await card.locator('.notification-step.completed').count()!==5||await card.getByRole('progressbar').getAttribute('aria-valuenow')!=='100')throw Error('Ready job not all green');
 await page.screenshot({path:path.join(out,'notification-completed.png'),fullPage:true});await card.getByRole('button',{name:'收起处理详情',exact:true}).click();
 await page.getByRole('button',{name:'拓展现有知识库',exact:true}).click();dialog=page.getByRole('dialog');const bad='解析失败演示.pdf';
 await dialog.getByLabel('建库资料文件',{exact:true}).setInputFiles({name:bad,mimeType:'application/pdf',buffer:Buffer.from('%PDF-1.4\nNot a real PDF file.')});
 await eventually(async()=>await page.getByRole('dialog').count()===0);await overall.hover();
 const failed=page.getByRole('region',{name:bad+'处理进度',exact:true});await eventually(async()=>await failed.count()>0&&await failed.getAttribute('data-state')==='failed');
 if(await failed.locator('.notification-step.failed').count()!==1||await failed.locator('.notification-step.pending').count()!==4)throw Error('Failed parse states wrong');
 await page.screenshot({path:path.join(out,'notification-failed.png'),fullPage:true});
 if(errors.length)throw Error(errors.join(';'));
 fs.writeFileSync(path.join(out,'result.json'),JSON.stringify({passed:true,providers:'fake',upload_auto_starts:true,modal_auto_closes:true,partial_progress:'1 / 2 pages = 50%',pending_red:true,running_yellow:true,completed_green:true,close_does_not_cancel:true,reload_restores_progress:true,parse_failure_visible:true,runtime_errors:errors},null,2));
 console.log('UPLOAD_AUTOSTART_AND_PROGRESS_NOTIFICATIONS: PASS');
 }finally{await browser.close();}})().catch(e=>{console.error(e.stack);process.exitCode=1});
