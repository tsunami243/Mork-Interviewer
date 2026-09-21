"""
输入：
state["current_claim"]：当前正在深挖的技术主张 dict
state["current_question"]：Question Planner 生成的问题 str
state["interview_history"]：历史问答列表

输出：
state["current_answer"]：用户本轮回答 str
state["interview_history"]：追加本轮问答后的历史
state["interview_status"]：answer_received
"""
from langgraph.types import interrupt
from typing import Any


def interviewer_node(state:dict[str, Any]) -> dict:
    #校验当前current——claims是否为dict
    current_claim = state.get("current_claim")
    if current_claim is None:
            raise ValueError("缺少上游节点输出：state['current_claim']")
    if not isinstance(current_claim,dict):
        raise ValueError("state['current_claim']必须是dict")
    

    #校验current_question是否是非空字符串 
    current_question = state.get("current_question")
    if not isinstance(current_question,str) or not current_question.strip():
        raise ValueError("current_question必须是非空字符串")

    technology = current_claim.get("technology")

    #调用interrupt，把问题抛给前端
    
    result = interrupt(
        {
         "question": f"{current_question}",
         "technology": technology,
         "instruction": "请结合你的真实项目实现回答，不确定时可以说明。"
         })
    #校验收到的结果是否为非空字符串
    if not isinstance(result,str):
        raise ValueError("当前收到的返回结果非字符串，不合法")
    if not result.strip():
        raise ValueError("当前收到的请求为空")

    #去除首为空格
    result = result.strip()

    interview_history = state.get("interview_history", [])
    if not isinstance(interview_history, list):
        raise ValueError("state['interview_history'] 必须是 list")
    
    #构造最终返回结果
    
    turn_record = {
        "claim": current_claim,
        "question": current_question,
        "answer": result,
    }

    return {
         "current_answer" : result,
         "interview_history":[turn_record],
         "interview_status": "answer_received"
    }

    
