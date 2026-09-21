"""手动冒烟测试：真实调用一次 Claim Extractor 节点。"""

from pathlib import Path
import sys


# 让测试文件可以导入 src 目录中的 claim_extractor.py
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from claim_extractor import claim_extractor_node, read_markdown


REQUIRED_FIELDS = {
    "technology",
    "claim",
    "evidence",
    "follow_up_directions",
}


def run_smoke_test() -> None:
    # 1. 读取测试项目材料
    sample_path = PROJECT_ROOT / "data" / "sample_project.md"
    project_text = read_markdown(sample_path)

    # 2. 调用 LangGraph 节点函数
    result = claim_extractor_node({
        "project_text": project_text
    })

    # 3. 校验节点返回结构
    assert isinstance(result, dict), "节点返回值必须是 dict"
    assert "claims" in result, "节点返回值必须包含 claims"
    assert isinstance(result["claims"], list), "claims 必须是 list"
    assert result["claims"], "样本材料至少应提取出一条技术主张"

    # 4. 校验每条技术主张的字段
    for index, claim in enumerate(result["claims"], start=1):
        assert isinstance(claim, dict), f"第 {index} 条技术主张必须是 dict"

        missing_fields = REQUIRED_FIELDS - claim.keys()
        assert not missing_fields, (
            f"第 {index} 条技术主张缺少字段：{missing_fields}"
        )

    # 5. 输出测试结果
    print(f"✅ 冒烟测试通过：成功提取 {len(result['claims'])} 条技术主张")
    print(f"✅ 第一条技术主张：{result['claims'][0]['technology']}")


if __name__ == "__main__":
    run_smoke_test()