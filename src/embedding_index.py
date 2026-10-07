"""百炼兼容 Embedding 的离线题库索引；用户资料绝不写入索引。"""

import json
import math
import os
from pathlib import Path
from typing import Any

from openai import OpenAI

from question_bank import QuestionReference, STAGES, bank_fingerprint


DEFAULT_MODEL = "text-embedding-v4"
DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DIMENSIONS = 1024
INDEX_DIR = Path(__file__).resolve().parents[1] / "data" / ".question-index"


def embedding_settings() -> tuple[str, str, str]:
    """与面试 LLM 凭据分离，缺少 Key 时不自动使用旧 Key。"""
    return (
        os.getenv("DASHSCOPE_API_KEY", ""),
        os.getenv("EMBEDDING_BASE_URL", DEFAULT_BASE_URL),
        os.getenv("EMBEDDING_MODEL", DEFAULT_MODEL),
    )


def index_path(bank: list[QuestionReference], model: str, dimensions: int = DIMENSIONS) -> Path:
    return INDEX_DIR / f"questions-{bank_fingerprint(bank, model, dimensions)}.json"


def _normalized(vector: list[float]) -> list[float]:
    if len(vector) != DIMENSIONS or not all(math.isfinite(value) for value in vector):
        raise ValueError(f"Embedding 向量必须是 {DIMENSIONS} 维有限数值")
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        raise ValueError("Embedding 向量不能为零向量")
    return [round(value / norm, 7) for value in vector]


def _embed(client: Any, model: str, texts: list[str]) -> list[list[float]]:
    vectors = []
    for offset in range(0, len(texts), 10):
        batch = texts[offset : offset + 10]
        response = client.embeddings.create(model=model, input=batch, dimensions=DIMENSIONS)
        ordered = sorted(response.data, key=lambda item: item.index)
        if len(ordered) != len(batch):
            raise ValueError("Embedding 服务返回的向量数量不一致")
        vectors.extend(_normalized(item.embedding) for item in ordered)
    return vectors


def build_question_index(bank: list[QuestionReference], client: Any, model: str = DEFAULT_MODEL) -> Path:
    """仅在运维/开发者显式执行脚本时调用远程服务。"""
    query_keys = sorted({f"{stage}:{tag}" for stage in STAGES for tag in {tag for item in bank for tag in item.tags}})
    texts = [f"{item.stage} {item.intent} {item.preview} {item.question}" for item in bank]
    vectors = _embed(client, model, texts + query_keys)
    path = index_path(bank, model)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = {
        "fingerprint": bank_fingerprint(bank, model, DIMENSIONS),
        "model": model,
        "dimensions": DIMENSIONS,
        "question_vectors": {item.id: vector for item, vector in zip(bank, vectors[:len(bank)])},
        "query_vectors": {key: vector for key, vector in zip(query_keys, vectors[len(bank):])},
    }
    path.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
    return path


def load_question_index(bank: list[QuestionReference], model: str = DEFAULT_MODEL) -> dict | None:
    path = index_path(bank, model)
    if not path.is_file():
        return None
    try:
        content = json.loads(path.read_text(encoding="utf-8"))
        if content["fingerprint"] != bank_fingerprint(bank, model, DIMENSIONS):
            return None
        if content["model"] != model or content["dimensions"] != DIMENSIONS:
            return None
        if set(content["question_vectors"]) != {item.id for item in bank}:
            return None
        if not isinstance(content.get("query_vectors"), dict) or not content["query_vectors"]:
            return None
        if any(len(vector) != DIMENSIONS for vector in (*content["question_vectors"].values(), *content["query_vectors"].values())):
            return None
        return content
    except (KeyError, ValueError, TypeError, OSError):
        return None


def create_embedding_client() -> OpenAI:
    key, base_url, _ = embedding_settings()
    if not key:
        raise ValueError("请先设置 DASHSCOPE_API_KEY，再构建向量索引")
    return OpenAI(api_key=key, base_url=base_url, timeout=25, max_retries=1)
