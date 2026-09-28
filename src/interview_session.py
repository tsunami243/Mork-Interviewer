"""浏览器无关的面试会话适配层：启动、暂停与恢复。"""

from dataclasses import dataclass
from typing import Any, Callable, Sequence
from uuid import uuid4

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
        return interrupts[0].value if interrupts else None

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
) -> InterviewRun:
    """文档只在开始时解析；之后各轮只恢复同一个 Graph 检查点。"""
    if type(max_turns) is not int or not 1 <= max_turns <= 8:
        raise ValueError("面试轮数须为 1 到 8 之间的整数")
    project_text = combine_project_text(documents)
    graph = (graph_factory or create_interview_graph)()
    thread_id = uuid4().hex
    config = {"configurable": {"thread_id": thread_id}}
    result = _execute(graph,
        {
            "project_text": project_text,
            "interview_history": [],
            "evaluation_history": [],
            "turn_count": 0,
            "max_turns": max_turns,
        },
        config,
        on_progress,
    )
    return InterviewRun(graph=graph, thread_id=thread_id, result=result, document_ms=document_ms)
