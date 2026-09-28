from typing import Any, Literal


def interviewer_router(state: dict[str, Any]) -> Literal["next_topic", "report_generator"]:
    """达到轮数上限后出报告，否则让主题节点决定追问或换主题。"""
    evaluation_result = state.get("evaluation_result")
    turn_count = state.get("turn_count")
    max_turns = state.get("max_turns")

    if not isinstance(turn_count, int) or turn_count < 0:
        raise ValueError("state['turn_count'] 必须是非负整数")

    if not isinstance(max_turns, int) or max_turns < 1:
        raise ValueError("state['max_turns'] 必须是大于 0 的整数")

    if not isinstance(evaluation_result, dict):
        return "report_generator"

    should_continue = evaluation_result.get("should_continue")
    if not isinstance(should_continue, bool):
        raise ValueError("evaluation_result['should_continue'] 必须是 bool")

    if turn_count >= max_turns:
        return "report_generator"

    return "next_topic"
