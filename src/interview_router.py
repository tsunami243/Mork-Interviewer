from langgraph.graph import END
def interviewer_router(state:list):
    evaluation_result = state.get("evaluation_result")
    turn_count = state.get("turn_count")
    max_turns = state.get("max_turns")

    #判断
    if turn_count >= max_turns:
        return "end"

    if evaluation_result["should_continue"] :
        return "continue"

    else:
        return "end"
