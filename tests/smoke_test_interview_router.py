"""手动冒烟测试：验证多轮面试条件路由规则。"""

from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from interview_router import interviewer_router


def test_continue_route() -> None:
    state = {
        "evaluation_result": {"should_continue": True},
        "turn_count": 1,
        "max_turns": 3,
    }
    assert interviewer_router(state) == "continue"
    print("✅ 继续追问分支通过")


def test_max_turns_route() -> None:
    state = {
        "evaluation_result": {"should_continue": True},
        "turn_count": 3,
        "max_turns": 3,
    }
    assert interviewer_router(state) == "end"
    print("✅ 最大轮数结束分支通过")


def test_evaluator_end_route() -> None:
    state = {
        "evaluation_result": {"should_continue": False},
        "turn_count": 1,
        "max_turns": 3,
    }
    assert interviewer_router(state) == "end"
    print("✅ Evaluator 结束分支通过")


if __name__ == "__main__":
    test_continue_route()
    test_max_turns_route()
    test_evaluator_end_route()
    print("✅ Interview Router 全部冒烟测试通过")
