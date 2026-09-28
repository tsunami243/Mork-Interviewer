"""Streamlit 无关的入口测试：文件合并、会话隔离、恢复输入校验。"""

import os
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
os.environ.setdefault("LLM_API_KEY", "offline-test-key")
os.environ.setdefault("LLM_MODEL", "offline-test-model")

from document_loader import LoadedDocument, TextSegment
from interview_session import MAX_ANSWER_CHARS, start_interview
from langgraph.types import Command


class FakeGraph:
    def __init__(self):
        self.calls = []
        self.values = {}

    def invoke(self, payload, config):
        self.calls.append((payload, config))
        if isinstance(payload, dict):
            self.values = payload.copy()
            return {"__interrupt__": [SimpleNamespace(value={"question": "如何设计会话隔离？", "technology": ["Redis"]})]}
        self.values.update({"turn_count": 1, "interview_status": "completed", "interview_report": {"overall_summary": "结束"}})
        return self.values.copy()

    def get_state(self, config):
        return SimpleNamespace(values=self.values)

    def stream(self, payload, config, stream_mode):
        yield "custom", {"stage": "claim_extractor", "status": "started"}
        update = self.invoke(payload, config)
        yield "updates", update
        yield "custom", {"stage": "claim_extractor", "status": "finished", "elapsed_ms": 2}


class InterviewSessionTests(unittest.TestCase):
    def setUp(self):
        self.documents = [
            LoadedDocument("简历.pdf", "pdf", (TextSegment("使用 Redis。", page=1),)),
            LoadedDocument("项目.md", "md", (TextSegment("会话按 session_id 隔离。"),)),
        ]

    def test_start_resume_and_single_parse(self):
        graph = FakeGraph()
        run = start_interview(self.documents, max_turns=2, graph_factory=lambda: graph)
        initial, initial_config = graph.calls[0]
        self.assertIn("[来源：简历.pdf，第 1 页]", initial["project_text"])
        self.assertIn("[来源：项目.md]", initial["project_text"])
        self.assertEqual(initial["max_turns"], 2)
        self.assertEqual(run.question["question"], "如何设计会话隔离？")

        run.resume("  我使用 session_id 作为 key。  ")
        self.assertEqual(len(graph.calls), 2, "恢复不应重新解析并传入 project_text")
        resume_payload, resume_config = graph.calls[1]
        self.assertIsInstance(resume_payload, Command)
        self.assertEqual(resume_payload.resume, "我使用 session_id 作为 key。")
        self.assertEqual(resume_config, initial_config)
        self.assertIsNone(run.question)
        self.assertEqual(run.state["interview_status"], "completed")

    def test_invalid_answer_does_not_resume_graph(self):
        graph = FakeGraph()
        run = start_interview(self.documents, graph_factory=lambda: graph)
        for answer in ("   ", "a" * (MAX_ANSWER_CHARS + 1)):
            with self.subTest(answer_length=len(answer)), self.assertRaises(ValueError):
                run.resume(answer)
        self.assertEqual(len(graph.calls), 1)

    def test_each_interview_has_different_thread_id(self):
        first = start_interview(self.documents, graph_factory=FakeGraph)
        second = start_interview(self.documents, graph_factory=FakeGraph)
        self.assertNotEqual(first.thread_id, second.thread_id)

    def test_invalid_turn_count(self):
        for value in (0, 9, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                start_interview(self.documents, max_turns=value, graph_factory=FakeGraph)

    def test_stream_progress_keeps_interrupt_and_checkpoint(self):
        events = []
        graph = FakeGraph()
        run = start_interview(self.documents, graph_factory=lambda: graph, on_progress=events.append)
        self.assertEqual(run.question["question"], "如何设计会话隔离？")
        self.assertEqual([event["status"] for event in events], ["started", "finished"])
        run.resume("使用 session_id 隔离。", on_progress=events.append)
        self.assertIsNone(run.question)
        self.assertEqual(run.state["interview_status"], "completed")
        self.assertEqual(graph.calls[0][1], graph.calls[1][1])


if __name__ == "__main__":
    unittest.main()
