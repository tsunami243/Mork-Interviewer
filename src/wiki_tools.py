"""Safe Wiki retrieval for A-RAG with explicit namespace and hybrid recall."""
import wiki_store
from knowledge_metadata import page_statuses, record_retrieval
from wiki_index import DEFAULT_NAMESPACE, load_wiki_index, validate_namespace
from wiki_store import list_pages


class WikiTools:
    def __init__(self, tags: list[str], namespaces: list[str] | None = None):
        if not isinstance(namespaces, list) or not namespaces:
            raise ValueError("Wiki 检索必须显式传入 namespace 白名单")
        self.tags = tags
        self.namespaces = [validate_namespace(item) for item in namespaces]
        if len(set(self.namespaces)) != len(self.namespaces):
            raise ValueError("namespace 白名单不能重复")
        try:
            self.pages = {page.id: page for page in list_pages()}
        except (OSError, ValueError):
            self.pages = {}
        statuses = page_statuses(root=wiki_store.WIKI_ROOT) if DEFAULT_NAMESPACE in self.namespaces else None
        if statuses is not None:
            self.pages = {page_id: page for page_id, page in self.pages.items() if statuses.get(page_id, "approved") == "approved"}
        self.seen: set[str] = set()
        self.indexes = {}
        if self.pages and DEFAULT_NAMESPACE in self.namespaces:
            try:
                index = load_wiki_index(namespace=DEFAULT_NAMESPACE)
                if index:
                    self.indexes[DEFAULT_NAMESPACE] = index
            except (OSError, ValueError, RuntimeError):
                pass

    def search(self, top_k: int = 3, keywords: list[str] | None = None) -> dict:
        if type(top_k) is not int or not 1 <= top_k <= 10:
            raise ValueError("top_k 必须在1至10之间")
        terms = [str(item).casefold().strip() for item in (keywords or self.tags) if str(item).strip() and item != "通用"][:8]
        scored = []
        for page in self.pages.values():
            haystack = " ".join((page.title, *page.aliases, *page.tags, page.body)).casefold()
            score = sum((5 if term in page.title.casefold() else 0) + (3 * haystack.count(term)) for term in terms)
            if score:
                scored.append((score, page.id))
        found = [page_id for _, page_id in sorted(scored, key=lambda item: (-item[0], item[1]))[:top_k * 2]]
        for index in self.indexes.values():
            try:
                found.extend(index.search(self.tags, top_k=top_k, keywords=terms))
            except (OSError, ValueError, RuntimeError):
                pass
        found = list(dict.fromkeys(found))[:top_k]
        self.seen.update(found)
        try:
            record_retrieval(" ".join(terms), self.namespaces, found, found, "hybrid")
        except (OSError, RuntimeError, ValueError, TypeError):
            pass
        previews = [f"wiki:{page_id} {self.pages[page_id].title} [{', '.join(self.pages[page_id].tags)}]" for page_id in found if page_id in self.pages]
        return {
            "tool_result": "\n".join(previews) if previews else "没有匹配的 Wiki 知识",
            "references": found,
            "tool_log": {"status": "ok" if found else "not_found", "reference_ids": found,
                         "chunks_found": len(found), "namespaces": self.namespaces, "strategy": "hybrid"},
        }

    def read(self, page_id: str) -> dict:
        if page_id not in self.seen or page_id not in self.pages:
            return {"tool_result": "请先检索已发布的 Wiki 页面", "references": [], "tool_log": {"status": "not_found", "chunks_found": 0}}
        page = self.pages[page_id]
        warning = "系统自动编译，未经过人工审核；应核对来源，不可当作候选人项目事实。\n" if page.review_status == "automatic" else ""
        if page.conflicts:
            warning += "存在资料差异：" + "；".join(item[:150] for item in page.conflicts[:3]) + "\n"
        body = f"{warning}{page.title}\n{page.body[:2100]}\n来源：{', '.join(page.sources)}\n相关知识：{', '.join(page.links)}"
        return {"tool_result": body, "references": [page_id], "tool_log": {"status": "ok", "chunks_found": 1, "reference_ids": [page_id]}}
