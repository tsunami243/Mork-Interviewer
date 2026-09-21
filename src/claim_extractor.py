import json
import os
from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain_openai import ChatOpenAI


# 加载 .env 文件中的环境变量
load_dotenv()

# 我们将使用这个 llm 实例来驱动所有节点的智能
llm = ChatOpenAI(
    model=os.getenv("LLM_MODEL"),
    api_key=os.getenv("LLM_API_KEY"),
    base_url=os.getenv("LLM_BASE_URL"),
    temperature=0
)

#解析简历以及项目节点
def claim_extractor_node(state: dict) -> dict:
    system_prompt = """
    你是技术项目分析助手。请从材料中提取可验证的技术主张，
    每条必须包含：技术名称、具体实现、原始证据、可追问方向。
    未在材料中出现的信息不得编造。

    #格式输出要求：
    你的每次回复都要遵循以下格式是，包含technology、claim、evidence、follow_up_directions
    [
    {
        "technology": [从用户简历信息中提取到的技术栈],
        "claim": 用户在简历中用技术栈所解决的问题,
        "evidence": [该技术在用户简历中的原句子],
        "follow_up_directions": [由Tech和Advocate分析出来的可针对用户该技术栈发起追问的各个方向]
    }
    ]

    #示例
    [
    {
        "technology": ["FastAPI"],
        "claim": "构建智能饮食推荐 Agent 的后端服务，并承载推荐请求处理。",
        "evidence": [
        "这是一个基于 FastAPI 和大语言模型构建的智能饮食推荐 Agent。"
        ],
        "follow_up_directions": [
        "接口设计",
        "异步并发处理",
        "依赖注入"
        ]
    }
    ]

    #重要提示
    -只输出 JSON 数组，不要输出 Markdown 代码块，不要输出任何解释文字。
    -只提取材料中有明确证据支撑的技术主张。
    材料中有几条就输出几条；没有则返回空数组 []。

    """
    # 从 state 中读取 project_text
    project_text = state["project_text"]

    # 调用 Agent 获得 response
    response = llm.invoke([SystemMessage(content=system_prompt),
                           HumanMessage(content=project_text)])
    # 调用 exchange_to_pyData(response) 得到 claims
    claims = exchange_to_pyData(response.content)
    # 返回 {"claims": claims}
    return {"claims" : claims}


def read_markdown(path:str)->str:
     with open(f"{path}", "r",encoding="utf-8") as file:
        content = file.read()

     return content

def exchange_to_pyData(response : str) ->list[dict[str, str | list[str]]]:
    #防止模型用 ```json ... ``` 包裹结果
     clean_response = response.strip()
     if clean_response.startswith("```json"):
         clean_response = clean_response.removeprefix("```json").removesuffix("```").strip()
     elif clean_response.startswith("```"):
         clean_response = clean_response.removeprefix("```").removesuffix("```").strip()
         # 3. 将 JSON 字符串转换为 Python 列表
     try:
        claims = json.loads(clean_response)
     except json.JSONDecodeError as error:
        print("❌ 模型返回的内容不是合法 JSON：")
        print(clean_response)
        raise error

    # 4. 校验最外层必须是列表
     if not isinstance(claims, list):
        raise ValueError("❌ 技术主张的最外层必须是 JSON 数组 []")

    # 5. 美化输出 JSON，ensure_ascii=False 保证中文不会变成 Unicode 编码
     print("\n✅ 技术主张提取成功，共提取到", len(claims), "条：\n")
     print(json.dumps(claims, ensure_ascii=False, indent=2))
     return claims









