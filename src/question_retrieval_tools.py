"""A-RAG 的题库检索工具：线索搜索、语义搜索、按 ID 阅读。"""

from math import isfinite
from typing import Any

from question_bank import QuestionReference
from wiki_tools import WikiTools


class QuestionRetrievalTools:
    def __init__(self, bank: list[QuestionReference], stage: str, tags: list[str], excluded: set[str], index: dict | None, wiki: WikiTools | None = None):
        self.bank = bank
        self.stage = stage
        self.tags = set(tags)
        self.excluded = excluded
        self.index = index
        self.wiki = wiki if wiki is not None else WikiTools(tags, namespaces=["personal"])
        self.by_id = {item.id: item for item in bank}
        self.allowed = [item for item in bank if item.stage == stage and item.id not in excluded]
        self.seen_ids: list[str] = []

    def _render(self, items: list[QuestionReference], status: str = "ok") -> dict[str, Any]:
        for item in items:
            if item.id not in self.seen_ids:
                self.seen_ids.append(item.id)
        snippets = [f"{item.id} [{item.stage}] {item.preview}" for item in items]
        return {
            "tool_result": "\n".join(snippets) if snippets else "没有匹配的参考题",
            "tool_log": {"status": status, "chunks_found": len(items), "reference_ids": [item.id for item in items]},
            "references": [item.id for item in items],
        }

    def keyword_search(self, keywords: list[str], top_k: int = 3) -> dict[str, Any]:
        if type(top_k) is not int or not 1 <= top_k <= 5:
            raise ValueError("top_k 必须在 1 到 5 之间")
        if not isinstance(keywords, list):
            raise ValueError("keywords 必须是关键词列表")
        cleaned = [item.casefold().strip() for item in keywords if isinstance(item, str) and item.strip()][:5]
        if not cleaned:
            return self._render([])
        matches = []
        for item in self.allowed:
            haystack = " ".join((*item.tags, item.intent, item.preview, item.question)).casefold()
            score = sum(haystack.count(keyword) for keyword in cleaned)
            if score:
                matches.append((score, item))
        matches.sort(key=lambda pair: (-pair[0], pair[1].id))
        return self._render([item for _, item in matches[:top_k]])

    def semantic_search(self, top_k: int = 3) -> dict[str, Any]:
        """仅使用题库预计算的受控技术标签向量，绝不上传简历原文。"""
        if type(top_k) is not int or not 1 <= top_k <= 5:
            raise ValueError("top_k 必须在 1 到 5 之间")
        if self.index is None:
            return self._render([], "embedding_unavailable")
        query_vectors = [self.index["query_vectors"].get(f"{self.stage}:{tag}") for tag in self.tags]
        query_vectors = [vector for vector in query_vectors if vector is not None]
        if not query_vectors:
            return self._render([], "embedding_unavailable")
        matches = []
        for item in self.allowed:
            vector = self.index["question_vectors"].get(item.id)
            if vector is None:
                continue
            score = max(sum(a * b for a, b in zip(query, vector)) for query in query_vectors)
            score += 0.1 * len(set(item.tags) & self.tags)
            if isfinite(score):
                matches.append((score, item))
        matches.sort(key=lambda pair: (-pair[0], pair[1].id))
        return self._render([item for _, item in matches[:top_k]])

    def chunk_read(self, reference_id: str) -> dict[str, Any]:
        item = self.by_id.get(reference_id)
        if item is None or item not in self.allowed:
            return self._render([], "not_found")
        # 仅展开同阶段、同主题的相邻参考题，避免读取无关题目。
        position = self.allowed.index(item)
        neighboring = next(
            (other for other in sorted(self.allowed, key=lambda other: abs(self.allowed.index(other) - position))
             if other.id != item.id and set(other.tags) & set(item.tags)),
            None,
        )
        items = [item] + ([neighboring] if neighboring else [])
        for entry in items:
            if entry.id not in self.seen_ids:
                self.seen_ids.append(entry.id)
        return {
            "tool_result": "\n".join(f"{entry.id}：{entry.question}（意图：{entry.intent}）" for entry in items),
            "tool_log": {"status": "ok", "chunks_found": len(items), "reference_ids": [entry.id for entry in items]},
            "references": [entry.id for entry in items],
        }

    def wiki_search(self) -> dict[str, Any]:
        return self.wiki.search()

    def wiki_read(self, page_id: str) -> dict[str, Any]:
        return self.wiki.read(page_id)
