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
    interview_report: dict[str, Any] | None
    evaluation_result: dict[str, Any] | None
    interview_status: str
    turn_count: int
    max_turns: int
    evaluation_history: Annotated[list[dict[str, Any]], operator.add]
    stage_metrics: Annotated[list[dict[str, Any]], operator.add]
    selected_claim_ids: list[str]
    question_stage: str
    question_draft: str
    chosen_reference_ids: list[str]
    used_question_ids: Annotated[list[str], operator.add]
    retrieval_status: str
    retrieval_log: Annotated[list[dict[str, Any]], operator.add]
