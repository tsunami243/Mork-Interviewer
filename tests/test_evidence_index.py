"""证据块构建不需要调用模型或访问网络。"""

from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from document_loader import LoadedDocument, TextSegment, load_document
from evidence_index import build_evidence_index


class EvidenceIndexTests(unittest.TestCase):
    def test_sample_project_produces_traceable_chunks(self):
        document = load_document(PROJECT_ROOT / "data" / "sample_project.md")
        chunks = build_evidence_index([document], chunk_size=500)

        self.assertTrue(chunks)
        self.assertEqual(len({chunk.chunk_id for chunk in chunks}), len(chunks))
        self.assertTrue(all(chunk.source == "sample_project.md" for chunk in chunks))
        self.assertTrue(all(chunk.page is None for chunk in chunks))
        self.assertTrue(all(len(chunk.text) <= 500 for chunk in chunks))
        self.assertTrue(any("Redis" in chunk.text for chunk in chunks))

    def test_pdf_pages_and_document_ids_do_not_mix(self):
        resume = LoadedDocument("resume.pdf", "pdf", (
            TextSegment("第一页材料", page=1),
            TextSegment("第二页材料", page=2),
        ))
        project = LoadedDocument("project.md", "md", (TextSegment("项目经历"),))
        chunks = build_evidence_index([resume, project])

        self.assertEqual([chunk.page for chunk in chunks], [1, 2, None])
        self.assertEqual([chunk.source for chunk in chunks], ["resume.pdf", "resume.pdf", "project.md"])
        self.assertEqual(len({chunk.chunk_id for chunk in chunks}), 3)

    def test_long_paragraph_splits_without_losing_characters(self):
        text = "技术实现细节" * 31
        document = LoadedDocument("project.md", "md", (TextSegment(text),))
        chunks = build_evidence_index([document], chunk_size=20)
        self.assertEqual("".join(chunk.text for chunk in chunks), text)
        self.assertTrue(all(len(chunk.text) <= 20 for chunk in chunks))

    def test_empty_and_invalid_size(self):
        self.assertEqual(build_evidence_index([]), [])
        with self.assertRaises(ValueError):
            build_evidence_index([], chunk_size=0)


if __name__ == "__main__":
    unittest.main()
