export async function api<T>(url:string,options:RequestInit={}):Promise<T>{
 const headers=new Headers(options.headers);
 if(options.method && options.method!=='GET')headers.set('X-Workbench-Request','1');
 const response=await fetch('/api'+url,{...options,headers,credentials:'include'});
 const data=await response.json().catch(()=>({detail:'服务返回了无法读取的响应'}));
 if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:`请求未完成（${response.status}）`);
 return data as T;
}
export function jsonPost<T>(url:string,body:unknown){return api<T>(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})}
