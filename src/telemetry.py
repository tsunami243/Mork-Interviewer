"""只记录阶段耗时与用量，不记录简历、回答或密钥。"""

import logging
import os
from contextvars import ContextVar
from functools import wraps
from time import perf_counter
from typing import Any, Callable
from urllib.parse import urlparse

from langchain_openai import ChatOpenAI


logger = logging.getLogger("mock_interview.performance")
_calls: ContextVar[list[dict[str, int]] | None] = ContextVar("stage_model_calls", default=None)


def invoke_model(llm: Any, messages: Any) -> Any:
    """统一统计 LLM 调用；没有 usage_metadata 的测试替身也能工作。"""
    stage_calls = _calls.get()
    record = {"input_tokens": 0, "output_tokens": 0}
    if stage_calls is not None:
        stage_calls.append(record)
    fast_deepseek = (
        isinstance(llm, ChatOpenAI)
        and urlparse(os.getenv("LLM_BASE_URL", "")).hostname == "api.deepseek.com"
        and os.getenv("LLM_THINKING_MODE", "disabled") == "disabled"
    )
    response = llm.invoke(messages, extra_body={"thinking": {"type": "disabled"}}) if fast_deepseek else llm.invoke(messages)
    if stage_calls is not None:
        metadata = getattr(response, "response_metadata", None) or {}
        usage = getattr(response, "usage_metadata", None) or metadata.get("token_usage") or {}
        record["input_tokens"] = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
        record["output_tokens"] = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
    return response


def measured_node(name: str, node: Callable[[dict], dict]) -> Callable[[dict], dict]:
    """为图节点计时并发布不含用户文本的阶段状态。"""
    @wraps(node)
    def wrapped(state: dict) -> dict:
        try:
            from langgraph.config import get_stream_writer
            writer = get_stream_writer()
        except (ImportError, RuntimeError):
            writer = None

        if writer is not None:
            writer({"stage": name, "status": "started"})
        calls: list[dict[str, int]] = []
        token = _calls.set(calls)
        started = perf_counter()
        try:
            update = node(state)
        except Exception as error:
            if type(error).__name__ == "GraphInterrupt":
                raise
            logger.warning("stage_failed stage=%s elapsed_ms=%d error_type=%s", name, round((perf_counter() - started) * 1000), type(error).__name__)
            if writer is not None:
                writer({"stage": name, "status": "failed"})
            raise
        finally:
            _calls.reset(token)

        metric = {
            "stage": name,
            "elapsed_ms": round((perf_counter() - started) * 1000),
            "model_calls": len(calls),
            "input_tokens": sum(item["input_tokens"] for item in calls),
            "output_tokens": sum(item["output_tokens"] for item in calls),
        }
        logger.info("stage_complete %s", metric)
        if writer is not None:
            writer({"stage": name, "status": "finished", "elapsed_ms": metric["elapsed_ms"]})
        return {**update, "stage_metrics": [metric]}

    return wrapped
