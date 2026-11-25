"""Offline Web recovery regressions using the real graph and SQLite checkpointer."""

from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from time import sleep
from unittest import TestCase
from unittest.mock import patch

from fastapi.testclient import TestClient

from test_multi_turn_graph import CLAIM, REPORT, answer, evaluation, reference

import answer_evaluator
import claim_extractor
import question_retrieval
import report_generator
from api_server import COOKIE, create_app
from interview_store import InterviewStore
from wiki_intake import IntakeService


HEADERS = {"X-Workbench-Request": "1"}
MATERIALS = {
    "resume_file": ("resume.md", b"Synthetic resume", "text/markdown"),
    "project_file": ("project.md", CLAIM["evidence"][0].encode(), "text/markdown"),
}


class WebInterviewRecoveryTests(TestCase):
    def setUp(self):
        temporary = self.enterContext(TemporaryDirectory())
        self.base = Path(temporary)

    def client(self, cookie=None):
        service = IntakeService(self.base / "vault", self.base / "private")
        return TestClient(create_app(service), cookies={COOKIE: cookie} if cookie else None)

    def start(self, client, materials=MATERIALS):
        response = client.post("/api/interviews", headers=HEADERS, files=materials, data={"max_turns": 2})
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()["id"]

    def wait_for(self, client, session_id, status):
        path = f"/api/interviews/{session_id}"
        for _ in range(100):
            response = client.get(path)
            self.assertEqual(response.status_code, 200, response.text)
            session = response.json()
            if session["status"] == status:
                return session
            sleep(0.025)
        self.fail(f"Expected {status} for {session_id}. Last response was {session}")

    def test_resume_real_graph_after_reopening_both_sqlite_databases(self):
        with (
            patch.object(claim_extractor, "llm") as extract,
            patch.object(question_retrieval, "run_retrieval_agent") as retrieve,
            patch.object(answer_evaluator, "llm") as assess,
            patch.object(report_generator, "llm") as report,
        ):
            extract.invoke.return_value = answer([CLAIM])
            retrieve.side_effect = [reference("项目职责是什么？", "overview-01"), reference("如何续期？", "design-03")]
            assess.invoke.side_effect = [answer(evaluation(True)), answer(evaluation(True))]
            report.invoke.return_value = answer(REPORT)

            with self.client() as client:
                cookie = client.get("/api/bootstrap").cookies[COOKIE]
                session_id = self.start(client)
                path = f"/api/interviews/{session_id}"
                self.wait_for(client, session_id, "waiting")
                response = client.post(f"{path}/answers", headers=HEADERS, json={"request_id": "first", "answer": "负责会话隔离。"})
                self.assertEqual(response.status_code, 202, response.text)
                before_restart = self.wait_for(client, session_id, "waiting")
                self.assertEqual(before_restart["turn_count"], 1)

            with self.client() as other_client:
                other_client.get("/api/bootstrap")
                self.assertEqual(other_client.get(path).status_code, 404)

            with self.client(cookie) as client:
                bootstrapped = client.get("/api/bootstrap").json()["sessions"]
                self.assertEqual(len(bootstrapped), 1)
                self.assertEqual(bootstrapped[0], before_restart)
                response = client.post(f"{path}/answers", headers=HEADERS, json={"request_id": "second", "answer": "接近过期时刷新 TTL。"})
                self.assertEqual(response.status_code, 202, response.text)
                completed = self.wait_for(client, session_id, "completed")
                self.assertEqual(completed["report"], REPORT)
                self.assertEqual([turn["answer"] for turn in completed["history"]], ["负责会话隔离。", "接近过期时刷新 TTL。"])

            with self.client(cookie) as client:
                self.assertEqual(client.get(path).json(), completed)

            extract.invoke.assert_called_once()
            self.assertEqual(assess.invoke.call_count, 2)
            report.invoke.assert_called_once()

    def test_legacy_sessions_keep_saved_history_and_reports_without_a_checkpoint(self):
        cookie = "a" * 32
        owner = sha256(cookie.encode()).hexdigest()
        store = InterviewStore(self.base / "private" / "interviews.db")
        history = [{"question": "第一题", "answer": "第一轮回答"}]
        evaluations = [{"evaluation": evaluation(True)}]
        for status in ("waiting", "completed"):
            store.create(session_id=status, owner=owner, name=status, max_turns=2, profile_version_id=None, profile_snapshot=None)
            store.save(status, status=status, phase="等待回答" if status == "waiting" else "面试结束", turn=1, state={
                "turn_count": 1, "interview_history": history, "evaluation_history": evaluations,
                "interview_report": REPORT if status == "completed" else None, "project_text": "private material",
            })

        with self.client(cookie) as client:
            response = client.get("/api/interviews")
            sessions = {session["id"]: session for session in response.json()["sessions"]}
            self.assertNotIn("private material", response.text)
            self.assertEqual(sessions["completed"]["status"], "completed")
            self.assertEqual(sessions["completed"]["report"], REPORT)
            self.assertEqual(sessions["waiting"]["status"], "interrupted")
            self.assertIsNone(sessions["waiting"]["question"])
            self.assertTrue(sessions["waiting"]["error"])
            for session in sessions.values():
                self.assertEqual(session["history"], history)
                self.assertEqual(session["evaluations"], evaluations)
            retry = client.post("/api/interviews/waiting/answers", headers=HEADERS, json={"request_id": "retry", "answer": "回答"})
            self.assertEqual(retry.status_code, 409, retry.text)

    def test_preparation_error_remains_visible_after_restart(self):
        with self.client() as client:
            cookie = client.get("/api/bootstrap").cookies[COOKIE]
            bad_resume = {**MATERIALS, "resume_file": ("invalid.pdf", b"invalid PDF", "application/pdf")}
            session_id = self.start(client, bad_resume)
            failure = self.wait_for(client, session_id, "error")
            self.assertIn("PDF", failure["error"])

        with self.client(cookie) as client:
            recovered = client.get(f"/api/interviews/{session_id}").json()
            self.assertEqual(recovered["status"], "error")
            self.assertEqual(recovered["error"], failure["error"])
            self.assertFalse(recovered["busy"])
