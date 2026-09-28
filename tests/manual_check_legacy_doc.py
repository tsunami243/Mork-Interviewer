"""人工验证入口：仅在用户允许启动桌面 Word 后运行此脚本。"""

from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from document_loader import load_document


def main() -> None:
    if len(sys.argv) != 2 or Path(sys.argv[1]).suffix.lower() != ".doc":
        raise SystemExit("用法：python tests/manual_check_legacy_doc.py <真实的旧版.doc路径>")

    document = load_document(sys.argv[1])
    print(f"DOC 读取成功：{len(document.segments)} 段，{sum(len(segment.text) for segment in document.segments)} 字符")
    print("请自行检查提取文字是否完整；本脚本不打印简历正文。")


if __name__ == "__main__":
    main()
