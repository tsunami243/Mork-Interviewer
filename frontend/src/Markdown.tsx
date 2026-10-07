import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
export function Markdown({body}:{body:string}){
 return <div className="markdown-body"><ReactMarkdown remarkPlugins={[remarkGfm]} components={{
  img:({alt})=><span className="image-placeholder">图片 · {alt||'只读预览不加载外部资源'}</span>,
  a:({href,children})=><a href={href} target="_blank" rel="noreferrer noopener">{children}</a>
 }}>{body}</ReactMarkdown></div>;
}
