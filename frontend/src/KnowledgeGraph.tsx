import {useEffect,useRef,useState} from 'react';
import ForceGraph2D from 'react-force-graph-2d';
import type {ForceGraphMethods,NodeObject,LinkObject} from 'react-force-graph-2d';
import {ArrowUpRight,RotateCcw,ZoomIn,ZoomOut} from 'lucide-react';
import {api} from './api';

interface Node {id:string;path:string;title:string;group:string;type:string;kind:string}
interface Edge {source:string|NodeObject<Node>;target:string|NodeObject<Node>;types:string[]}
interface Data {source_aliases?:Record<string,string>;nodes:NodeObject<Node>[];links:LinkObject<Node,Edge>[]}
const colors:Record<string,string>={'实体':'#5f9fea','概念':'#b68aec','资料':'#f0a057','概览':'#d4b558','参考题':'#6eb69c','综合':'#8597b1'};
const endpoint=(value:Edge['source'])=>typeof value==='object'?value.id:value;

export function KnowledgeGraph({active,onOpen}:{active:boolean;onOpen:(path:string)=>void}){
 const pointerInside=useRef(false);
 const host=useRef<HTMLDivElement>(null),graph=useRef<ForceGraphMethods<Node,Edge>|undefined>(undefined);
 const [data,setData]=useState<Data>({nodes:[],links:[]});
 const [selection,setSelection]=useState(''),[hoverFocus,setHoverFocus]=useState<string|null>(null);
 const focus=selection==='__all__'?null:selection||hoverFocus;
 const pinned=selection!=='';
 const [size,setSize]=useState({width:600,height:500}),[error,setError]=useState('');
 const signature=useRef(''),positions=useRef<Data>({nodes:[],links:[]}),fit=useRef(false);
 useEffect(()=>{if(!active)return;let live=true;
  const load=async()=>{try{const result=await api<Data>('/library/graph');if(!live)return;
   const next=JSON.stringify(result);if(signature.current===next)return;signature.current=next;
   const old=new Map(positions.current.nodes.map(n=>[n.id,n]));
   const nodes=result.nodes.map(n=>({...n,...(old.has(n.id)?{x:old.get(n.id)?.x,y:old.get(n.id)?.y,fx:old.get(n.id)?.fx,fy:old.get(n.id)?.fy}:{})}));
   const updated={nodes,links:result.links.map(l=>({...l}))};positions.current=updated;setData(updated);setError('');
   setSelection(old=>result.source_aliases?.[old]||(old&&old!=='__all__'&&!nodes.some(node=>node.id===old)?'':old));
  }catch(e){if(live)setError((e as Error).message)}};
  void load();const timer=setInterval(()=>void load(),3500);return()=>{live=false;clearInterval(timer)};
 },[active]);
 useEffect(()=>{if(!active||!host.current)return;const observer=new ResizeObserver(entries=>{const r=entries[0].contentRect;if(r.width>0&&r.height>0)setSize({width:r.width,height:r.height})});observer.observe(host.current);return()=>observer.disconnect()},[active]);
 useEffect(()=>{if(!active){pointerInside.current=false;graph.current?.pauseAnimation();setHoverFocus(null)}else graph.current?.resumeAnimation()},[active]);
 const adjacent=new Set<string>();if(focus){adjacent.add(focus);data.links.forEach(edge=>{const a=endpoint(edge.source),b=endpoint(edge.target);if(a===focus&&b)adjacent.add(b);if(b===focus&&a)adjacent.add(a)})}
 const visibleLinks=data.links.filter(l=>!focus||endpoint(l.source)===focus||endpoint(l.target)===focus);
 const focused=data.nodes.find(n=>n.id===focus);
 function paint(node:NodeObject<Node>,ctx:CanvasRenderingContext2D,scale:number){
  const x=node.x||0,y=node.y||0,r=node.id===focus?7:5;
  ctx.beginPath();ctx.arc(x,y,r,0,Math.PI*2);ctx.fillStyle=colors[node.group]||'#8b9cb2';ctx.fill();
  ctx.font=`${12/scale}px -apple-system, "Microsoft YaHei", sans-serif`;
  ctx.fillStyle='#4d6078';ctx.textAlign='left';ctx.textBaseline='middle';
  const label=node.title.length>24?node.title.slice(0,24)+'…':node.title;
  ctx.fillText(label,x+r+5/scale,y);
 }
 return <section className="graph-workspace" hidden={!active} aria-label="知识图谱" data-focus={focus||''} data-display-mode={selection==='__all__'?'all':pinned?'pinned':'auto'} data-visible-nodes={focus?adjacent.size:data.nodes.length} data-visible-links={visibleLinks.length}>
  <div className="graph-toolbar"><div><h3>Knowledge Graph</h3><span>{focus?adjacent.size:data.nodes.length} / {data.nodes.length} 文件 · {visibleLinks.length} 关联</span></div>
   <select aria-label="定位知识节点" value={selection} onChange={e=>{const id=e.target.value;setSelection(id);setHoverFocus(null);const node=data.nodes.find(n=>n.id===id);if(node)graph.current?.centerAt(node.x,node.y,250)}}><option value="">自动模式 · 悬停看关联</option><option value="__all__">查看全部节点（固定）</option>{data.nodes.map(n=><option key={n.id} value={n.id}>{n.title}</option>)}</select>
   <button className="quiet-button" onClick={()=>{setSelection('');setHoverFocus(null);graph.current?.zoomToFit(350,55)}}><RotateCcw size={15}/>重置</button>
  </div>
  {error&&<div className="error-banner" role="alert">{error}</div>}
  <div className="graph-canvas" ref={host} onPointerEnter={()=>{pointerInside.current=true}} onPointerMove={event=>{pointerInside.current=!(event.target as Element).closest('.graph-zoom')}} onPointerLeave={()=>{pointerInside.current=false;setHoverFocus(null)}}>
   {data.nodes.length>0?<ForceGraph2D<Node,Edge> ref={graph} graphData={data} width={size.width} height={size.height} backgroundColor="#f8faff" nodeLabel={()=>''}
    nodeCanvasObject={paint} nodePointerAreaPaint={(node,color,ctx)=>{ctx.beginPath();ctx.arc(node.x||0,node.y||0,9,0,Math.PI*2);ctx.fillStyle=color;ctx.fill()}}
    nodeVisibility={node=>!focus||adjacent.has(node.id)} linkVisibility={l=>!focus||endpoint(l.source)===focus||endpoint(l.target)===focus}
    linkColor={()=>focus?'#829bbb':'#c1cfdf'} linkWidth={focus?1.4:.8} nodeRelSize={5} minZoom={.2} maxZoom={8} cooldownTicks={120}
    onNodeHover={node=>{if(!pinned&&pointerInside.current)setHoverFocus(node?.id||null)}} onNodeClick={node=>onOpen(node.path)}
    onEngineStop={()=>{if(!fit.current&&active){fit.current=true;graph.current?.zoomToFit(350,65)}}}/>:<div className="file-empty"><h3>暂无可展示的知识文件</h3><p>已有文件的真实引用将形成图谱连线。</p></div>}
   <div className="graph-zoom" onMouseEnter={()=>setHoverFocus(null)}><button className="icon-button" aria-label="放大图谱" onClick={()=>graph.current?.zoom((graph.current.zoom()||1)*1.3,200)}><ZoomIn size={18}/></button><button className="icon-button" aria-label="缩小图谱" onClick={()=>graph.current?.zoom((graph.current.zoom()||1)/1.3,200)}><ZoomOut size={18}/></button></div>
   <div className="graph-legend">{Object.entries(colors).map(([label,color])=><span key={label}><i style={{background:color}}/>{label}</span>)}</div>
  </div>
  <div className="graph-caption"><span>{focused?`${pinned?'固定展示':'临时查看'}「${focused.title}」及其直接关联文件`:selection==='__all__'?'固定展示全部节点；切换选择或重置以更改显示方式。':'悬停只看直接关联；下拉选择可固定显示，重置恢复自动模式。'}</span>{focused&&<button className="quiet-button" onClick={()=>onOpen(focused.path)}>查看聚焦文件<ArrowUpRight size={15}/></button>}</div>
 </section>
}
