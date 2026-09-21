"""Interview Agent 在 LangGraph 节点之间共享的状态定义。"""

import operator
from typing import Annotated, Any, TypedDict


class InterviewState(TypedDict, total=False):
    """所有节点只读取需要的字段，并返回自己产生的状态更新。"""

    project_text: str
    claims: list[dict[str, Any]]
    current_claim: dict[str, Any] | None
    current_question: str | None
    question_reason: str | None
    current_answer: str | None
    interview_history: Annotated[list[dict[str, Any]], operator.add]
    evaluation_result: dict[str, Any] | None
    interview_status: str
