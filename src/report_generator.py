"""根据整场评估历史生成结构化模拟面试报告。"""

import json
import os
from typing import Any

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from json_utils import parse_json_object
from telemetry import invoke_model


load_dotenv()

llm = ChatOpenAI(
    model=os.getenv("LLM_MODEL"),
    api_key=os.getenv("LLM_API_KEY"),
    base_url=os.getenv("LLM_BASE_URL"),
    temperature=0,
)

REQUIRED_REPORT_FIELDS = {
    "overall_summary",
    "strengths",
    "weaknesses",
    "next_practice_focus",
}


def validate_interview_report(report: dict[str, Any]) -> None:
    """校验报告能以结构化字段安全地交给前端展示。"""
    missing_fields = REQUIRED_REPORT_FIELDS - report.keys()
    if missing_fields:
        raise ValueError(f"面试报告缺少字段：{sorted(missing_fields)}")

    if not isinstance(report["overall_summary"], str) or not report["overall_summary"].strip():
        raise ValueError("overall_summary 必须是非空字符串")

    for field in ("strengths", "next_practice_focus"):
        value = report[field]
        if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
            raise ValueError(f"{field} 必须是字符串列表")

    weaknesses = report["weaknesses"]
    if not isinstance(weaknesses, list):
        raise ValueError("weaknesses 必须是 list")

    for weakness in weaknesses:
        if not isinstance(weakness, dict):
            raise ValueError("weaknesses 中每一项必须是 dict")
        for field in ("topic", "reason", "study_action"):
            value = weakness.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"weaknesses 中的 {field} 必须是非空字符串")


def report_generator_node(state: dict[str, Any]) -> dict[str, Any]:
    """读取评估历史，生成本次面试的最终报告。"""
    evaluation_history = state.get("evaluation_history")
    turn_count = state.get("turn_count")

    if not isinstance(evaluation_history, list) or not evaluation_history:
        raise ValueError("state['evaluation_history'] 必须是非空 list")

    if not all(isinstance(record, dict) for record in evaluation_history):
        raise ValueError("evaluation_history 中每一项必须是 dict")

    if not isinstance(turn_count, int) or turn_count < 1:
        raise ValueError("state['turn_count'] 必须是大于 0 的整数")

    report_context = {
        "turn_count": turn_count,
        "evaluation_history": evaluation_history,
    }
    context_text = json.dumps(report_context, ensure_ascii=False, indent=2)

    system_prompt = """
你是一名资深技术面试结果报告编写者。请仅根据 evaluation_history 中的真实问答与评估结果生成报告。

只输出一个合法 JSON 对象，不要输出 Markdown 代码块或任何解释文字。
输出字段必须且只能包含：
{
  "overall_summary": "本次面试的整体总结",
  "strengths": ["已展现出的能力"],
  "weaknesses": [
    {
      "topic": "薄弱技术点",
      "reason": "关联的真实回答或评估结论",
      "study_action": "具体复习动作"
    }
  ],
  "next_practice_focus": ["下一轮练习重点"]
}

规则：
- 只能依据 evaluation_history 总结，不得把材料中没有的实现当成候选人已完成的能力。
- 不要给泛泛的八股建议；每个薄弱点必须关联真实问答或评估结果。
- strengths、weaknesses、next_practice_focus 可以是空列表，但字段必须存在。
"""

    response = invoke_model(llm,
        [
            SystemMessage(content=system_prompt),
            HumanMessage(content=f"<evaluation_history>\n{context_text}\n</evaluation_history>"),
        ]
    )
    report = parse_json_object(response.content)
    validate_interview_report(report)

    return {
        "interview_report": report,
        "interview_status": "completed",
    }
