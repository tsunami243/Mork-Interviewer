"""只用仓库公开样本测速；必须显式 --live 才会调用模型。"""

import argparse
from pathlib import Path
import sys
from time import perf_counter
from uuid import uuid4

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
load_dotenv(ROOT / "src" / ".env")

from interview_graph import create_interview_graph
from langgraph.types import Command


def main() -> None:
    parser = argparse.ArgumentParser(description="公开样本的首题/追问耗时和阶段模型用量")
    parser.add_argument("--live", action="store_true", help="显式允许调用已配置的模型接口")
    args = parser.parse_args()
    if not args.live:
        parser.error("需要显式传入 --live；此命令会调用已配置的模型接口并产生费用")

    sample = (ROOT / "data" / "sample_project.md").read_text(encoding="utf-8")
    graph = create_interview_graph()
    config = {"configurable": {"thread_id": f"public-benchmark-{uuid4().hex}"}}
    started = perf_counter()
    first = graph.invoke({"project_text": sample, "turn_count": 0, "max_turns": 2, "interview_history": [], "evaluation_history": []}, config=config)
    first_seconds = perf_counter() - started
    if not first.get("__interrupt__"):
        raise RuntimeError("公开样本没有产生首题")
    started = perf_counter()
    second = graph.invoke(Command(resume="我负责将饮食推荐拆成意图提取、检索、校验与输出组装，重点避免忌口被模型忽略。"), config=config)
    follow_seconds = perf_counter() - started
    state = graph.get_state(config).values

    print(f"首题：{first_seconds:.2f} 秒；后续：{follow_seconds:.2f} 秒；阶段状态：{state.get('retrieval_status')}")
    print(f"是否得到追问：{bool(second.get('__interrupt__'))}")
    for row in state.get("stage_metrics", []):
        print(f"{row['stage']}: {row['elapsed_ms']} ms / {row['model_calls']} 次模型调用 / 输入 {row['input_tokens']} tokens / 输出 {row['output_tokens']} tokens")
    if first_seconds > 25 or follow_seconds > 15:
        print("本次样本未达到首题 25 秒、追问 15 秒的软目标；请检查阶段耗时。")


if __name__ == "__main__":
    main()
