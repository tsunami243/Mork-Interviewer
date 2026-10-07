// Read-only UI regression. All /api requests are intercepted with synthetic progress data.
const path=require('path'),fs=require('fs');
const {chromium}=require(path.join(process.env.USERPROFILE,'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
const url=process.env.WORKBENCH_URL||'http://127.0.0.1:8501';
const out=path.resolve(process.argv[2]||'data/private/runtime/progress-popover');fs.mkdirSync(out,{recursive:true});
const row={id:'synthetic-progress',name:'进度演示.md',authorized:true,status:'processing',error:'',job:{id:'synthetic-job',stage:'core',status:'running',phase:'compile',attempts:1,index_status:null,pages:null,error:'',progress:{stage:'generation',completed:1,total:2}}};
async function mock(page){await page.route('**/api/**',async route=>{if(route.request().method()!=='GET')throw Error('Read-only UI regression must never start a real task');const pathname=new URL(route.request().url()).pathname;const uploads=[row];let data=pathname.endsWith('/bootstrap')?{files:[],groups:[],sessions:[],uploads,configured:true}:pathname.endsWith('/uploads')?{uploads}:pathname.endsWith('/interviews')?{sessions:[]}:pathname.endsWith('/activity')?{pending:[],actions:[]}:{files:[]};await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(data)})})}
(async()=>{const browser=await chromium.launch({headless:true,executablePath:path.join(process.env.LOCALAPPDATA,'ms-playwright/chromium_headless_shell-1243/chrome-headless-shell-win64/chrome-headless-shell.exe')});try{
 const page=await browser.newPage({viewport:{width:1600,height:1050}});await mock(page);const errors=[];page.on('pageerror',e=>errors.push(e.message));await page.goto(url);
 const trigger=page.getByRole('button',{name:/文件处理总进度/}),card=page.getByRole('region',{name:row.name+'处理进度',exact:true});await trigger.waitFor();
 if(await trigger.getAttribute('aria-expanded')!=='false'||await card.count())throw Error('Details must default to collapsed');
 const buttonBox=await page.getByRole('button',{name:'拓展现有知识库',exact:true}).boundingBox(),ringBox=await trigger.boundingBox();
 if(ringBox.y<buttonBox.y+buttonBox.height||Math.abs(ringBox.x+ringBox.width-buttonBox.x-buttonBox.width)>2)throw Error('Progress not aligned beneath expand button');
 await page.screenshot({path:path.join(out,'collapsed.png'),fullPage:true});await trigger.hover();await card.waitFor();
 const box=await card.boundingBox();await page.mouse.move(box.x+25,box.y+35,{steps:12});await page.waitForTimeout(200);if(await trigger.getAttribute('aria-expanded')!=='true')throw Error('Popover collapsed while moving into details');
 if(await card.locator('.notification-step.completed').count()!==2||await card.locator('.notification-step.running').count()!==1||await card.locator('.notification-step.pending').count()!==2)throw Error('Progress step states changed');
 await page.screenshot({path:path.join(out,'expanded.png'),fullPage:true});await page.mouse.move(10,10);await page.waitForTimeout(220);if(await card.count())throw Error('Mouse leave did not collapse details');
 await trigger.focus();await card.waitFor();await page.keyboard.press('Escape');await page.waitForTimeout(50);if(await card.count()||!await trigger.evaluate(element=>element===document.activeElement))throw Error('Escape/focus return failed');
 await page.keyboard.press('Enter');await card.waitFor();await card.getByRole('button',{name:'收起处理详情',exact:true}).click();await page.waitForTimeout(200);if(await card.count()||!await trigger.isVisible())throw Error('Collapse hid the overall indicator');
 row.job={...row.job,status:'completed',index_status:'ready',progress:{stage:'index',completed:1,total:1}};
 await page.waitForFunction(()=>document.querySelector('.progress-summary strong')?.textContent==='100%');
 await trigger.hover();await card.waitFor();if(await card.locator('.notification-step.completed').count()!==5)throw Error('Completion not all green');
 const mobile=await browser.newContext({viewport:{width:390,height:844},hasTouch:true,isMobile:true});const touch=await mobile.newPage();await mock(touch);row.job={...row.job,status:'running',index_status:null,progress:{stage:'generation',completed:1,total:2}};await touch.goto(url);
 const touchTrigger=touch.getByRole('button',{name:/文件处理总进度/}),touchCard=touch.getByRole('region',{name:row.name+'处理进度',exact:true});await touchTrigger.waitFor();await touchTrigger.tap();await touchCard.waitFor();await touch.waitForTimeout(180);if(await touchTrigger.getAttribute('aria-expanded')!=='true')throw Error('Touch tap did not remain open');
 if(await touch.evaluate(()=>document.documentElement.scrollWidth>innerWidth))throw Error('Mobile overflow');await touch.screenshot({path:path.join(out,'mobile.png'),fullPage:true});await touchTrigger.tap();await touch.waitForTimeout(200);if(await touchCard.count())throw Error('Second tap did not collapse');await mobile.close();
 if(errors.length)throw Error(errors.join(';'));console.log('PROGRESS_RING_HOVER_KEYBOARD_TOUCH: PASS');
 }finally{await browser.close();}})().catch(error=>{console.error(error.stack);process.exitCode=1});
