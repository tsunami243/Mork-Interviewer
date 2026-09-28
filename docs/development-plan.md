# 开发计划与进度记录

> 最后更新：2026-09-25
>
> 项目目标：构建一个依据简历与项目材料进行多轮项目深挖、回答评估和学习建议生成的模拟面试 Agent。

## 当前进度评估

| 维度 | 当前完成度 | 说明 |
| --- | ---: | --- |
| LangGraph 核心工作流 | 约 85% | 已具备分层选题、A-RAG 参考题检索、人工中断、多轮路由、评估和报告生成。 |
| 可演示产品整体 | 约 75% | 浏览器面试闭环和题库已可用；百炼真实索引、跨重启持久化和部署仍需验收。 |

## 已完成

### 核心 Agent 节点

- [x] **Claim Extractor**：从 Markdown 项目材料中提取带证据的技术主张 `claims`。
- [x] **Question Planner**：首轮选择值得深挖的技术主张；评估后根据缺失点生成追问。
- [x] **Interviewer**：使用 LangGraph `interrupt()` 暂停，接收 `Command(resume=...)` 恢复的用户回答。
- [x] **Answer Evaluator**：根据当前主张、问题、材料证据和回答输出 `rating`、缺失点、反馈与是否继续追问。
- [x] **Interview Router**：依据 `should_continue`、`turn_count` 与 `max_turns` 路由到下一轮出题或报告生成。
- [x] **Report Generator**：根据 `evaluation_history` 生成结构化面试总结、优点、薄弱点和下一轮练习重点。

### 工作流与状态

- [x] 使用 `InterviewState` 定义共享状态。
- [x] 使用 `InMemorySaver + thread_id` 支持同一面试会话的暂停和恢复。
- [x] 使用 `interview_history` 保存每轮 `claim + question + answer`。
- [x] 使用 `evaluation_history` 保存每轮问答及对应评估结果。
- [x] 实现 `Evaluator → Router → Planner / Report Generator` 的条件路由。

### 工程基础

- [x] README、架构图、样本项目材料。
- [x] `.gitignore` 忽略 `.env`、密钥、虚拟环境和本地私有材料。
- [x] GitHub 独立仓库已建立并推送。
- [x] Claim Extractor、Answer Evaluator 的真实 API 冒烟测试及 Router 冒烟测试。
- [x] 离线单元测试覆盖 Router 决策、JSON 对象解析、Evaluator 契约与 Report Generator 的输入输出校验。
- [x] 固定响应的 LangGraph 多轮回归测试：两轮 interrupt/resume、历史追加、轮数上限、提前结束与最终报告；不依赖真实 API。
- [x] Document Loader 支持 Markdown、PDF、DOCX 和 Windows Word 读取 DOC；离线校验解析与输入边界。真实 DOC 需在桌面 Word 上人工验证。
- [x] 已手动验证多轮状态流转和报告生成闭环。
- [x] Streamlit 单页 MVP：双文件上传、独立会话、暂停恢复、逐轮反馈与报告展示；离线页面/适配层测试通过。
- [x] 原创题库 45 道、按主题/阶段拆分的出题节点、三种检索工具与最多三次工具调用的检索 Agent。
- [x] 百炼兼容接口的离线索引构建脚本、受控技术标签及索引版本失效检查；当前本地尚未配置百炼 Key，真实语义检索待用户构建索引后验证。
- [x] 阶段耗时/模型用量统计、逐阶段进度提示；官方 DeepSeek 服务可配置关闭思考模式。

## 尚未完成的节点与组件

### P0：下一个开发阶段

- [x] **Document Loader / Material Parser（首版）**
  - 支持本地路径和上传字节输入的 Markdown、PDF、DOC、DOCX，并输出带来源标签的 `project_text`。
  - 终端入口与 Streamlit 上传页面均已接入 Loader。
  - PDF 暂不支持 OCR；DOC 的真实 Word 自动化待桌面人工核验。

- [ ] **用户材料证据检索（独立支线）**
  - 已有证据块切分实验，但尚未接入 Evaluator；不得将题库检索误称为用户材料证据检索。
  - 项目内参考题库的 A-RAG 已接入主线出题，仅用于提问，不参与评分。

- [x] **Report Generator 与多轮工作流自动化测试**
  - Router 断言已匹配 `report_generator` 路由标签。
  - 已加入报告节点字段校验和固定两轮到报告的离线回归测试。
  - 使用 Mock LLM 的固定响应，不需要密钥或网络；真实 API 冒烟测试仍需手动执行。

### P1：面向用户的 MVP

- [x] **Streamlit 页面（本机版）**
  - 上传简历和项目材料，展示每轮问题、回答、评估及最终报告。
  - 每个浏览器会话拥有独立的 Graph、`thread_id` 和 `InMemorySaver`。
  - 已通过离线逻辑和页面冒烟测试；仍需由用户用真实材料走一轮人工体验验收。

- [ ] **会话持久化**
  - 开发期继续使用 `InMemorySaver`。
  - 生产期替换为 Redis 或数据库 checkpointer，避免服务重启后丢失会话。
  - 明确 Redis 会话元数据与 LangGraph 工作流状态的唯一数据源边界。

### P2：工程化与简历加分项

- [ ] 结构化日志：记录节点耗时、路由结果、模型调用失败和解析错误。
- [ ] 评估可观测性：统计每次面试轮数、答非所问比例、常见薄弱技术点。
- [ ] Docker Compose：一键启动应用及可选 Redis/向量数据库。
- [ ] README 补充运行方式、演示截图、测试说明和技术取舍。
- [ ] GitHub Actions：基础语法检查与测试自动执行。

## 下一步执行顺序

1. 已完成：Router、Report Generator、完整多轮到报告闭环的离线回归测试。
2. 使用百炼 Key 离线构建题库索引，人工验证三工具齐备时的面试质量与耗时。
3. 用真实材料完成浏览器验收，人工验证 DOC；公开演示只用脱敏样本。
4. 单独评估用户材料的证据检索、持久化会话与部署，并明确材料保存期限。

## 下一阶段验收标准

用户可以在浏览器中完成以下闭环：

```text
上传一份简历和一个项目材料
→ 开始模拟面试
→ 完成至少两轮问答与追问
→ 查看带证据依据的最终报告
→ 在同一浏览器会话内重新运行页面仍能看到本轮面试状态或历史
```
