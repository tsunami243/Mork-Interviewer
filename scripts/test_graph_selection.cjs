// Read-only browser regression: manual graph selection must survive hover, polling and navigation.
// Override PLAYWRIGHT_MODULE / PLAYWRIGHT_CHROMIUM_EXECUTABLE for another local runtime.
const path=require('path');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||path.join(process.env.USERPROFILE,'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
const url=process.env.WORKBENCH_URL||'http://127.0.0.1:8501';
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE||path.join(process.env.LOCALAPPDATA,'ms-playwright/chromium_headless_shell-1243/chrome-headless-shell-win64/chrome-headless-shell.exe')});
 try{
  const page=await browser.newPage({viewport:{width:1600,height:1050}});const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto(url);await page.getByRole('button',{name:'知识图谱场景',exact:true}).click();
  const select=page.getByLabel('定位知识节点',{exact:true}),workspace=page.locator('.graph-workspace');await select.waitFor();await page.waitForTimeout(2800);
  const data=await(await page.request.get(url+'/api/library/graph')).json();
  const neighbors=id=>new Set([id,...data.links.filter(l=>l.source===id||l.target===id).map(l=>l.source===id?l.target:l.source)]);
  const node=data.nodes.find(n=>neighbors(n.id).size>1&&neighbors(n.id).size<data.nodes.length);
  if(!node)throw Error('Need a connected node with at least one unrelated node for this regression');
  await select.selectOption(node.id);await page.waitForTimeout(600);
  const canvas=await page.locator('.graph-canvas canvas').first().boundingBox();
  async function fixed(){
   if(await workspace.getAttribute('data-display-mode')!=='pinned'||await workspace.getAttribute('data-focus')!==node.id||await select.inputValue()!==node.id||Number(await workspace.getAttribute('data-visible-nodes'))!==neighbors(node.id).size)throw Error('Pinned selection changed');
  }
  await fixed();
  await page.mouse.move(canvas.x+canvas.width/2,canvas.y+canvas.height/2);await page.waitForTimeout(150);await fixed();
  await page.mouse.move(canvas.x+80,canvas.y+25);await page.waitForTimeout(150);await fixed();
  await page.getByRole('button',{name:'放大图谱',exact:true}).hover();await page.waitForTimeout(150);await fixed();
  await page.mouse.move(10,10);await page.waitForTimeout(3700);await fixed();
  await page.getByRole('button',{name:'查看聚焦文件',exact:true}).click();await page.locator('.document-card').waitFor();
  await page.getByRole('button',{name:'返回知识图谱',exact:true}).click();await page.waitForTimeout(250);await fixed();
  await select.selectOption('__all__');await page.mouse.move(canvas.x+canvas.width/2,canvas.y+canvas.height/2);await page.waitForTimeout(200);
  if(await workspace.getAttribute('data-display-mode')!=='all'||Number(await workspace.getAttribute('data-visible-nodes'))!==data.nodes.length)throw Error('Fixed full graph filtered on hover');
  await select.selectOption(node.id);await page.waitForTimeout(600);await select.selectOption('');
  await page.mouse.move(canvas.x+80,canvas.y+25);await page.waitForTimeout(100);await page.mouse.move(canvas.x+canvas.width/2,canvas.y+canvas.height/2);await page.waitForTimeout(200);
  // Force-layout coordinates can settle differently after a vault grows; test rendered hit areas,
  // rather than assuming the originally centered node must still occupy the exact center pixel.
  let hovered=await workspace.getAttribute('data-focus');
  const latest=await page.locator('.graph-canvas canvas').first().boundingBox();
  if(!hovered){
   for(let dy=-120;dy<=120&&!hovered;dy+=20){for(let dx=-120;dx<=120&&!hovered;dx+=20){
    await page.mouse.move(latest.x+latest.width/2+dx,latest.y+latest.height/2+dy);await page.waitForTimeout(8);hovered=await workspace.getAttribute('data-focus');
   }}
  }
  if(!data.nodes.some(n=>n.id===hovered)||await select.inputValue()!=='')throw Error('Automatic hover does not work independently from dropdown');
  await page.mouse.move(10,10);await page.waitForTimeout(150);
  if(Number(await workspace.getAttribute('data-visible-nodes'))!==data.nodes.length)throw Error('Automatic hover did not restore all nodes');
  await select.selectOption(node.id);await page.getByRole('button',{name:'重置',exact:true}).click();await page.mouse.move(10,10);await page.waitForTimeout(400);
  if(await select.inputValue()!==''||await workspace.getAttribute('data-display-mode')!=='auto')throw Error('Reset did not clear pin');
  if(errors.length)throw Error(errors.join(';'));
  console.log('GRAPH_PINNED_SELECTION: PASS');console.log('PIN_SURVIVES_HOVER_POLLING_NAVIGATION: PASS');console.log('FIXED_ALL_AND_AUTOMATIC_HOVER: PASS');
 }finally{await browser.close();}
})().catch(error=>{console.error(error.stack);process.exitCode=1});
