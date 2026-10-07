"""选择参考题并定稿；评分仍由独立 Evaluator 根据用户材料完成。"""

import os
from typing import Any

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

from embedding_index import embedding_settings, load_question_index
from question_bank import controlled_tags, load_question_bank
from question_retrieval_agent import run_retrieval_agent, _acceptable_question
from question_retrieval_tools import QuestionRetrievalTools


load_dotenv()
llm = ChatOpenAI(model=os.getenv("LLM_MODEL"), api_key=os.getenv("LLM_API_KEY"), base_url=os.getenv("LLM_BASE_URL"), temperature=0)


def question_retrieval_node(state: dict[str, Any]) -> dict[str, Any]:
    claim = state.get("current_claim")
    if not isinstance(claim, dict):
        raise ValueError("检索参考题前必须先选择 current_claim")
    bank = load_question_bank()
    stage = state.get("question_stage", "overview")
    _, _, model = embedding_settings()
    index = load_question_index(bank, model)
    tools = QuestionRetrievalTools(bank, stage, controlled_tags(claim, bank), set(state.get("used_question_ids", [])), index)
    evaluation = state.get("evaluation_result") or {}
    selection = run_retrieval_agent(
        tools, llm, claim,
        previous_question=str(state.get("current_question") or ""),
        previous_answer=str(state.get("current_answer") or ""),
        follow_up_focus=str(evaluation.get("follow_up_focus") or ""),
    )
    return {
        "question_draft": selection["question"],
        "question_reason": selection["reason"],
        "chosen_reference_ids": selection["reference_ids"],
        "chosen_wiki_ids": selection.get("wiki_reference_ids", []),
        "retrieval_status": selection["retrieval_status"],
        "retrieval_log": [{"stage": stage, "status": selection["retrieval_status"], "tool_calls": selection["tool_calls"], "reference_ids": selection["reference_ids"], "wiki_ids": selection.get("wiki_reference_ids", [])}],
        "interview_status": "references_ready",
    }


def question_finalizer_node(state: dict[str, Any]) -> dict[str, Any]:
    draft = state.get("question_draft")
    if not _acceptable_question(draft):
        raise ValueError("出题节点未生成合格的单一问题")
    if state.get("turn_count", 0) == 0 and state.get("question_stage") == "overview":
        draft = "请介绍一下这个项目的目标和你负责的核心工作？"
    asked = {turn.get("question") for turn in state.get("interview_history", []) if isinstance(turn, dict)}
    if draft.strip() in asked:
        claim = state["current_claim"]
        tech = claim.get("technology", ["这部分设计"])
        subject = str(tech[0]) if isinstance(tech, list) and tech else "这部分设计"
        draft = f"围绕{subject}，你自己做过的一个设计取舍是什么？"
    return {
        "current_question": draft.strip(),
        "used_question_ids": state.get("chosen_reference_ids", []),
        "interview_status": "question_ready",
    }
