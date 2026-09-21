import json
from typing import Any

def parse_json_object(response_text: str) -> dict[str, Any]:
    """将模型返回的 JSON 对象文本转换为 Python 字典。"""
    clean_response = response_text.strip()

    if clean_response.startswith("```json"):
        clean_response = clean_response.removeprefix("```json")
        clean_response = clean_response.removesuffix("```").strip()
    elif clean_response.startswith("```"):
        clean_response = clean_response.removeprefix("```")
        clean_response = clean_response.removesuffix("```").strip()

    try:
        result = json.loads(clean_response)
    except json.JSONDecodeError as error:
        raise ValueError(
            "返回的内容不是合法 JSON：\n"
            f"{clean_response}"
        ) from error

    if not isinstance(result, dict):
        raise ValueError("的返回值必须是 JSON 对象 {}")

    return result
