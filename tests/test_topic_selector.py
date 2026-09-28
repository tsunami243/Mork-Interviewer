"""项目级主题去重、沿回答追问、答充分后换主题。"""

import os
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("LLM_API_KEY", "offline-test-key")
os.environ.setdefault("LLM_MODEL", "offline-test-model")

from topic_selector import claim_id, topic_selector_node


class TopicSelectorTests(unittest.TestCase):
    def test_related_redis_claims_count_as_one_topic(self):
        first = {"technology": ["Redis SessionStore"], "claim": "使用 Redis 保存用户会话"}
        second = {"technology": ["Redis TTL"], "claim": "会话过期后清理"}
        third = {"technology": ["RAG"], "claim": "项目结合检索构建推荐流程"}
        self.assertEqual(claim_id(first), claim_id(second))
        initial = topic_selector_node({"claims": [first, second, third], "turn_count": 0})
        next_state = topic_selector_node({
            "claims": [first, second, third], "turn_count": 1,
            "current_claim": initial["current_claim"],
            "evaluation_result": {"rating": "strong", "should_continue": False},
            "selected_claim_ids": initial["selected_claim_ids"],
        })
        self.assertNotEqual(claim_id(initial["current_claim"]), claim_id(next_state["current_claim"]))

    def test_partial_answer_stays_on_current_claim(self):
        claim = {"technology": ["RAG"], "claim": "使用 RAG 检索"}
        update = topic_selector_node({
            "claims": [claim], "turn_count": 1, "current_claim": claim,
            "question_stage": "overview",
            "evaluation_result": {"rating": "partial", "should_continue": True},
            "selected_claim_ids": [claim_id(claim)],
        })
        self.assertEqual(update["current_claim"], claim)
        self.assertEqual(update["question_stage"], "design")

    def test_no_remaining_topics_marks_report_path(self):
        claim = {"technology": ["Redis"], "claim": "保存会话"}
        update = topic_selector_node({
            "claims": [claim], "turn_count": 1,
            "selected_claim_ids": [claim_id(claim)],
            "evaluation_history": [{"evaluation": {"rating": "strong"}}],
        })
        self.assertEqual(update["interview_status"], "no_more_topics")


if __name__ == "__main__":
    unittest.main()
