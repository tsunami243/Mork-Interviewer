"""模拟面试 LangGraph：材料分析、追问循环与最终报告。"""

import json

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from answer_evaluator import answer_evaluate_node
from claim_extractor import claim_extractor_node
from document_loader import combine_project_text, load_document
from interview_router import interviewer_router
from interview_state import InterviewState
from interviewer import interviewer_node
from question_retrieval import question_finalizer_node, question_retrieval_node
from report_generator import report_generator_node
from telemetry import measured_node
from topic_selector import topic_selector_node


def route_selected_topic(state: InterviewState) -> str:
    """无主张时结束；有过问答但主题用尽时生成报告。"""
    if state.get("interview_status") == "no_claims":
        return "end"
    if state.get("interview_status") == "no_more_topics":
        return "report_generator"
    return "question_retrieval"

def create_interview_graph():
    """创建并编译支持多轮 interrupt/resume 的模拟面试工作流。"""
    workflow = StateGraph(InterviewState)

    workflow.add_node("claim_extractor", measured_node("claim_extractor", claim_extractor_node))
    workflow.add_node("topic_selector", measured_node("topic_selector", topic_selector_node))
    workflow.add_node("question_retrieval", measured_node("question_retrieval", question_retrieval_node))
    workflow.add_node("question_finalizer", measured_node("question_finalizer", question_finalizer_node))
    workflow.add_node("interviewer", measured_node("interviewer", interviewer_node))
    workflow.add_node("answer_evaluator", measured_node("answer_evaluator", answer_evaluate_node))
    workflow.add_node("report_generator", measured_node("report_generator", report_generator_node))

    workflow.add_edge(START, "claim_extractor")
    workflow.add_edge("claim_extractor", "topic_selector")
    workflow.add_conditional_edges(
        "topic_selector", route_selected_topic,
        {"question_retrieval": "question_retrieval", "report_generator": "report_generator", "end": END},
    )
    workflow.add_edge("question_retrieval", "question_finalizer")
    workflow.add_edge("question_finalizer", "interviewer")
    workflow.add_edge("interviewer", "answer_evaluator")
    workflow.add_conditional_edges(
        "answer_evaluator",
        interviewer_router,
        {
            "next_topic": "topic_selector",
            "report_generator": "report_generator",
        },
    )
    workflow.add_edge("report_generator", END)

    checkpointer = InMemorySaver()
    return workflow.compile(checkpointer=checkpointer)


def print_evaluation(evaluation: dict) -> None:
    """在终端中展示刚完成一轮的评估结果。"""
    print("\n=== 本轮评估 ===")
    print(f"等级：{evaluation['rating']}")
    print(f"反馈：{evaluation['feedback']}")

    if evaluation["missing_points"]:
        print("待补充：")
        for point in evaluation["missing_points"]:
            print(f"- {point}")


def main() -> None:
    """在终端持续处理每次 interrupt，直到工作流生成最终报告。"""
    sample_path = input("请上传您的简历文件")
    project_path = input("请上传您的项目文件")
    resume = load_document(sample_path)
    project = load_document(project_path)
    project_text = combine_project_text([resume,project])



    app = create_interview_graph()
    config = {"configurable": {"thread_id": "demo-interview-001"}}
    result = app.invoke(
        {
            "project_text": project_text,
            "interview_history": [],
            "evaluation_history": [],
            "turn_count": 0,
            "max_turns": 5,
        },
        config=config,
    )

    while result.get("__interrupt__"):
        interrupt_payload = result["__interrupt__"][0].value
        current_turn = result.get("turn_count", 0) + 1

        print(f"\n=== 第 {current_turn} 轮模拟面试 ===")
        print(interrupt_payload["question"])
        print(f"技术方向：{', '.join(interrupt_payload['technology'])}")

        user_answer = input("\n你的回答：").strip()
        if not user_answer:
            print("未输入回答，面试已结束。")
            return

        result = app.invoke(Command(resume=user_answer), config=config)

        evaluation = result.get("evaluation_result")
        if isinstance(evaluation, dict):
            print_evaluation(evaluation)

    report = result.get("interview_report")
    if isinstance(report, dict):
        print("\n=== 最终面试报告 ===")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return

    raise RuntimeError(f"工作流未生成报告，最终状态：{result.get('interview_status')}")


if __name__ == "__main__":
    main()
