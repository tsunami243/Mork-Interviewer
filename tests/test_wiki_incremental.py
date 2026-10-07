"""Offline incremental index updates, deletions, provider changes and failure isolation."""

import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from document_loader import load_uploaded_document
import wiki_index
from wiki_store import approve_draft


class FakeEmbeddings:
    def __init__(self):
        self.requests = []
        self.on_call = None

    def create(self, *, model, input, dimensions):
        self.requests.extend(input)
        if self.on_call:
            self.on_call()
        return SimpleNamespace(data=[SimpleNamespace(index=i, embedding=[1.] + [0.] * (dimensions-1)) for i in range(len(input))])


class IncrementalIndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name) / "vault"
        self.directory = Path(self.tmp.name) / "vectors"
        self.fake = FakeEmbeddings()
        self.client = SimpleNamespace(embeddings=self.fake)
        self.page = approve_draft(load_uploaded_document("public.md", b"Redis cache."),
                                 {"title": "Redis", "body": "Redis cached data.", "tags": ["Redis"], "links": []}, self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def sync(self, **kwargs):
        return wiki_index.sync_wiki_index(self.client, root=self.root, directory=self.directory, **kwargs)

    def test_unchanged_index_makes_no_remote_calls(self):
        first = self.sync()
        count = len(self.fake.requests)
        with patch.object(wiki_index, "create_embedding_client", side_effect=AssertionError("no remote call permitted")):
            second = wiki_index.sync_wiki_index(root=self.root, directory=self.directory)
        self.assertTrue(second.no_op)
        self.assertEqual(second.embedded_chunks, 0)
        self.assertEqual(len(self.fake.requests), count)
        self.assertEqual(first.pages, 1)

    def test_only_changed_page_chunks_are_embedded(self):
        second_page = approve_draft(load_uploaded_document("java.md", b"Java backend."),
                                   {"title": "Java", "body": "Java request lifecycle.", "tags": ["Java"], "links": []}, self.root)
        self.sync()
        self.fake.requests.clear()
        self.page.path.write_text(self.page.path.read_text(encoding="utf-8") + "New cache tradeoff.", encoding="utf-8")
        self.assertIsNone(wiki_index.load_wiki_index(root=self.root, directory=self.directory))
        update = self.sync()
        self.assertEqual(update.embedded_chunks, 1)
        self.assertEqual(update.embedded_queries, 0)
        self.assertEqual(update.reused_chunks, 1)
        self.assertEqual(len(self.fake.requests), 1)
        self.assertNotIn("Java request lifecycle", self.fake.requests[0])
        self.assertIn(second_page.id, wiki_index.load_wiki_index(root=self.root, directory=self.directory).search(["Java"]))

    def test_metadata_only_edit_reuses_vectors_without_api_key(self):
        self.sync()
        self.page.path.write_text(self.page.path.read_text(encoding="utf-8").replace('status: "approved"', 'status: approved'), encoding="utf-8")
        with patch.object(wiki_index, "create_embedding_client", side_effect=AssertionError("no remote call permitted")):
            result = wiki_index.sync_wiki_index(root=self.root, directory=self.directory)
        self.assertEqual(result.embedded_chunks, 0)
        self.assertIsNotNone(wiki_index.load_wiki_index(root=self.root, directory=self.directory))

    def test_changed_model_does_not_mix_vector_spaces(self):
        self.sync()
        self.fake.requests.clear()
        result = self.sync(model="a-different-model")
        self.assertGreater(result.embedded_chunks, 0)
        self.assertGreater(result.embedded_queries, 0)
        self.assertEqual(result.reused_chunks, 0)
        self.assertIsNone(wiki_index.load_wiki_index(root=self.root, directory=self.directory))
        self.assertIsNotNone(wiki_index.load_wiki_index(root=self.root, directory=self.directory, model="a-different-model"))

    def test_deletions_and_empty_vault_do_not_keep_stale_results(self):
        self.sync()
        self.page.path.unlink()
        self.fake.requests.clear()
        result = self.sync()
        self.assertEqual(result.pages, 0)
        self.assertEqual(self.fake.requests, [])
        self.assertIsNone(wiki_index.load_wiki_index(root=self.root, directory=self.directory))

    def test_failed_embedding_leaves_old_manifest_untouched(self):
        self.sync()
        manifest = (self.directory / "manifest.json").read_bytes()
        self.page.path.write_text(self.page.path.read_text(encoding="utf-8") + "New knowledge.", encoding="utf-8")
        with patch.object(wiki_index, "_embed", side_effect=RuntimeError("provider unavailable")):
            with self.assertRaises(RuntimeError):
                self.sync()
        self.assertEqual((self.directory / "manifest.json").read_bytes(), manifest)
        self.assertIsNone(wiki_index.load_wiki_index(root=self.root, directory=self.directory))

    def test_edit_during_embedding_does_not_publish_mixed_snapshot(self):
        self.sync()
        manifest = (self.directory / "manifest.json").read_bytes()
        self.page.path.write_text(self.page.path.read_text(encoding="utf-8") + "A first edit.", encoding="utf-8")
        def edit():
            self.page.path.write_text(self.page.path.read_text(encoding="utf-8") + "Concurrent Obsidian edit.", encoding="utf-8")
        self.fake.on_call = edit
        with self.assertRaises(ValueError):
            self.sync()
        self.assertEqual((self.directory / "manifest.json").read_bytes(), manifest)

    def test_index_is_unavailable_during_batch_publication(self):
        self.sync()
        (self.root / ".wiki-publishing.json").write_text('{"job_id":"publishing"}', encoding="utf-8")
        self.assertIsNone(wiki_index.load_wiki_index(root=self.root, directory=self.directory))
        with self.assertRaises(ValueError):
            self.sync()

    def test_invalid_vector_metadata_is_not_served(self):
        self.sync()
        path = self.directory / "manifest.json"
        meta = json.loads(path.read_text(encoding="utf-8"))
        meta["query_vectors"]["Redis"] = [float("nan")] * 1024
        path.write_text(json.dumps(meta), encoding="utf-8")
        self.assertIsNone(wiki_index.load_wiki_index(root=self.root, directory=self.directory))


if __name__ == "__main__":
    unittest.main()
