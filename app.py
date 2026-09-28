"""项目经历深挖与模拟面试的 Streamlit 单页入口。"""

import os
from pathlib import Path
import sys
from time import perf_counter

from dotenv import load_dotenv
import streamlit as st


PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
load_dotenv(PROJECT_ROOT / "src" / ".env")

from document_loader import DocumentLoadError, load_uploaded_document


PHASE_LABELS = {
    "claim_extractor": "提取项目核心主张",
    "topic_selector": "选择本轮面试主题",
    "question_retrieval": "检索真实面试参考题",
    "question_finalizer": "整理为一道自然的问题",
    "interviewer": "等待你的回答",
    "answer_evaluator": "对照项目材料评估回答",
    "report_generator": "汇总本场面试报告",
}


def update_status(panel, event: dict) -> None:
    label = PHASE_LABELS.get(event.get("stage"), "处理面试阶段")
    if event.get("status") == "started":
        panel.update(label=label, state="running")
    elif event.get("status") == "finished":
        panel.write(f"{label} · {event.get('elapsed_ms', 0) / 1000:.1f} 秒")
    elif event.get("status") == "failed":
        panel.update(label=f"{label}失败", state="error")


st.set_page_config(
    page_title="项目深挖 | 模拟面试",
    page_icon="📎",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    .stApp { background: #f5f8fa; color: #173040; }
    [data-testid="stSidebar"] { background: #eaf1f3; border-right: 1px solid #d5e0e5; }
    .block-container { max-width: 1080px; padding-top: 2.8rem; }
    h1, h2, h3 { color: #173040; letter-spacing: -.025em; font-family: "Segoe UI", "Microsoft YaHei", sans-serif; }
    .hero-title { font-size: clamp(2.2rem, 5vw, 3.6rem); font-weight: 650; line-height: 1.19; letter-spacing: -.045em; margin: .3rem 0 .8rem; }
    .hero-description { max-width: 620px; color: #546774; font-size: 1.03rem; line-height: 1.8; }
    .phase-line { border-bottom: 1px solid #d5e0e5; padding: .85rem 0; color: #4d6570; }
    .question-panel { border-left: 4px solid #107a70; padding: .6rem 0 .7rem 1.25rem; margin: 1.4rem 0; }
    .question-panel .question-label { color: #107a70; font-weight: 600; margin-bottom: .6rem; }
    [data-testid="stSidebar"] h2 { margin-top: .8rem; }
    .stButton button[kind="primary"], .stFormSubmitButton button[kind="primary"] { background: #107a70; border-color: #107a70; }
    .stButton button:focus-visible, .stFormSubmitButton button:focus-visible { outline: 3px solid #b27c36; outline-offset: 2px; }
    @media (max-width: 700px) { .block-container { padding-top: 1.2rem; } }
    </style>
    """,
    unsafe_allow_html=True,
)


def reset_interview() -> None:
    st.session_state.pop("interview_run", None)
    st.session_state["upload_nonce"] = st.session_state.get("upload_nonce", 0) + 1


def render_history(state: dict) -> None:
    history = state.get("interview_history", [])
    evaluations = state.get("evaluation_history", [])
    if not history:
        return

    st.subheader("面试记录")
    for index, turn in enumerate(history):
        st.caption(f"第 {index + 1} 轮")
        with st.chat_message("assistant", avatar="🧭"):
            st.write(turn["question"])
        with st.chat_message("user", avatar="👤"):
            st.write(turn["answer"])

        if index < len(evaluations):
            evaluation = evaluations[index]["evaluation"]
            with st.expander(f"查看第 {index + 1} 轮反馈 · {evaluation['rating']}"):
                st.write(evaluation["feedback"])
                if evaluation["strengths"]:
                    st.markdown("**回答到的点**")
                    for point in evaluation["strengths"]:
                        st.write(f"• {point}")
                if evaluation["missing_points"]:
                    st.markdown("**可以补充的点**")
                    for point in evaluation["missing_points"]:
                        st.write(f"• {point}")


def render_report(report: dict) -> None:
    st.success("本次面试已完成")
    st.header("面试报告")
    st.write(report["overall_summary"])

    left, right = st.columns(2, gap="large")
    with left:
        st.subheader("表现较好的地方")
        for item in report["strengths"]:
            st.write(f"• {item}")
        if not report["strengths"]:
            st.caption("本次没有足够的回答依据来确认优势。")

    with right:
        st.subheader("下一轮练习重点")
        for item in report["next_practice_focus"]:
            st.write(f"• {item}")

    st.subheader("需要补强")
    for weakness in report["weaknesses"]:
        with st.expander(weakness["topic"], expanded=True):
            st.write(weakness["reason"])
            st.caption(f"建议动作：{weakness['study_action']}")
    if not report["weaknesses"]:
        st.caption("本次评估未发现明确的薄弱点。")


def main() -> None:
    st.markdown('<div class="hero-title">把项目经历，讲到能经得起追问。</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="hero-description">上传简历和项目材料，围绕你真正写过的技术经历进行多轮模拟面试。'
        '每轮问题、回答与反馈都会留在当前会话中。</div>',
        unsafe_allow_html=True,
    )

    run = st.session_state.get("interview_run")
    with st.sidebar:
        st.header("面试工作台")
        if run is None:
            st.write("准备两份材料，开始一次项目深挖。")
            nonce = st.session_state.get("upload_nonce", 0)
            resume_file = st.file_uploader(
                "简历", type=["pdf", "md", "doc", "docx"],
                max_upload_size=8, key=f"resume_{nonce}",
            )
            project_file = st.file_uploader(
                "项目材料", type=["pdf", "md", "doc", "docx"],
                max_upload_size=8, key=f"project_{nonce}",
            )
            st.caption("材料不会写入项目目录；提取出的文字会发送至你配置的模型服务。")
            max_turns = st.select_slider("最多提问轮数", options=[1, 2, 3, 5, 8], value=3)
            start_clicked = st.button("开始模拟面试", type="primary", use_container_width=True)

            if start_clicked:
                if resume_file is None or project_file is None:
                    st.error("请同时上传简历和项目材料。")
                elif not all(os.getenv(name) for name in ("LLM_MODEL", "LLM_API_KEY", "LLM_BASE_URL")):
                    st.error("请先在 src/.env 配置 LLM_MODEL、LLM_API_KEY 和 LLM_BASE_URL。")
                else:
                    # 需要密钥时才导入 Graph，避免未配置环境时无法显示页面。
                    from interview_session import start_interview

                    try:
                        with st.status("读取材料并准备第一轮面试", expanded=True) as progress:
                            started = perf_counter()
                            documents = [
                                load_uploaded_document(upload.name, upload.getvalue())
                                for upload in (resume_file, project_file)
                            ]
                            document_ms = round((perf_counter() - started) * 1000)
                            progress.write(f"文档读取 · {document_ms / 1000:.1f} 秒")
                            st.session_state["interview_run"] = start_interview(
                                documents, max_turns,
                                document_ms=document_ms,
                                on_progress=lambda event: update_status(progress, event),
                            )
                            progress.update(label="首轮问题已准备", state="complete")
                        st.rerun()
                    except DocumentLoadError as exc:
                        st.error(str(exc))
                    except Exception:
                        st.error("面试启动失败。请检查模型配置、API 可用性及材料内容后重试。")
        else:
            state = run.state
            st.metric("已完成轮数", f"{state.get('turn_count', 0)} / {state.get('max_turns', 3)}")
            metrics = state.get("stage_metrics", [])
            if metrics:
                elapsed = run.document_ms + sum(item.get("elapsed_ms", 0) for item in metrics)
                calls = sum(item.get("model_calls", 0) for item in metrics)
                st.caption(f"阶段累计：{elapsed / 1000:.1f} 秒 · 模型调用 {calls} 次")
            st.caption("当前使用进程内记忆；服务器重启后本场面试无法恢复。")
            if st.button("重新开始", use_container_width=True):
                reset_interview()
                st.rerun()

    if run is None:
        st.markdown('<div class="phase-line">上传材料 → 回答项目追问 → 查看评估报告</div>', unsafe_allow_html=True)
        st.info("在左侧上传简历和项目材料后，开始第一轮面试。")
        return

    state = run.state
    render_history(state)

    question = run.question
    if question is not None:
        retrieval_status = state.get("retrieval_status")
        if retrieval_status == "embedding_unavailable":
            st.info("题库语义索引尚未构建，本轮已用关键词和参考题降级出题；完整 A-RAG 需先离线构建索引。")
        elif retrieval_status and retrieval_status != "ok":
            st.info("本轮检索未能完整执行，已使用现有参考题降级出题；检查模型服务或题库配置后可重试。")
        current_turn = state.get("turn_count", 0) + 1
        st.markdown(
            f'<div class="question-panel"><div class="question-label">第 {current_turn} 轮 · 当前问题</div></div>',
            unsafe_allow_html=True,
        )
        st.write(question["question"])
        if question.get("technology"):
            st.caption("技术方向：" + "、".join(question["technology"]))

        with st.form(key=f"answer_form_{current_turn}", clear_on_submit=True):
            answer = st.text_area("你的回答", max_chars=3000, placeholder="结合你实际做过的实现讲；不确定的部分可以直接说明。")
            submitted = st.form_submit_button("提交回答", type="primary", use_container_width=True)
        if submitted:
            try:
                with st.status("评估回答并准备下一步", expanded=True) as progress:
                    run.resume(answer, on_progress=lambda event: update_status(progress, event))
                    progress.update(label="本轮处理完成", state="complete")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))
            except Exception:
                st.error("处理回答失败。请检查模型服务后重试，本轮面试状态仍保留在当前会话。")
        return

    report = state.get("interview_report")
    if isinstance(report, dict):
        render_report(report)
    elif state.get("interview_status") == "no_claims":
        st.warning("未从材料中提取到可追问的技术主张。请提供包含具体技术实现的项目材料，然后重新开始。")
    else:
        st.error("工作流未生成报告；请检查模型响应或重新开始面试。")


if __name__ == "__main__":
    main()
