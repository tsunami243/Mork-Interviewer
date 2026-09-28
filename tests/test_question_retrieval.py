"""题库、离线向量构建、三工具、检索预算和隐私边界。"""

from dataclasses import replace
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

import embedding_index
import question_retrieval
from question_bank import controlled_tags, load_question_bank
from question_retrieval_agent import MAX_TOOL_CALLS, parse_retrieval_action, run_retrieval_agent
from question_retrieval_tools import QuestionRetrievalTools


class FakeEmbeddings:
    def __init__(self):
        self.requests = []

    def create(self, *, model, input, dimensions):
        self.requests.extend(input)
        return SimpleNamespace(data=[SimpleNamespace(index=i, embedding=[1.0] + [0.0] * (dimensions - 1)) for i in range(len(input))])


class FakeLLM:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def invoke(self, messages):
        self.calls.append(messages)
        return SimpleNamespace(content=json.dumps(next(self.responses), ensure_ascii=False))


class QuestionRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.bank = load_question_bank()

    def test_bank_is_original_structured_and_questions_are_single(self):
        self.assertGreaterEqual(len(self.bank), 40)
        self.assertEqual(len({item.id for item in self.bank}), len(self.bank))
        for item in self.bank:
            self.assertEqual(item.question.count("？") + item.question.count("?"), 1, item.id)
            self.assertTrue(item.preview and item.intent and item.tags)

    def test_deepseek_parameter_suffix_is_accepted_but_arbitrary_tail_is_not(self):
        parsed = parse_retrieval_action('{"action":"finish","question":"项目是什么？"}</parameter>')
        self.assertEqual(parsed["action"], "finish")
        with self.assertRaises(ValueError):
            parse_retrieval_action('{"action":"finish"} 其他文本')

    def test_tags_sent_for_semantic_query_cannot_contain_private_text(self):
        claim = {"technology": ["Redis SessionStore 18096048426@163.com", "TTL"], "claim": "候选人的个人经历"}
        tags = controlled_tags(claim, self.bank)
        self.assertTrue("Redis" in tags)
        self.assertNotIn("18096048426", " ".join(tags))
        self.assertTrue(set(tags) <= {tag for item in self.bank for tag in item.tags})

    def test_offline_index_builds_from_public_bank_and_invalidates_on_change(self):
        fake = FakeEmbeddings()
        client = SimpleNamespace(embeddings=fake)
        with TemporaryDirectory() as directory, patch.object(embedding_index, "INDEX_DIR", Path(directory)):
            index_file = embedding_index.build_question_index(self.bank, client)
            self.assertTrue(index_file.exists())
            loaded = embedding_index.load_question_index(self.bank)
            self.assertEqual(len(loaded["question_vectors"]), len(self.bank))
            self.assertEqual(len(loaded["question_vectors"][self.bank[0].id]), 1024)
            self.assertTrue(all("18096048426" not in text for text in fake.requests))
            changed = [replace(self.bank[0], question="新的原创问题？"), *self.bank[1:]]
            self.assertIsNone(embedding_index.load_question_index(changed))

    def test_embedding_failure_does_not_leave_an_index(self):
        failing = SimpleNamespace(embeddings=SimpleNamespace(create=lambda **_: (_ for _ in ()).throw(RuntimeError("provider unavailable"))))
        with TemporaryDirectory() as directory, patch.object(embedding_index, "INDEX_DIR", Path(directory)):
            with self.assertRaises(RuntimeError):
                embedding_index.build_question_index(self.bank, failing)
            self.assertIsNone(embedding_index.load_question_index(self.bank))

    def test_three_tools_search_read_and_embedding_fallback(self):
        tools = QuestionRetrievalTools(self.bank, "design", ["Redis", "会话"], set(), None)
        keyword = tools.keyword_search(["Redis"])
        self.assertGreater(keyword["tool_log"]["chunks_found"], 0)
        read = tools.chunk_read(keyword["references"][0])
        self.assertIn("意图", read["tool_result"])
        self.assertLessEqual(len(read["references"]), 2)
        self.assertEqual(tools.semantic_search()["tool_log"]["status"], "embedding_unavailable")

        query = [1.0] + [0.0] * 1023
        index = {
            "question_vectors": {entry.id: query for entry in self.bank},
            "query_vectors": {"design:Redis": query},
        }
        semantic = QuestionRetrievalTools(self.bank, "design", ["Redis"], set(), index).semantic_search()
        self.assertEqual(semantic["tool_log"]["status"], "ok")
        self.assertGreater(len(semantic["references"]), 0)

    def test_agent_uses_tools_and_stops_at_budget(self):
        tools = QuestionRetrievalTools(self.bank, "design", ["Redis"], set(), {"question_vectors": {}, "query_vectors": {}})
        client = FakeLLM([
            {"action": "chunk_read", "reference_id": "design-03"},
            {"action": "finish", "question": "你怎样保证两个用户的对话不会读到同一份历史？", "reason": "追问会话隔离"},
        ])
        result = run_retrieval_agent(tools, client, {"technology": ["Redis"]}, previous_answer="用 session_id 区分。")
        self.assertEqual(result["tool_calls"], 2)
        self.assertIn("design-03", result["reference_ids"])
        self.assertIn("不会读到", result["question"])

        repeated = FakeLLM([{"action": "keyword_search", "keywords": ["Redis"]}] * (MAX_TOOL_CALLS + 1))
        fallback = run_retrieval_agent(QuestionRetrievalTools(self.bank, "design", ["Redis"], set(), {"question_vectors": {}, "query_vectors": {}}), repeated, {"technology": ["Redis"]})
        self.assertEqual(fallback["tool_calls"], MAX_TOOL_CALLS)
        self.assertEqual(fallback["retrieval_status"], "budget_exhausted")

    def test_retrieval_node_never_calls_embedding_api_during_interview(self):
        with patch.object(question_retrieval, "load_question_index", return_value=None), patch.object(question_retrieval, "run_retrieval_agent") as run:
            run.return_value = {"question": "你的设计目的是什么？", "reason": "项目总览", "reference_ids": ["overview-03"], "retrieval_status": "embedding_unavailable", "tool_calls": 2}
            update = question_retrieval.question_retrieval_node({"current_claim": {"technology": ["Redis SessionStore"]}, "question_stage": "overview", "used_question_ids": []})
        self.assertEqual(update["retrieval_status"], "embedding_unavailable")
        self.assertTrue(set(run.call_args.args[0].tags) <= {tag for entry in self.bank for tag in entry.tags})


if __name__ == "__main__":
    unittest.main()
