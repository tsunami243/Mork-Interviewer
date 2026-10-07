"""Shared model construction, independent of any frontend framework."""
import os


def configured_llm(*,max_retries=1):
    from langchain_openai import ChatOpenAI
    if not all(os.getenv(key) for key in ('LLM_MODEL','LLM_API_KEY','LLM_BASE_URL')):
        raise ValueError('模型服务尚未配置，资料仍会保存在本机')
    return ChatOpenAI(model=os.environ['LLM_MODEL'],api_key=os.environ['LLM_API_KEY'],
                      base_url=os.environ['LLM_BASE_URL'],temperature=0,timeout=60,max_retries=max_retries)


def configured_wiki_llm():
    # Wiki runtime records and budgets each explicit request retry.
    return configured_llm(max_retries=0)
