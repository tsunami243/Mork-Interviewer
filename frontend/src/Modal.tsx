import {useEffect,useRef} from 'react';
import type {ReactNode} from 'react';
import {X} from 'lucide-react';

export function Modal({title,onClose,children}:{title:string;onClose:()=>void;children:ReactNode}){
 const panel=useRef<HTMLDivElement>(null),closeRef=useRef(onClose);closeRef.current=onClose;
 useEffect(()=>{
  const before=document.activeElement as HTMLElement|null;
  panel.current?.focus();
  function key(event:KeyboardEvent){
   if(event.key==='Escape')closeRef.current();
   if(event.key==='Tab'&&panel.current){const items=Array.from(panel.current.querySelectorAll<HTMLElement>('button:not([disabled]),input,select,textarea,[tabindex="0"]'));const first=items[0],last=items.at(-1);if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus()}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus()}}
  }
  document.addEventListener('keydown',key);return()=>{document.removeEventListener('keydown',key);before?.focus()};
 },[]);
 return <div className="modal-backdrop" onMouseDown={event=>{if(event.target===event.currentTarget)onClose()}}><div className="modal-panel" role="dialog" aria-modal="true" aria-label={title} tabIndex={-1} ref={panel}><header className="modal-header"><h2>{title}</h2><button className="icon-button" aria-label="关闭" onClick={onClose}><X size={20}/></button></header>{children}</div></div>;
}
