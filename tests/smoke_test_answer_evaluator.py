"""手动冒烟测试：验证 Answer Evaluator 的两个核心分支。"""

from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from answer_evaluator import answer_evaluate_node


def assert_evaluation_contract(result: dict) -> dict:
    assert isinstance(result, dict), "Evaluator 返回值必须是 dict"
    assert result["interview_status"] == "evaluation_ready"

    evaluation = result["evaluation_result"]
    required_fields = {
        "rating",
        "is_on_topic",
        "strengths",
        "missing_points",
        "feedback",
        "should_continue",
        "follow_up_focus",
    }
    assert required_fields <= evaluation.keys(), "评估结果缺少必需字段"
    return evaluation


def test_partial_answer() -> None:
    state = {
        "current_claim": {
            "technology": ["Redis", "SessionStore", "TTL"],
            "claim": "使用 Redis SessionStore 保存多轮对话历史，通过 session_id 隔离不同用户会话，并设置 TTL 自动清理。",
            "evidence": [
                "项目使用 Redis SessionStore 保存多轮对话历史，并通过 session_id 隔离不同用户的会话数据。",
                "Session 设置 TTL，过期后自动清理。",
            ],
            "follow_up_directions": ["会话数据结构", "TTL 设置策略", "会话续期"],
        },
        "current_question": "Session 到期时用户请求如何处理？你是否考虑了 TTL 续期策略？",
        "current_answer": "我会用 session_id 作为 Redis 的 key 保存会话。每次请求读取 Session；如果 Session 已过期或不存在，就返回未登录状态，让前端跳转登录页。",
    }

    evaluation = assert_evaluation_contract(answer_evaluate_node(state))
    assert evaluation["rating"] == "partial", evaluation
    assert evaluation["is_on_topic"] is True, evaluation
    assert evaluation["should_continue"] is True, evaluation
    print("✅ 部分回答分支通过")


def test_off_topic_answer() -> None:
    state = {
        "current_claim": {
            "technology": ["RAG", "向量检索", "SQL 元数据过滤"],
            "claim": "通过向量检索召回候选菜品，并结合 SQL 元数据过滤候选项。",
            "evidence": [
                "用户输入先进行向量召回，再通过 SQL JOIN 查询菜品价格、菜系、食材标签等元数据。"
            ],
            "follow_up_directions": ["召回策略", "过滤顺序"],
        },
        "current_question": "向量召回与 SQL 元数据过滤如何组织？",
        "current_answer": "我会用 Redis SessionStore 按 session_id 隔离会话，并通过 TTL 自动过期。",
    }

    evaluation = assert_evaluation_contract(answer_evaluate_node(state))
    assert evaluation["rating"] == "off_topic", evaluation
    assert evaluation["is_on_topic"] is False, evaluation
    assert evaluation["should_continue"] is True, evaluation
    print("✅ 答非所问分支通过")


if __name__ == "__main__":
    test_partial_answer()
    test_off_topic_answer()
    print("✅ Answer Evaluator 全部冒烟测试通过")
