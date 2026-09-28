"""无网络单元测试：Router、结构化输出校验及报告节点。"""

import json
import os
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
# 模块在 import 时构造 ChatOpenAI；离线回归用无效占位值，绝不调用真实 API。
os.environ.setdefault("LLM_API_KEY", "offline-test-key")
os.environ.setdefault("LLM_MODEL", "offline-test-model")

import report_generator
from answer_evaluator import validate_evaluation_result
from interview_router import interviewer_router
from json_utils import parse_json_object


class RouterTests(unittest.TestCase):
    def test_continue_when_evaluator_requests_it(self):
        state = {"evaluation_result": {"should_continue": True}, "turn_count": 1, "max_turns": 3}
        self.assertEqual(interviewer_router(state), "next_topic")

    def test_reach_limit_even_when_evaluator_requests_continue(self):
        state = {"evaluation_result": {"should_continue": True}, "turn_count": 3, "max_turns": 3}
        self.assertEqual(interviewer_router(state), "report_generator")

    def test_strong_answer_moves_to_topic_selection(self):
        state = {"evaluation_result": {"should_continue": False}, "turn_count": 1, "max_turns": 3}
        self.assertEqual(interviewer_router(state), "next_topic")

    def test_invalid_counter_and_decision(self):
        for state in (
            {"evaluation_result": {"should_continue": True}, "turn_count": -1, "max_turns": 3},
            {"evaluation_result": {"should_continue": True}, "turn_count": 1, "max_turns": 0},
            {"evaluation_result": {"should_continue": "yes"}, "turn_count": 1, "max_turns": 3},
        ):
            with self.subTest(state=state), self.assertRaises(ValueError):
                interviewer_router(state)


class JsonAndEvaluatorContractTests(unittest.TestCase):
    def test_parse_json_object_with_code_fence(self):
        self.assertEqual(parse_json_object('```json\n{"rating": "partial"}\n```'), {"rating": "partial"})

    def test_reject_json_array_and_malformed_json(self):
        for response in ("[]", "{invalid"):
            with self.subTest(response=response), self.assertRaises(ValueError):
                parse_json_object(response)

    def test_evaluator_rejects_inconsistent_rating(self):
        invalid = {
            "rating": "off_topic", "is_on_topic": True, "strengths": [],
            "missing_points": ["缺少说明"], "feedback": "答非所问",
            "should_continue": True, "follow_up_focus": "回到原问题",
        }
        with self.assertRaises(ValueError):
            validate_evaluation_result(invalid)


class ReportGeneratorTests(unittest.TestCase):
    def setUp(self):
        self.record = {
            "claim": {"technology": ["Redis"], "evidence": ["使用 Redis 存储会话。"]},
            "question": "如何处理过期？", "answer": "过期则重新登录。",
            "evaluation": {"rating": "partial", "missing_points": ["续期策略"]},
        }
        self.report = {
            "overall_summary": "完成一轮模拟面试。", "strengths": ["理解过期处理"],
            "weaknesses": [{"topic": "续期策略", "reason": "回答未覆盖续期", "study_action": "比较固定与滑动过期"}],
            "next_practice_focus": ["练习过期边界"],
        }

    def test_report_is_structured_and_receives_full_history(self):
        with patch.object(report_generator, "llm") as mock_llm:
            mock_llm.invoke.return_value = SimpleNamespace(content=json.dumps(self.report, ensure_ascii=False))
            result = report_generator.report_generator_node({"evaluation_history": [self.record], "turn_count": 1})
        self.assertEqual(result, {"interview_report": self.report, "interview_status": "completed"})
        context = json.loads(mock_llm.invoke.call_args.args[0][1].content.split("\n", 1)[1].rsplit("\n", 1)[0])
        self.assertEqual(context["turn_count"], 1)
        self.assertEqual(context["evaluation_history"], [self.record])

    def test_report_rejects_empty_history_without_model_call(self):
        with patch.object(report_generator, "llm") as mock_llm:
            with self.assertRaises(ValueError):
                report_generator.report_generator_node({"evaluation_history": [], "turn_count": 0})
        mock_llm.invoke.assert_not_called()

    def test_report_rejects_missing_output_field(self):
        malformed = {key: value for key, value in self.report.items() if key != "weaknesses"}
        with patch.object(report_generator, "llm") as mock_llm:
            mock_llm.invoke.return_value = SimpleNamespace(content=json.dumps(malformed, ensure_ascii=False))
            with self.assertRaisesRegex(ValueError, "weaknesses"):
                report_generator.report_generator_node({"evaluation_history": [self.record], "turn_count": 1})


if __name__ == "__main__":
    unittest.main()
