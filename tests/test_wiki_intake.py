"""Offline two-phase intake, recovery and reviewed multi-page publication tests."""

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
from wiki_intake import IntakeService, split_source
import wiki_publish
from wiki_publish import PublishConflict
from wiki_store import approve_draft, list_pages
from wiki_index import load_wiki_index, sync_wiki_index


TEXT = "Redis stores cached data. TTL expires keys."


def analysis(quote="Redis stores cached data.", old_id=None, conflict=None):
    return {"summary": "Redis 缓存与过期。", "topics": [{"title": "Redis Cache", "tags": ["Redis"],
        "key_points": ["讨论缓存与过期"], "quotes": [quote], "existing_page_id": old_id,
        "related_ids": [], "conflicts": [conflict] if conflict else []}]}


def generation(old_id=None, quote="Redis stores cached data.", conflict=None, question=True):
    concept = {"id": old_id or "cache", "type": "concept", "title": "Redis Cache", "body": "缓存知识与边界。",
               "tags": ["Redis"], "links": [], "quotes": [quote], "conflicts": [conflict] if conflict else []}
    pages = [concept]
    if question:
        concept["links"] = ["cache-question"]
        pages.append({"id": "cache-question", "type": "question", "title": "缓存设计追问", "body": "先问缓存动机，再问失效取舍。",
                      "tags": ["Redis"], "links": [old_id or "cache"], "quotes": [quote], "conflicts": []})
    return {"pages": pages}


class FakeLLM:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = []

    def invoke(self, messages):
        payload = json.loads(messages[-1].content)
        self.calls.append(payload)
        value = next(self.replies)
        if isinstance(value, Exception):
            raise value
        if callable(value):
            value = value(payload)
        return SimpleNamespace(content=json.dumps(value, ensure_ascii=False))


class FakeEmbeddings:
    def __init__(self):
        self.requests = []

    def create(self, *, model, input, dimensions):
        self.requests.extend(input)
        return SimpleNamespace(data=[SimpleNamespace(index=n, embedding=[1.] + [0.] * (dimensions-1)) for n in range(len(input))])


class IntakeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.service = IntakeService(self.base / "vault", self.base / "private")
        self.document = load_uploaded_document("cache.md", TEXT.encode())

    def tearDown(self):
        self.tmp.cleanup()

    def draft(self, document=None, replies=None):
        job, reused = self.service.enqueue(document or self.document)
        self.assertFalse(reused)
        llm = FakeLLM(replies or [analysis(), generation()])
        job = self.service.process_next(llm, job_id=job["id"])
        self.assertEqual(job["status"], "needs_review", job["error"])
        return job, llm

    def old_page(self):
        document = load_uploaded_document("old.md", b"Previous cache source.")
        return approve_draft(document, {"title": "Redis Cache", "body": "Older knowledge must be preserved.", "tags": ["Redis"], "links": []}, self.service.root)

    def test_two_phases_multi_page_review_and_provenance(self):
        job, llm = self.draft()
        self.assertEqual(len(llm.calls), 2)
        self.assertEqual(list_pages(self.service.root), [])
        self.assertFalse((self.service.root / "sources").exists())
        result = self.service.approve(job["id"])
        self.assertEqual(result["status"], "completed")
        pages = list_pages(self.service.root)
        self.assertEqual({page.type for page in pages}, {"source", "concept", "question"})
        ids = {page.id for page in pages}
        self.assertTrue(all(set(page.links) <= ids for page in pages))
        concept = next(page for page in pages if page.type == "concept")
        self.assertEqual(concept.evidence[0]["quote"], "Redis stores cached data.")
        self.assertTrue((self.service.root / concept.sources[0]).exists())
        self.assertTrue((self.service.root / "overview.md").exists())
        self.assertIn(job["id"], (self.service.root / "log.md").read_text(encoding="utf-8"))
        with self.assertRaises(ValueError):
            self.service.approve(job["id"])

    def test_content_dedup_after_restart_and_configuration_changes(self):
        job, _ = self.draft()
        restarted = IntakeService(self.service.root, self.service.directory)
        duplicate, reused = restarted.enqueue(self.document)
        self.assertTrue(reused)
        self.assertEqual(duplicate["id"], job["id"])
        self.service.approve(job["id"])
        duplicate, reused = restarted.enqueue(load_uploaded_document("renamed.md", TEXT.encode()))
        self.assertTrue(reused)
        self.assertEqual(duplicate["status"], "completed")
        (self.service.root / "purpose.md").write_text("新的建库目标", encoding="utf-8")
        different, reused = restarted.enqueue(self.document)
        self.assertFalse(reused)
        self.assertNotEqual(different["id"], job["id"])

    def test_force_reingest_uses_existing_page_diff_and_immutable_sources(self):
        job, _ = self.draft()
        self.service.approve(job["id"])
        concept = next(p for p in list_pages(self.service.root) if p.type == "concept")
        second, reused = self.service.enqueue(self.document, force=True)
        self.assertFalse(reused)
        drafted = self.service.process_next(FakeLLM([analysis(old_id=concept.id), generation(concept.id, question=False)]))
        source = next(p for p in drafted["payload"]["proposal"]["pages"] if p["type"] == "source")
        self.assertEqual(source["action"], "update")
        self.assertTrue(source["before_body"])
        self.assertEqual(self.service.approve(second["id"])["status"], "completed")
        renamed = load_uploaded_document("renamed.md", TEXT.encode())
        third, reused = self.service.enqueue(renamed, force=True)
        self.assertFalse(reused)
        drafted = self.service.process_next(FakeLLM([analysis(old_id=concept.id), generation(concept.id, question=False)]))
        self.assertNotEqual(drafted["payload"]["proposal"]["source_path"], job["payload"]["proposal"]["source_path"])
        self.assertEqual(self.service.approve(third["id"])["status"], "completed")

    def test_missing_published_page_invalidates_ingest_cache(self):
        job, _ = self.draft()
        self.service.approve(job["id"])
        next(p for p in list_pages(self.service.root) if p.type == "concept").path.unlink()
        regenerated, reused = self.service.enqueue(self.document)
        self.assertFalse(reused)
        self.assertNotEqual(regenerated["id"], job["id"])

    def test_long_documents_are_mapped_without_resending_whole_original(self):
        text = ("Redis stores cached data. " + "细节" * 650 + "\n\n") * 20
        document = load_uploaded_document("long.md", text.encode())
        job, _ = self.service.enqueue(document)
        chunks = job["payload"]["chunks"]
        self.assertGreater(len(chunks), 2)
        self.assertTrue(all(len(part["text"]) <= 8000 for part in chunks))
        self.assertEqual(chunks[0]["start"], 0)
        self.assertEqual(chunks[-1]["end"], len(job["payload"]["content"]))
        self.assertTrue(all(right["start"] <= left["end"] for left, right in zip(chunks, chunks[1:])))
        llm = FakeLLM([analysis()] * len(chunks) + [generation()])
        finished = self.service.process_next(llm)
        self.assertEqual(finished["status"], "needs_review", finished["error"])
        self.assertNotIn("source_text", llm.calls[-1])
        self.assertEqual(len(llm.calls[-1]["analysis"]["summaries"]), len(chunks))
        self.assertEqual(len(finished["payload"]["analyses"]), len(chunks))

    def test_merge_preserves_sources_and_requires_conflict_acknowledgement(self):
        old = self.old_page()
        conflict = "旧资料没有讨论TTL，新资料补充过期条件，需确认适用范围。"
        job, _ = self.draft(replies=[analysis(old_id=old.id, conflict=conflict), generation(old.id, conflict=conflict, question=False)])
        updated = next(page for page in job["payload"]["proposal"]["pages"] if page["id"] == old.id)
        self.assertEqual(updated["action"], "update")
        self.assertIn("Older knowledge", updated["before_body"])
        with self.assertRaises(ValueError):
            self.service.approve(job["id"])
        self.assertEqual(old.path.read_text(encoding="utf-8").count("Older knowledge"), 1)
        result = self.service.approve(job["id"], {old.id: {"body": "Older knowledge must be preserved. 新增TTL说明。"}}, acknowledge_conflicts=True)
        page = next(p for p in list_pages(self.service.root) if p.id == old.id)
        self.assertEqual(len(page.sources), 2)
        self.assertIn(old.sources[0], page.sources)
        self.assertIn("Older knowledge", page.body)
        journal = json.loads((self.service.directory / "transactions" / f"{job['id']}.json").read_text(encoding="utf-8"))
        self.assertIn("Older knowledge", next(entry["before"] for entry in journal["files"] if entry["path"] == updated["path"]))
        self.assertEqual(result["status"], "completed")

    def test_publication_cannot_overwrite_configuration_files(self):
        job, _ = self.draft()
        data = job["payload"]
        data["proposal"]["pages"][1]["path"] = "purpose.md"
        self.service.queue.save(job["id"], payload=data, status="needs_review")
        with self.assertRaises(ValueError):
            self.service.approve(job["id"])
        self.assertFalse((self.service.root / "purpose.md").exists())
        self.assertEqual(list_pages(self.service.root), [])

    def test_external_obsidian_edit_is_not_overwritten(self):
        old = self.old_page()
        job, _ = self.draft(replies=[analysis(old_id=old.id), generation(old.id, question=False)])
        old.path.write_text(old.path.read_text(encoding="utf-8") + "\nObsidian edit", encoding="utf-8")
        with self.assertRaises(PublishConflict):
            self.service.approve(job["id"])
        self.assertIn("Obsidian edit", old.path.read_text(encoding="utf-8"))
        self.assertEqual(self.service.queue.get(job["id"])["status"], "needs_review")
        self.assertFalse((self.service.root / job["payload"]["proposal"]["source_path"]).exists())

    def test_forged_analysis_quote_never_publishes(self):
        job, _ = self.service.enqueue(self.document)
        result = self.service.process_next(FakeLLM([analysis("Invented claim.")]))
        self.assertEqual(result["status"], "failed")
        self.assertIn("逐字", result["error"])
        self.assertEqual(list_pages(self.service.root), [])

    def test_compile_failure_retry_uses_saved_analysis(self):
        job, _ = self.service.enqueue(self.document)
        failed = self.service.process_next(FakeLLM([analysis(), generation(quote="not in source")]))
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(len(failed["payload"]["analyses"]), 1)
        self.service.queue.retry(job["id"])
        llm = FakeLLM([generation()])
        result = self.service.process_next(llm)
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(len(llm.calls), 1)

    def test_unknown_links_and_unsafe_ids_rejected(self):
        for field, value in (("links", ["../../secret"]), ("id", "../../outside")):
            with self.subTest(field=field):
                service = IntakeService(self.base / field, self.base / (field + "private"))
                service.enqueue(self.document)
                generated = generation(question=False)
                generated["pages"][0][field] = value
                result = service.process_next(FakeLLM([analysis(), generated]))
                self.assertEqual(result["status"], "failed")
                self.assertFalse((service.root / "sources").exists())

    def test_cancelled_running_task_stops_before_compile(self):
        job, _ = self.service.enqueue(self.document)
        def cancel(payload):
            self.service.queue.cancel(job["id"])
            return analysis()
        llm = FakeLLM([cancel])
        result = self.service.process_next(llm)
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(list_pages(self.service.root), [])

    def test_queue_serializes_workers_and_recovers_dead_process(self):
        first, _ = self.service.enqueue(self.document)
        second, _ = self.service.enqueue(load_uploaded_document("other.md", b"Other source."))
        claimed = self.service.queue.claim(first["id"])
        self.assertEqual(claimed["status"], "running")
        self.assertIsNone(self.service.queue.claim(second["id"]))
        with self.service.queue.connection() as db:
            db.execute("UPDATE jobs SET owner_pid=2147483647 WHERE id=?", (first["id"],))
        restarted = IntakeService(self.service.root, self.service.directory)
        restarted.recover()
        self.assertEqual(restarted.queue.get(first["id"])["status"], "queued")
        self.assertIsNotNone(restarted.queue.claim(first["id"]))

    def test_provider_error_does_not_store_credentials_or_raw_body(self):
        job, _ = self.service.enqueue(self.document)
        result = self.service.process_next(FakeLLM([ValueError("api-key-secret and private input")]))
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("api-key-secret", result["error"])
        self.assertNotIn("private input", result["error"])

    def test_failure_attempt_limit_and_explicit_regeneration(self):
        job, _ = self.service.enqueue(self.document)
        for attempt in range(3):
            result = self.service.process_next(FakeLLM([RuntimeError("unavailable")]))
            self.assertEqual(result["attempts"], attempt + 1)
            if attempt < 2:
                self.service.queue.retry(job["id"])
        with self.assertRaises(ValueError):
            self.service.queue.retry(job["id"])
        self.service.queue.cancel(job["id"])
        replacement, reused = self.service.enqueue(self.document)
        self.assertFalse(reused)
        self.assertNotEqual(replacement["id"], job["id"])

    def test_interrupted_publication_is_hidden_then_rolled_back(self):
        job, _ = self.draft()
        real = wiki_publish.atomic_write
        def interrupted(path, text):
            real(path, text)
            if path.parent == self.service.root / "sources":
                raise SystemExit("simulated crash")
        with patch.object(wiki_publish, "atomic_write", side_effect=interrupted):
            with self.assertRaises(SystemExit):
                self.service.approve(job["id"])
        self.assertTrue((self.service.root / ".wiki-publishing.json").exists())
        self.assertEqual(list_pages(self.service.root), [])
        with self.service.queue.connection() as db:
            db.execute("UPDATE jobs SET owner_pid=2147483647 WHERE id=?", (job["id"],))
        restarted = IntakeService(self.service.root, self.service.directory)
        restarted.recover()
        self.assertFalse((self.service.root / ".wiki-publishing.json").exists())
        self.assertEqual(restarted.queue.get(job["id"])["status"], "needs_review")
        self.assertFalse((self.service.root / job["payload"]["proposal"]["source_path"]).exists())
        self.assertEqual(restarted.approve(job["id"])["status"], "completed")

    def test_file_commit_recovers_receipt_if_status_save_was_interrupted(self):
        job, _ = self.draft()
        real = self.service.queue.save
        def crash(job_id, **kwargs):
            if kwargs.get("status") == "completed":
                raise SystemExit("status commit interrupted")
            return real(job_id, **kwargs)
        first_id = job["payload"]["proposal"]["pages"][1]["id"]
        with patch.object(self.service.queue, "save", side_effect=crash):
            with self.assertRaises(SystemExit):
                self.service.approve(job["id"], {first_id: {"body": "Reviewed edit persists."}})
        with self.service.queue.connection() as db:
            db.execute("UPDATE jobs SET owner_pid=2147483647 WHERE id=?", (job["id"],))
        restarted = IntakeService(self.service.root, self.service.directory)
        restarted.recover()
        recovered = restarted.queue.get(job["id"])
        self.assertEqual(recovered["status"], "completed")
        self.assertEqual(next(p for p in recovered["payload"]["proposal"]["pages"] if p["id"] == first_id)["body"], "Reviewed edit persists.")
        self.assertEqual(len(list_pages(self.service.root)), 3)

    def test_ordinary_write_failure_rolls_back_whole_batch(self):
        job, _ = self.draft()
        real = wiki_publish.atomic_write
        failed = False
        def fail_once(path, text):
            nonlocal failed
            if path.parent == self.service.root / "concepts" and not failed:
                failed = True
                raise OSError("disk unavailable")
            return real(path, text)
        with patch.object(wiki_publish, "atomic_write", side_effect=fail_once):
            with self.assertRaises(OSError):
                self.service.approve(job["id"])
        self.assertFalse((self.service.root / ".wiki-publishing.json").exists())
        self.assertEqual(list_pages(self.service.root), [])
        self.assertFalse((self.service.root / job["payload"]["proposal"]["source_path"]).exists())

    def test_publishing_with_index_updates_only_private_test_index(self):
        job, _ = self.draft()
        fake = FakeEmbeddings()
        result = self.service.approve(job["id"], update_index=True, embedding_client=SimpleNamespace(embeddings=fake))
        self.assertEqual(result["payload"]["receipt"]["index_status"], "ready")
        self.assertIsNotNone(load_wiki_index(root=self.service.root, directory=self.service.index_directory))
        self.assertTrue(fake.requests)

    def test_failed_embedding_keeps_approved_pages(self):
        job, _ = self.draft()
        bad = SimpleNamespace(embeddings=SimpleNamespace(create=lambda **_: (_ for _ in ()).throw(RuntimeError("unavailable"))))
        result = self.service.approve(job["id"], update_index=True, embedding_client=bad)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["payload"]["receipt"]["index_status"], "failed")
        self.assertEqual(len(list_pages(self.service.root)), 3)
        self.assertIsNone(load_wiki_index(root=self.service.root, directory=self.service.index_directory))


if __name__ == "__main__":
    unittest.main()
