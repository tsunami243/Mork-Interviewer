"""管理员手动运行一次：只向百炼发送公开题库，不发送简历。"""

from pathlib import Path
import sys

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
load_dotenv(ROOT / "src" / ".env")

from embedding_index import build_question_index, create_embedding_client, embedding_settings, index_path, load_question_index
from question_bank import load_question_bank


def main() -> None:
    bank = load_question_bank()
    _, _, model = embedding_settings()
    if load_question_index(bank, model) is not None:
        print(f"题库索引已是最新版本：{index_path(bank, model)}")
        return
    client = create_embedding_client()
    output = build_question_index(bank, client, model)
    print(f"参考题：{len(bank)} 条；索引已创建：{output}")


if __name__ == "__main__":
    main()
