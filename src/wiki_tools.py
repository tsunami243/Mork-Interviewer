"""Safe wiki search/read tools for the interview A-RAG agent."""

from wiki_index import load_wiki_index
from wiki_store import list_pages


class WikiTools:
    def __init__(self, tags: list[str]):
        self.tags = tags
        try:
            self.pages = {page.id: page for page in list_pages()}
        except (OSError, ValueError):
            self.pages = {}  # A damaged optional vault cannot disable the existing question bank.
        self.seen: set[str] = set()
        self.index = load_wiki_index() if self.pages else None

    def search(self, top_k: int = 3) -> dict:
        # Model-provided user text is never embedded. Only controlled topic tags are used.
        keywords = [tag.casefold() for tag in self.tags if tag != "通用"]
        scored = []
        for page in self.pages.values():
            title_and_tags = (page.title + " " + " ".join(page.tags)).casefold()
            score = sum(3 * title_and_tags.count(word) + page.body.casefold().count(word) for word in keywords)
            if score:
                scored.append((score, page.id))
        scored.sort(key=lambda item: (-item[0], item[1]))
        found = [id for _, id in scored[:top_k]]
        if self.index:
            try:
                vectors = self.index.search(self.tags, top_k)
                found = list(dict.fromkeys([*found, *vectors]))[:top_k]
            except (OSError, ValueError, RuntimeError):
                pass  # Corrupted/unavailable vectors must not break the interview.
        self.seen.update(found)
        previews = [f"wiki:{id} {self.pages[id].title} [{', '.join(self.pages[id].tags)}]" for id in found]
        return {"tool_result": "\n".join(previews) if previews else "没有匹配的 Wiki 知识",
                "references": found, "tool_log": {"status": "ok" if found else "not_found", "reference_ids": found, "chunks_found": len(found)}}

    def read(self, page_id: str) -> dict:
        if page_id not in self.seen or page_id not in self.pages:
            return {"tool_result": "请先检索已发布的 Wiki 页面", "references": [], "tool_log": {"status": "not_found", "chunks_found": 0}}
        page = self.pages[page_id]
        # Bounded content; source paths are provenance, never file read parameters.
        warning = "系统自动编译，未经过人工审核；应核对来源，不可当作候选人项目事实。\n" if page.review_status == "automatic" else ""
        if page.conflicts:
            warning += "存在资料差异：" + "；".join(item[:150] for item in page.conflicts[:3]) + "\n"
        body = f"{warning}{page.title}\n{page.body[:2100]}\n来源：{', '.join(page.sources)}\n相关知识：{', '.join(page.links)}"
        return {"tool_result": body, "references": [page_id], "tool_log": {"status": "ok", "chunks_found": 1, "reference_ids": [page_id]}}
