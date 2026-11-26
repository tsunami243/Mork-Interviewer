"""Offline Web interview lifecycle tests using the real graph and SQLite checkpointer."""

from contextlib import closing
from hashlib import sha256
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from threading import Event
from time import sleep
from unittest import TestCase
from unittest.mock import patch

from fastapi.testclient import TestClient
from langgraph.checkpoint.sqlite import SqliteSaver

from test_multi_turn_graph import CLAIM, REPORT, answer, evaluation, reference

import answer_evaluator
import claim_extractor
import question_retrieval
import report_generator
from api_server import COOKIE, create_app
from interview_store import InterviewStore
from profile_store import ProfileStore
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

    def checkpoint_counts(self, session_id):
        with closing(sqlite3.connect(self.base / "private" / "checkpoints.db")) as database:
            return [
                database.execute(f"SELECT count(*) FROM {table} WHERE thread_id = ?", (session_id,)).fetchone()[0]
                for table in ("checkpoints", "writes")
            ]

    def test_delete_purges_only_the_owned_interview_after_restart(self):
        with (
            patch.object(claim_extractor, "llm") as extract,
            patch.object(question_retrieval, "run_retrieval_agent", return_value=reference("项目职责是什么？", "overview-01")),
            patch.object(answer_evaluator, "llm") as assess,
            patch.object(report_generator, "llm") as report,
        ):
            extract.invoke.return_value = answer([CLAIM])
            assess.invoke.side_effect = [answer(evaluation(True)), answer(evaluation(False))]
            report.invoke.return_value = answer(REPORT)

            with self.client() as client:
                cookie = client.get("/api/bootstrap").cookies[COOKIE]
                target = self.start(client)
                path = f"/api/interviews/{target}"
                self.wait_for(client, target, "waiting")
                response = client.post(f"{path}/answers", headers=HEADERS, json={"request_id": "first", "answer": "负责会话隔离。"})
                self.assertEqual(response.status_code, 202, response.text)
                target_state = self.wait_for(client, target, "waiting")
                self.assertEqual(target_state["turn_count"], 1)
                survivor = self.start(client)
                survivor_state = self.wait_for(client, survivor, "waiting")
                profile_id = target_state["profile_version_id"]
                self.assertIsNotNone(profile_id)
                self.assertEqual(survivor_state["profile_version_id"], profile_id)

            counts = self.checkpoint_counts(target)
            self.assertTrue(all(counts), counts)
            with self.client() as other:
                other.get("/api/bootstrap")
                self.assertEqual(other.delete(path, headers=HEADERS).status_code, 404)
            self.assertEqual(self.checkpoint_counts(target), counts)

            with self.client(cookie) as client:
                response = client.delete(path, headers=HEADERS)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json(), {"deleted": target})
                self.assertEqual(client.post(f"{path}/answers", headers=HEADERS, json={"request_id": "late", "answer": "late"}).status_code, 404)

            with self.client(cookie) as client:
                self.assertEqual(client.get(path).status_code, 404)
                self.assertEqual([row["id"] for row in client.get("/api/interviews").json()["sessions"]], [survivor])
                self.assertEqual(self.checkpoint_counts(target), [0, 0])
                store = InterviewStore(self.base / "private" / "interviews.db")
                with store.connection() as database:
                    for table in ("interview_sessions", "interview_messages", "interview_events"):
                        column = "id" if table == "interview_sessions" else "session_id"
                        self.assertEqual(database.execute(f"SELECT count(*) FROM {table} WHERE {column} = ?", (target,)).fetchone()[0], 0)
                self.assertIsNotNone(ProfileStore(self.base / "private" / "profile").snapshot(profile_id))
                response = client.post(f"/api/interviews/{survivor}/answers", headers=HEADERS, json={"request_id": "first", "answer": "负责刷新 TTL。"})
                self.assertEqual(response.status_code, 202, response.text)
                self.assertEqual(self.wait_for(client, survivor, "completed")["report"], REPORT)

    def test_delete_refuses_an_active_interview_until_idle(self):
        entered = Event()
        release = Event()

        def blocked_response(value):
            def invoke(*args, **kwargs):
                entered.set()
                if not release.wait(10):
                    raise TimeoutError("test did not release the model response")
                return answer(value)
            return invoke

        def assert_busy_delete(client, path):
            try:
                self.assertTrue(entered.wait(10))
                self.assertEqual(client.delete(path, headers=HEADERS).status_code, 409)
            finally:
                release.set()

        with (
            patch.object(claim_extractor, "llm") as extract,
            patch.object(question_retrieval, "run_retrieval_agent", return_value=reference("项目职责是什么？", "overview-01")),
            patch.object(answer_evaluator, "llm") as assess,
            patch.object(report_generator, "llm") as report,
            self.client() as client,
        ):
            extract.invoke.side_effect = blocked_response([CLAIM])
            assess.invoke.side_effect = blocked_response(evaluation(False))
            report.invoke.return_value = answer(REPORT)
            client.get("/api/bootstrap")
            session_id = self.start(client)
            path = f"/api/interviews/{session_id}"
            assert_busy_delete(client, path)
            self.wait_for(client, session_id, "waiting")
            entered.clear()
            release.clear()
            response = client.post(f"{path}/answers", headers=HEADERS, json={"request_id": "first", "answer": "负责会话隔离。"})
            self.assertEqual(response.status_code, 202, response.text)
            assert_busy_delete(client, path)
            self.wait_for(client, session_id, "completed")
            self.assertEqual(client.delete(path, headers=HEADERS).status_code, 200)
            self.assertEqual(self.checkpoint_counts(session_id), [0, 0])

    def test_delete_can_retry_after_either_database_fails(self):
        with (
            patch.object(claim_extractor, "llm") as extract,
            patch.object(question_retrieval, "run_retrieval_agent", return_value=reference("项目职责是什么？", "overview-01")),
            self.client() as client,
        ):
            extract.invoke.return_value = answer([CLAIM])
            client.get("/api/bootstrap")
            session_id = self.start(client)
            path = f"/api/interviews/{session_id}"
            original = self.wait_for(client, session_id, "waiting")
            counts = self.checkpoint_counts(session_id)

            with patch.object(SqliteSaver, "delete_thread", side_effect=sqlite3.OperationalError("private database path")):
                response = client.delete(path, headers=HEADERS)
            self.assertEqual(response.status_code, 503, response.text)
            self.assertNotIn("private database path", response.text)
            self.assertEqual(self.checkpoint_counts(session_id), counts)
            self.assertEqual(client.get(path).json(), original)

            with patch.object(InterviewStore, "delete", side_effect=sqlite3.OperationalError("private database path")):
                response = client.delete(path, headers=HEADERS)
            self.assertEqual(response.status_code, 503, response.text)
            self.assertNotIn("private database path", response.text)
            self.assertEqual(self.checkpoint_counts(session_id), [0, 0])
            self.assertEqual(client.get(path).json()["status"], "interrupted")
            response = client.post(f"{path}/answers", headers=HEADERS, json={"request_id": "late", "answer": "late"})
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(client.delete(path, headers=HEADERS).status_code, 200)
            self.assertEqual(client.get(path).status_code, 404)

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
            deleted = client.delete("/api/interviews/waiting", headers=HEADERS)
            self.assertEqual(deleted.status_code, 200, deleted.text)
            self.assertEqual(client.get("/api/interviews/waiting").status_code, 404)

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
