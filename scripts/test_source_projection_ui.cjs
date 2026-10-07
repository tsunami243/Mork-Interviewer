const path=require('path');const {chromium}=require(path.join(process.env.USERPROFILE,'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright'));
(async()=>{const browser=await chromium.launch({headless:true,executablePath:path.join(process.env.LOCALAPPDATA,'ms-playwright/chromium_headless_shell-1243/chrome-headless-shell-win64/chrome-headless-shell.exe')});try{
 const page=await browser.newPage({viewport:{width:1600,height:1050}});await page.goto('http://127.0.0.1:8501');await page.getByRole('button',{name:'知识文件',exact:true}).click();
 const graph=await(await page.request.get('http://127.0.0.1:8501/api/library/graph')).json();const pairs=Object.entries(graph.source_aliases||{});if(!pairs.length)throw Error('Expected one folded source pair');
 const [raw,summary]=pairs[0];const library=await(await page.request.get('http://127.0.0.1:8501/api/library')).json();
 if(library.files.some(f=>f.path===raw)||graph.nodes.some(n=>n.path===raw))throw Error('Duplicate raw source still visible');
 const file=library.files.find(f=>f.path===summary);await page.getByRole('button',{name:'打开'+file.title,exact:true}).click();await page.getByRole('button',{name:'查看原文快照',exact:true}).click();await page.getByRole('button',{name:'返回来源摘要',exact:true}).waitFor();await page.getByRole('button',{name:'返回来源摘要',exact:true}).click();await page.locator('.document-card h3').filter({hasText:file.title}).waitFor();
 if(graph.links.some(e=>e.source===raw||e.target===raw||e.source===e.target))throw Error('Unprojected reference or self-link remains');console.log('SINGLE_SOURCE_GRAPH_AND_ORIGINAL_NAVIGATION: PASS');
 }finally{await browser.close();}})().catch(e=>{console.error(e.stack);process.exitCode=1});
