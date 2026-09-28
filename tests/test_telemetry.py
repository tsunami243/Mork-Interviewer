"""只记录阶段统计，官方 DeepSeek 可关闭思考且不暴露用户文本。"""

import os
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from langchain_openai import ChatOpenAI
from telemetry import invoke_model, measured_node


class TelemetryTests(unittest.TestCase):
    def test_official_deepseek_disables_thinking_and_counts_tokens(self):
        model = ChatOpenAI(model="deepseek-flash", api_key="offline-test-key", base_url="https://api.deepseek.com")
        reply = SimpleNamespace(content="可用", usage_metadata={"input_tokens": 12, "output_tokens": 7})
        with (
            patch.dict(os.environ, {"LLM_BASE_URL": "https://api.deepseek.com", "LLM_THINKING_MODE": "disabled"}),
            patch.object(ChatOpenAI, "invoke", return_value=reply) as invoke,
        ):
            result = measured_node("fake_node", lambda _: {"result": invoke_model(model, ["公开样本"]).content})({})
        self.assertEqual(result["result"], "可用")
        self.assertEqual(result["stage_metrics"][0]["model_calls"], 1)
        self.assertEqual(result["stage_metrics"][0]["input_tokens"], 12)
        self.assertEqual(result["stage_metrics"][0]["output_tokens"], 7)
        self.assertEqual(invoke.call_args.kwargs["extra_body"], {"thinking": {"type": "disabled"}})

    def test_other_provider_is_not_modified(self):
        model = ChatOpenAI(model="other", api_key="offline-test-key", base_url="https://example.com/v1")
        with patch.dict(os.environ, {"LLM_BASE_URL": "https://example.com/v1"}), patch.object(ChatOpenAI, "invoke", return_value=SimpleNamespace(content="ok")) as invoke:
            invoke_model(model, ["公开样本"])
        self.assertNotIn("extra_body", invoke.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
