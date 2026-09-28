"""决定本轮是沿当前主题追问，还是切换到未覆盖的新主张。"""

import hashlib
import json
from typing import Any

from question_bank import controlled_tags, load_question_bank


TOPIC_GROUPS = (
    ("retrieval", {"RAG", "向量检索", "SQL"}),
    ("session", {"Redis", "会话", "缓存"}),
    ("messaging", {"消息队列", "Kafka", "RabbitMQ", "幂等"}),
    ("reliability", {"防幻觉", "规则校验"}),
    ("agent", {"AI Agent", "工具调用", "工作流"}),
    ("architecture", {"架构", "微服务", "状态机"}),
    ("performance", {"限流", "性能", "异步"}),
)


def claim_id(claim: dict) -> str:
    tags = set(controlled_tags(claim, load_question_bank()))
    for name, related_tags in TOPIC_GROUPS:
        if tags & related_tags:
            return name
    payload = json.dumps({"technology": claim.get("technology", []), "claim": claim.get("claim", "")}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def topic_selector_node(state: dict[str, Any]) -> dict[str, Any]:
    claims = state.get("claims")
    if not isinstance(claims, list) or not claims:
        return {"interview_status": "no_claims" if not state.get("evaluation_history") else "no_more_topics"}

    evaluation = state.get("evaluation_result") or {}
    current = state.get("current_claim")
    if current and evaluation.get("should_continue") is True:
        if evaluation.get("rating") == "off_topic":
            stage = state.get("question_stage", "overview")
        else:
            stages = ("overview", "design", "tradeoff", "incident", "reflection")
            old = state.get("question_stage", "overview")
            stage = stages[min(stages.index(old) + 1, len(stages) - 1)] if old in stages else "design"
        return {"current_claim": current, "question_stage": stage, "interview_status": "topic_selected"}

    seen = set(state.get("selected_claim_ids", []))
    candidates = [claim for claim in claims if isinstance(claim, dict) and claim_id(claim) not in seen]
    if not candidates:
        return {"interview_status": "no_more_topics"}

    def priority(claim: dict) -> tuple[int, int]:
        text = str(claim.get("claim", ""))
        broad = sum(word in text.casefold() for word in ("管线", "架构", "平台", "项目", "流程", "agent"))
        return broad, len(claim.get("evidence", [])) if isinstance(claim.get("evidence"), list) else 0

    selected = max(candidates, key=priority)
    return {
        "current_claim": selected,
        "selected_claim_ids": [*state.get("selected_claim_ids", []), claim_id(selected)],
        "question_stage": "overview" if not state.get("turn_count") else "design",
        "interview_status": "topic_selected",
    }
