"""页面冒烟测试：不调用模型、不启动浏览器。"""

from pathlib import Path
from types import SimpleNamespace
import unittest

from streamlit.testing.v1 import AppTest


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


class FakeRun:
    def __init__(self, *, question=None, report=None):
        self.question = question
        self.state = {
            "turn_count": 1 if report else 0,
            "max_turns": 3,
            "interview_history": [],
            "evaluation_history": [],
            "interview_report": report,
            "interview_status": "completed" if report else "question_ready",
        }


class StreamlitPageTests(unittest.TestCase):
    def test_landing_page_and_missing_uploads(self):
        page = AppTest.from_file(str(APP_PATH), default_timeout=10).run()
        self.assertFalse(page.exception)
        self.assertEqual(len(page.get("file_uploader")), 2)

        page.button[0].click().run()
        self.assertFalse(page.exception)
        self.assertTrue(any("同时上传" in element.value for element in page.error))

    def test_current_question_is_shown(self):
        page = AppTest.from_file(str(APP_PATH), default_timeout=10)
        page.session_state["interview_run"] = FakeRun(
            question={"question": "TTL 过期时你会怎样处理？", "technology": ["Redis"]}
        )
        page.run()
        self.assertFalse(page.exception)
        self.assertTrue(any("TTL 过期时" in element.value for element in page.get("markdown")))
        self.assertEqual(len(page.get("text_area")), 1)

    def test_final_report_is_shown(self):
        report = {
            "overall_summary": "完成一次项目面试。",
            "strengths": ["清楚说明会话隔离"],
            "weaknesses": [{"topic": "TTL 策略", "reason": "未说明续期", "study_action": "比较固定与滑动过期"}],
            "next_practice_focus": ["继续练习 Redis"],
        }
        page = AppTest.from_file(str(APP_PATH), default_timeout=10)
        page.session_state["interview_run"] = FakeRun(report=report)
        page.run()
        self.assertFalse(page.exception)
        visible = " ".join(element.value for element in page.get("markdown"))
        self.assertIn("完成一次项目面试", visible)
        self.assertTrue(any("TTL 策略" in element.label for element in page.get("expander")))


if __name__ == "__main__":
    unittest.main()
