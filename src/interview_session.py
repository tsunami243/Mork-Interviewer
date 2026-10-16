"""Browser-independent interview session adapter with durable checkpoints."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3
from typing import Any, Callable, Sequence
from uuid import uuid4

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from document_loader import LoadedDocument, combine_project_text
from interview_graph import create_interview_graph

MAX_ANSWER_CHARS = 3000


@dataclass
class InterviewRun:
    graph: Any
    thread_id: str
    result: dict[str, Any]
    document_ms: int = 0

    @property
    def config(self) -> dict[str, Any]:
        return {"configurable": {"thread_id": self.thread_id}}

    @property
    def state(self) -> dict[str, Any]:
        return self.graph.get_state(self.config).values

    @property
    def question(self) -> dict[str, Any] | None:
        interrupts = self.result.get("__interrupt__", ())
        if interrupts:
            return interrupts[0].value
        try:
            snapshot = self.graph.get_state(self.config)
            if snapshot.interrupts:
                return snapshot.interrupts[0].value
        except (AttributeError, KeyError, TypeError, ValueError):
            pass
        return None

    def resume(self, answer: str, on_progress: Callable[[dict], None] | None = None) -> None:
        if self.question is None:
            raise ValueError("当前面试没有待回答的问题")
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError("请先填写回答")
        if len(answer.strip()) > MAX_ANSWER_CHARS:
            raise ValueError(f"回答不能超过 {MAX_ANSWER_CHARS} 字符")
        self.result = _execute(self.graph, Command(resume=answer.strip()), self.config, on_progress)


def _execute(graph: Any, payload: Any, config: dict, on_progress: Callable[[dict], None] | None) -> dict:
    if on_progress is None:
        return graph.invoke(payload, config=config)
    interruption = None
    for mode, event in graph.stream(payload, config=config, stream_mode=["updates", "custom"]):
        if mode == "custom" and isinstance(event, dict):
            on_progress(event)
        elif mode == "updates" and "__interrupt__" in event:
            interruption = event["__interrupt__"]
    result = dict(graph.get_state(config).values)
    if interruption:
        result["__interrupt__"] = interruption
    return result


def start_interview(
    documents: Sequence[LoadedDocument],
    max_turns: int = 3,
    *,
    graph_factory: Callable[[], Any] | None = None,
    on_progress: Callable[[dict], None] | None = None,
    document_ms: int = 0,
    thread_id: str | None = None,
    profile_version_id: str | None = None,
) -> InterviewRun:
    """Parse documents once, then continue the same durable Graph checkpoint."""
    if type(max_turns) is not int or not 1 <= max_turns <= 8:
        raise ValueError("面试轮数须为 1 到 8 之间的整数")
    project_text = combine_project_text(documents)
    graph = (graph_factory or create_interview_graph)()
    thread_id = thread_id or uuid4().hex
    config = {"configurable": {"thread_id": thread_id}}
    result = _execute(graph, {
        "project_text": project_text,
        "interview_history": [],
        "evaluation_history": [],
        "turn_count": 0,
        "max_turns": max_turns,
        "profile_version_id": profile_version_id,
    }, config, on_progress)
    return InterviewRun(graph=graph, thread_id=thread_id, result=result, document_ms=document_ms)


def restore_interview(graph_factory: Callable[[], Any], thread_id: str, *, document_ms: int = 0) -> InterviewRun:
    graph = graph_factory()
    config = {"configurable": {"thread_id": thread_id}}
    snapshot = graph.get_state(config)
    result = {"__interrupt__": snapshot.interrupts} if snapshot.interrupts else {}
    return InterviewRun(graph=graph, thread_id=thread_id, result=result, document_ms=document_ms)


class PersistentCheckpoint:
    """Owns a SQLite connection for LangGraph checkpoints during app lifetime."""
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        self.saver = SqliteSaver(self.connection)
        self.saver.setup()

    def close(self) -> None:
        self.connection.close()
