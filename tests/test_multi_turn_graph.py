"""固定双轮回归：模拟模型与 A-RAG 输出，不请求远程服务。"""

import json
import os
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from langgraph.types import Command


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("LLM_API_KEY", "offline-test-key")
os.environ.setdefault("LLM_MODEL", "offline-test-model")

import answer_evaluator
import claim_extractor
import question_retrieval
import report_generator
from document_loader import LoadedDocument, TextSegment
from interview_graph import create_interview_graph
from interview_session import start_interview


CLAIM = {
    "technology": ["Redis SessionStore"],
    "claim": "项目使用 Redis 管理会话并设置 TTL。",
    "evidence": ["使用 Redis SessionStore 按 session_id 隔离会话，并设置 TTL。"],
    "follow_up_directions": ["过期处理", "续期策略"],
}
SECOND_CLAIM = {
    "technology": ["RAG"],
    "claim": "结合 RAG 检索设计项目推荐流程。",
    "evidence": ["用户输入先进行向量召回，再做 SQL 条件过滤。"],
    "follow_up_directions": ["检索设计", "过滤顺序"],
}
REPORT = {
    "overall_summary": "完成两轮面试。", "strengths": ["理解项目分工"],
    "weaknesses": [{"topic": "续期策略", "reason": "回答未涉及续期", "study_action": "复习会话过期"}],
    "next_practice_focus": ["继续训练项目设计"],
}


def answer(value):
    return SimpleNamespace(content=json.dumps(value, ensure_ascii=False))


def evaluation(continue_question: bool) -> dict:
    return {
        "rating": "partial" if continue_question else "strong", "is_on_topic": True,
        "strengths": ["回答了基础设计"], "missing_points": ["续期策略"],
        "feedback": "继续说明设计理由。", "should_continue": continue_question,
        "follow_up_focus": "TTL 续期怎样设计？",
    }


def reference(question: str, ref: str) -> dict:
    return {"question": question, "reason": "匹配项目面试题", "reference_ids": [ref], "retrieval_status": "ok", "tool_calls": 2}


