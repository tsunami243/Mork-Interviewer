"""最小模拟面试图：材料分析 → 出题 → 人工回答。"""

from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from answer_evaluator import answer_evaluate_node
from claim_extractor import claim_extractor_node, read_markdown
from interview_state import InterviewState
from interviewer import interviewer_node
from question_planner import question_planner_node


def create_interview_graph():
    """创建并编译支持 interrupt/resume 的最小工作流。"""
    workflow = StateGraph(InterviewState)

    workflow.add_node("claim_extractor", claim_extractor_node)
    workflow.add_node("question_planner", question_planner_node)
    workflow.add_node("interviewer", interviewer_node)
    workflow.add_node("answer_evaluator", answer_evaluate_node)

    workflow.add_edge(START, "claim_extractor")
    workflow.add_edge("claim_extractor", "question_planner")
    workflow.add_edge("question_planner", "interviewer")
    workflow.add_edge("interviewer", "answer_evaluator")
    workflow.add_edge("answer_evaluator", END)

    checkpointer = InMemorySaver()
    return workflow.compile(checkpointer=checkpointer)


def main() -> None:
    """在终端演示一次 interrupt → Command(resume=...) 的完整流程。"""
    project_root = Path(__file__).resolve().parents[1]
    sample_path = project_root / "data" / "sample_project.md"
    project_text = read_markdown(sample_path)

    app = create_interview_graph()
    config = {"configurable": {"thread_id": "demo-interview-001"}}

    first_result = app.invoke(
        {
            "project_text": project_text,
            "interview_history": [],
        },
        config=config,
    )

    interrupts = first_result.get("__interrupt__")
    if not interrupts:
        raise RuntimeError("工作流没有在 Interviewer 节点暂停")

    interrupt_payload = interrupts[0].value
    print("\n=== 模拟面试问题 ===")
    print(interrupt_payload["question"])
    print(f"技术方向：{', '.join(interrupt_payload['technology'])}")

    user_answer = input("\n你的回答：").strip()
    if not user_answer:
        print("未输入回答，面试已结束。")
        return

    final_result = app.invoke(Command(resume=user_answer), config=config)
    print("\n=== 本轮面试记录 ===")
    print(final_result["interview_history"][-1])
    print("\n=== 回答评估 ===")
    print(final_result["evaluation_result"])
    print(f"\n当前状态：{final_result['interview_status']}")


if __name__ == "__main__":
    main()
