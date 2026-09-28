"""项目内原创参考题库；上传的简历内容不会进入此题库。"""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path


BANK_PATH = Path(__file__).resolve().parents[1] / "data" / "question_bank.json"
STAGES = ("overview", "design", "tradeoff", "incident", "reflection")


@dataclass(frozen=True)
class QuestionReference:
    id: str
    stage: str
    tags: tuple[str, ...]
    intent: str
    preview: str
    question: str


def load_question_bank(path: Path = BANK_PATH) -> list[QuestionReference]:
    entries = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(entries, list) or not entries:
        raise ValueError("参考题库必须是非空 JSON 列表")
    questions: list[QuestionReference] = []
    ids: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or not all(
            isinstance(entry.get(key), str) and entry[key].strip()
            for key in ("id", "stage", "intent", "preview", "question")
        ):
            raise ValueError("参考题缺少必要的非空文本字段")
        if entry["id"] in ids or entry["stage"] not in STAGES:
            raise ValueError(f"重复 ID 或无效阶段：{entry['id']}")
        tags = entry.get("tags")
        if not isinstance(tags, list) or not tags or not all(isinstance(tag, str) and tag.strip() for tag in tags):
            raise ValueError(f"参考题标签不合法：{entry['id']}")
        ids.add(entry["id"])
        questions.append(QuestionReference(entry["id"], entry["stage"], tuple(tags), entry["intent"], entry["preview"], entry["question"]))
    return questions


def bank_fingerprint(bank: list[QuestionReference], model: str, dimensions: int) -> str:
    payload = [(item.id, item.stage, item.tags, item.intent, item.preview, item.question) for item in bank]
    canonical = json.dumps({"questions": payload, "model": model, "dimensions": dimensions}, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


ALIASES = {
    "sessionstore": "会话", "session_id": "会话", "ttl": "会话",
    "pgvector": "向量检索", "embedding": "向量检索", "rag": "RAG",
    "react": "AI Agent", "langgraph": "AI Agent", "agent": "AI Agent",
    "kafka": "Kafka", "rabbitmq": "RabbitMQ", "mq": "消息队列",
    "redis": "Redis", "fastapi": "FastAPI", "spring": "Java",
    "sentinel": "限流", "setnx": "幂等", "docker": "架构",
    "prometheus": "监控", "grafana": "监控",
}


def controlled_tags(claim: dict, bank: list[QuestionReference]) -> list[str]:
    """只返回题库受控标签，不把简历句子发给 Embedding 服务。"""
    allowed = {tag for entry in bank for tag in entry.tags} - {"通用"}
    technologies = claim.get("technology", [])
    if not isinstance(technologies, list):
        technologies = []
    raw = " ".join(str(item) for item in technologies).casefold()
    found = [tag for tag in sorted(allowed) if tag.casefold() in raw]
    found.extend(tag for alias, tag in ALIASES.items() if alias in raw and tag in allowed)
    return list(dict.fromkeys(found))[:4] or ["通用"]


def questions_for_stage(bank: list[QuestionReference], stage: str, excluded: set[str] | None = None) -> list[QuestionReference]:
    excluded = excluded or set()
    return [item for item in bank if item.stage == stage and item.id not in excluded]