class MultiTurnGraphTests(unittest.TestCase):
    def test_real_graph_streams_only_stage_metadata(self):
        with patch.object(claim_extractor, "llm") as extract, patch.object(question_retrieval, "run_retrieval_agent") as retrieve:
            extract.invoke.return_value = answer([CLAIM])
            retrieve.return_value = reference("你在项目中亲自负责什么？", "overview-01")
            events = []
            document = LoadedDocument("synthetic.md", "md", (TextSegment("Redis SessionStore 使用 session_id。"),))
            run = start_interview([document], on_progress=events.append)
            self.assertEqual(run.question["question"], "你在项目中亲自负责什么？")
            self.assertIn(("claim_extractor", "started"), [(event["stage"], event["status"]) for event in events])
            self.assertIn(("question_retrieval", "finished"), [(event["stage"], event["status"]) for event in events])
            self.assertNotIn("session_id", json.dumps(events, ensure_ascii=False))
            self.assertGreaterEqual(sum(item["model_calls"] for item in run.state["stage_metrics"]), 1)

    def test_no_claims_finishes_without_interrupt(self):
        with patch.object(claim_extractor, "llm") as extract:
            extract.invoke.return_value = answer([])
            graph = create_interview_graph()
            config = {"configurable": {"thread_id": "regression-no-claims"}}
            result = graph.invoke({"project_text": "普通项目介绍", "interview_history": [], "evaluation_history": [], "turn_count": 0, "max_turns": 3}, config=config)
            self.assertEqual(result["interview_status"], "no_claims")
            self.assertNotIn("__interrupt__", result)
            self.assertEqual(graph.get_state(config).next, ())

    def test_partial_answer_drives_follow_up_then_report(self):
        with (
            patch.object(claim_extractor, "llm") as extract,
            patch.object(question_retrieval, "run_retrieval_agent") as retrieve,
            patch.object(answer_evaluator, "llm") as assess,
            patch.object(report_generator, "llm") as report,
        ):
            extract.invoke.return_value = answer([CLAIM])
            retrieve.side_effect = [
                reference("你在这个项目里亲自负责什么？", "overview-01"),
                reference("TTL 续期是怎样设计的？", "design-03"),
            ]
            assess.invoke.side_effect = [answer(evaluation(True)), answer(evaluation(True))]
            report.invoke.return_value = answer(REPORT)

            graph = create_interview_graph()
            config = {"configurable": {"thread_id": "regression-two-turns"}}
            first = graph.invoke({"project_text": CLAIM["evidence"][0], "interview_history": [], "evaluation_history": [], "turn_count": 0, "max_turns": 2}, config=config)
            self.assertEqual(first["__interrupt__"][0].value["question"], "你在这个项目里亲自负责什么？")
            first_resume = graph.invoke(Command(resume="我负责会话隔离。"), config=config)
            self.assertEqual(first_resume["__interrupt__"][0].value["question"], "TTL 续期是怎样设计的？")
            self.assertEqual(retrieve.call_args_list[1].kwargs["previous_answer"], "我负责会话隔离。")
            self.assertIn("续期", retrieve.call_args_list[1].kwargs["follow_up_focus"])

            final = graph.invoke(Command(resume="接近过期时刷新 TTL。"), config=config)
            self.assertEqual(final["interview_status"], "completed")
            self.assertNotIn("__interrupt__", final)
            self.assertEqual(final["turn_count"], 2)
            self.assertEqual(len(final["interview_history"]), 2)
            self.assertEqual(len(final["evaluation_history"]), 2)
            self.assertEqual(final["used_question_ids"], ["overview-01", "design-03"])
            self.assertEqual([row["question"] for row in final["interview_history"]], ["你在这个项目里亲自负责什么？", "TTL 续期是怎样设计的？"])
            self.assertEqual(final["interview_report"], REPORT)
            self.assertEqual(graph.get_state(config).next, ())
            self.assertEqual(extract.invoke.call_count, 1)
            self.assertEqual(retrieve.call_count, 2)
            self.assertEqual(assess.invoke.call_count, 2)
            report.invoke.assert_called_once()
            self.assertTrue(any(item["stage"] == "question_retrieval" for item in final["stage_metrics"]))

    def test_strong_answer_switches_topic_instead_of_ending(self):
        with (
            patch.object(claim_extractor, "llm") as extract,
            patch.object(question_retrieval, "run_retrieval_agent") as retrieve,
            patch.object(answer_evaluator, "llm") as assess,
            patch.object(report_generator, "llm") as report,
        ):
            extract.invoke.return_value = answer([CLAIM, SECOND_CLAIM])
            retrieve.side_effect = [reference("这个项目怎样运作？", "overview-02"), reference("换个主题，你做过什么技术取舍？", "design-01")]
            assess.invoke.side_effect = [answer(evaluation(False)), answer(evaluation(False))]
            report.invoke.return_value = answer(REPORT)
            graph = create_interview_graph()
            config = {"configurable": {"thread_id": "regression-switch-topic"}}

            first = graph.invoke({"project_text": "两项项目材料", "interview_history": [], "evaluation_history": [], "turn_count": 0, "max_turns": 2}, config=config)
            self.assertIn("__interrupt__", first)
            first_claim = graph.get_state(config).values["current_claim"]
            second = graph.invoke(Command(resume="已经解释过当前设计。"), config=config)
            self.assertIn("__interrupt__", second, "答好一题但尚有未覆盖主张时应换话题")
            self.assertNotEqual(first_claim, graph.get_state(config).values["current_claim"])
            final = graph.invoke(Command(resume="第二个主题的设计理由。"), config=config)
            self.assertEqual(final["interview_status"], "completed")
            self.assertEqual(len(final["selected_claim_ids"]), 2)


if __name__ == "__main__":
    unittest.main()
