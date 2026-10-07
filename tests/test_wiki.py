"""Offline validation of explicit Wiki approval, source links, vector rebuild, and A-RAG tools."""

import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("LLM_API_KEY", "offline-test-key")
os.environ.setdefault("LLM_MODEL", "offline-test-model")

import wiki_index
import wiki_store
from document_loader import load_uploaded_document
from question_bank import load_question_bank
from question_retrieval_agent import run_retrieval_agent
from question_retrieval_tools import QuestionRetrievalTools
from wiki_tools import WikiTools


class FakeEmbeddings:
    def __init__(self):
        self.requests = []

    def create(self, *, model, input, dimensions):
        self.requests.extend(input)
        return SimpleNamespace(data=[SimpleNamespace(index=i, embedding=[1.0] + [0.0] * (dimensions-1)) for i in range(len(input))])


class WikiTests(unittest.TestCase):
    def test_approval_required_and_obsidian_links(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            document = load_uploaded_document("lesson.md", b"Redis is a cache. TTL expires keys.")
            self.assertEqual(wiki_store.list_pages(root), [])
            first = wiki_store.approve_draft(document, {"title": "Cache", "body": "Cache has tradeoffs.", "tags": ["Redis"], "links": []}, root)
            self.assertEqual(first.sources[0].startswith("sources/user-"), True)
            self.assertTrue((root / first.sources[0]).exists())
            self.assertIn('status: "approved"', first.path.read_text(encoding="utf-8"))
            second = wiki_store.approve_draft(document, {"title": "Cache follow-up", "body": "Consider TTL.", "tags": ["Redis"], "links": [first.id]}, root)
            self.assertIn(f"[[{first.id}]]", second.path.read_text(encoding="utf-8"))
            self.assertEqual(second.links, (first.id,))
            with self.assertRaises(ValueError):
                wiki_store.approve_draft(document, {"title": "Invalid", "body": "bad", "links": ["../../private"]}, root)

    def test_generation_creates_only_a_reviewable_draft(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            document = load_uploaded_document("public.md", b"Redis cache document.")
            message = {"title": "Redis", "body": "A cache has tradeoffs.", "tags": ["Redis"], "links": ["not-approved"]}
            llm = SimpleNamespace(invoke=lambda _: SimpleNamespace(content=json.dumps(message)))
            with patch.object(wiki_store, "WIKI_ROOT", root):
                draft = wiki_store.make_draft(document, llm)
            self.assertEqual(draft["links"], [])
            self.assertEqual(wiki_store.list_pages(root), [])
            self.assertFalse((root / "sources").exists())

    def test_unapproved_page_is_never_indexed_and_obsidian_yaml_is_readable(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            document = load_uploaded_document("public.md", b"Redis cache.")
            page = wiki_store.approve_draft(document, {"title": "Cache", "body": "Cache tradeoffs.", "tags": ["Redis"], "links": []}, root)
            unquoted = page.path.read_text(encoding="utf-8").replace('status: "approved"', 'status: approved')
            page.path.write_text(unquoted, encoding="utf-8")
            self.assertEqual(len(wiki_store.list_pages(root)), 1)
            draft_path = root / "concepts" / "draft-example.md"
            draft_path.write_text('---\nid: draft-example\nstatus: draft\n---\n# Work in progress', encoding="utf-8")
            self.assertEqual([item.id for item in wiki_store.list_pages(root)], [page.id])
            self.assertIn(f"[[{page.id}|Cache]]", (root / "index.md").read_text(encoding="utf-8"))

    def test_rebuildable_lancedb_index_and_stale_manifest(self):
        with TemporaryDirectory() as directory:
            root, index_dir = Path(directory) / "vault", Path(directory) / "vectors"
            document = load_uploaded_document("public.md", "技术资料：Redis 与缓存。".encode())
            page = wiki_store.approve_draft(document, {"title": "Redis 缓存", "body": "Redis 是缓存工具。", "tags": ["Redis"], "links": []}, root)
            fake = FakeEmbeddings()
            with patch.object(wiki_index, "WIKI_ROOT", root):
                self.assertIsNone(wiki_index.load_wiki_index(directory=index_dir))
                wiki_index.build_wiki_index(SimpleNamespace(embeddings=fake), directory=index_dir)
                loaded = wiki_index.load_wiki_index(directory=index_dir)
                self.assertIn(page.id, loaded.search(["Redis"]))
                self.assertTrue(all("private" not in request for request in fake.requests))
                page.path.write_text(page.path.read_text(encoding="utf-8") + "新内容", encoding="utf-8")
                self.assertIsNone(wiki_index.load_wiki_index(directory=index_dir))
                wiki_index.build_wiki_index(SimpleNamespace(embeddings=fake), directory=index_dir)
                self.assertIsNotNone(wiki_index.load_wiki_index(directory=index_dir))

    def test_wiki_search_and_read_are_bounded_to_approved_ids(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            document = load_uploaded_document("source.md", b"Redis introduction")
            page = wiki_store.approve_draft(document, {"title": "Redis", "body": "Redis helps caches.", "tags": ["Redis"], "links": []}, root)
            with patch.object(wiki_store, "WIKI_ROOT", root), patch("wiki_tools.load_wiki_index", return_value=None):
                wiki = WikiTools(["Redis"])
                self.assertEqual(wiki.read(page.id)["tool_log"]["status"], "not_found")
                self.assertIn(page.id, wiki.search()["references"])
                self.assertIn("sources/user-", wiki.read(page.id)["tool_result"])
                self.assertEqual(wiki.read("../../secrets")["tool_log"]["status"], "not_found")

    def test_a_rag_can_search_and_read_wiki_without_user_embedding(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            page = wiki_store.approve_draft(load_uploaded_document("source.md", b"Redis introduction"),
                {"title": "Redis", "body": "Discuss tradeoffs.", "tags": ["Redis"], "links": []}, root)
            bank = load_question_bank()
            with patch.object(wiki_store, "WIKI_ROOT", root), patch("wiki_tools.load_wiki_index", return_value=None):
                tools = QuestionRetrievalTools(bank, "design", ["Redis"], set(), None, wiki=WikiTools(["Redis"]))
                responses = iter([
                    {"action": "wiki_search"}, {"action": "wiki_read", "reference_id": page.id},
                    {"action": "finish", "question": "你为什么采用缓存？", "reason": "关联技术取舍"},
                ])
                llm = SimpleNamespace(invoke=lambda _: SimpleNamespace(content=json.dumps(next(responses), ensure_ascii=False)))
                result = run_retrieval_agent(tools, llm, {"technology": ["Redis"]})
                self.assertIn(page.id, result["wiki_reference_ids"])
                self.assertEqual(result["tool_calls"], 3)


if __name__ == "__main__":
    unittest.main()
