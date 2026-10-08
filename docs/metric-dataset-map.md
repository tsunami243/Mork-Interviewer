# 指标 ↔ 数据集映射

> 目标：为已定的评测指标找到可用的公开数据集。
> 核验环境说明：本机直连 `huggingface.co` 不可达，全部条目通过镜像
> `https://hf-mirror.com` 的实际 API / README / 数据文件核验，而非凭记忆记录。
> 使用前请设置 `HF_ENDPOINT=https://hf-mirror.com`。

## 一、覆盖总表

| 指标 | 数据集 | 状态 |
| --- | --- | --- |
| `parse_success_rate` | opensporks/resumes, d4rk3r/resumes-raw-pdf | ✅ 有 |
| `truncation_rate` | opensporks/resumes（含超大文件） | ✅ 有 |
| `claim_tech_precision` | TechWolf/skill-extraction-tech, sandeeppanem/resume-json-extraction-5k | ✅ 有 |
| `claim_tech_recall@8` | 同上 | ✅ 有 |
| `evidence_verbatim_rate` | TechWolf/skill-extraction-tech（`span` 列） | ✅ 有 |
| `fabrication_rate` | justinthelaw/profile-qa-synthetic-public-v1（`refusal`） | ✅ 有 |
| `hit@3` / `MRR` | TechWolf/Skill-extraction-SkillSkape-graded | ✅ 有 |
| `off_topic_recall/precision` | hfl/cmrc2018, hfl/drcd | ✅ 有 |
| `fabrication_credit_rate` | Laurie/faithfulness-qa-dataset, shunk031/HaluEval | ✅ 有 |
| `strong_on_shallow_rate` | hfl/cmrc2018 challenge set | ✅ 有 |
| `missing_points_hit` | shunk031/HaluEval（`hallucination_spans`） | ✅ 有 |
| `rating_kappa` | 需人工标注（无公开集） | ❌ 自建 |
| `injection_success_rate` | 无公开集 | ❌ 自建对抗样本 |
| `single_question_pass` / `length_pass` | 无需外部数据（`_acceptable_question` 确定性判定） | ➖ 不需要 |
| `repeat_rate` | 无需外部数据（`question_finalizer_node` 去重逻辑） | ➖ 不需要 |
| `budget_compliance` | 无需外部数据（`tool_calls <= 3` 硬断言） | ➖ 不需要 |
| `degrade_visibility` | 无需外部数据（4 个状态枚举） | ➖ 不需要 |
| `unapproved_leak_rate` | 需自造知识库夹具（`wiki_pages.status`） | ❌ 自建 |
| `latency_p50/p95` / `tokens_per_interview` | 无需外部数据（`stage_metrics` 埋点） | ➖ 不需要 |

## 二、逐数据集详情

### 1. TechWolf/skill-extraction-tech
- **许可**：CC-BY-4.0 ｜ **语言**：英文 ｜ **规模**：n<1K
- **字段**（已读 `test.csv` 真实数据行）：`sentence, span, sub_span, label`
- **用途**：本清单最核心的数据集。
  - `span` → `evidence` 金标准，直接算 `evidence_verbatim_rate`（纯字符串逐字比对，无需模型）
  - `label` / `sub_span` → `technology` 金标准，算 precision 与 recall@8
  - `LABEL NOT PRESENT` → 「该 span 不属于任何技能」的负例，直接算 `fabrication_rate`
- **注意**：`label` 为 ESCO 技能名，与本项目技术栈词表不同名，比对前需归一化或人工映射。
- **同族**：`skill-extraction-house`、`skill-extraction-techwolf`

### 2. justinthelaw/profile-qa-synthetic-public-v1
- **许可**：MIT ｜ **语言**：英文 ｜ **规模**：n<1K（test 91 / train 468 / validation 70）
- **字段**：`id, split, task, question, answer, evidence, expected_terms, requires_refusal, history, source_profile_version`
- **用途**：补上此前无数据源的「编造率」。
  - `task == refusal`（167 条）+ `requires_refusal` → 材料未提供该事实时必须拒答，答了即 `fabrication_rate` 命中
  - `evidence`（`section_id`/`fact_id` 引用）→ 校验证据是否真指向原文
  - `expected_terms` → 确定性打分，不需要 LLM 裁判
- **注意**：合成数据，源自小型公开 profile 事实集；README 明确其适用面是「聚焦的 profile-QA 行为，不是广泛指令微调」。适合做回归测试，不宜当广泛质量基准。

### 3. Laurie/faithfulness-qa-dataset
- **许可**：MIT ｜ **语言**：英文 ｜ **规模**：99,094（squad 49,094 / triviaqa 50,000）
- **字段**：`id, question, original_context, modified_context, original_answer, faithful_answer, original_entity, replacement_entity, entity_type, source`
- **用途**：反事实实体替换（把答案实体替换为同类型实体）→ 测 `fabrication_credit_rate`
  - `modified_context` 当上传材料；答 `faithful_answer` 应判 `strong`，答 `original_answer`（凭记忆/编造）应判 `off_topic`
- **注意**：`test` split 仅 4,910 条级别，抽样即可，不需要全量。

### 4. shunk031/HaluEval
- **许可**：见仓库 card ｜ **语言**：英文 ｜ **规模**：qa 10,000 / dialogue 10,000 / general 4,507 / summarization 10,000
- **字段**（已读 config schema）：
  - `qa`：`knowledge, question, right_answer, hallucinated_answer`
  - `dialogue`：`knowledge, dialogue_history, right_response, hallucinated_response`
  - `general`：`ID, user_query, chatgpt_response, hallucination(0/1), hallucination_spans`
- **用途**：`right` vs `hallucinated` 成对结构 = 天然的 `strong` vs `off_topic` 二元测试集；
  `hallucination_spans` 用于校验 `missing_points` 是否指向正确片段（`missing_points_hit`）。

### 5. opensporks/resumes
- **许可**：CC0-1.0 ｜ **规模**：2400+ 简历
- **形态**：`Resume/Resume.csv`（文本）+ `data/data/<职业>/<id>.pdf`（真实 PDF 原件）
- **用途**：`parse_success_rate`（PDF 解析）、`truncation_rate`（含超大文件），按职业目录分层抽样。
- **注意**：源自 Kaggle `snehaanbhawal/resume-dataset`，简历多为非技术岗。

### 6. d4rk3r/resumes-raw-pdf
- **许可**：MIT ｜ **规模**：近千份
- **形态**：`it-domain/*.pdf` 与 `all-domains/*.pdf`，纯 PDF 无标注
- **用途**：解析鲁棒性压测（版式差异大）。`it-domain/` 子集与项目目标用户最接近。

### 7. sandeeppanem/resume-json-extraction-5k
- **许可**：Apache-2.0 ｜ **语言**：英文 ｜ **规模**：4,879（单个 parquet）
- **字段**：简历文本 + 结构化 JSON（当前/历任职位、公司、年限、资历、主领域、核心与次要技能）
- **用途**：`claim_tech_precision` / `recall@8` 的第二个金标准来源
- **注意**：标注为 `dataset_type: synthetic`，且「不逐字分发原始专有简历文档」。

### 8. TechWolf/Skill-extraction-SkillSkape-graded
- **许可**：CC-BY-4.0 ｜ **语言**：英文
- **形态**：BEIR 布局（`queries`：validation 100 / test 500；`corpus`）
- **用途**：`hit@3` / `MRR`。分级相关性标注可直接喂 MTEB 式检索评估器，无需自造指标。

### 9. hfl/cmrc2018
- **许可**：CC-BY-SA-4.0 ｜ **语言**：中文 ｜ **规模**：train 10,142 / validation 3,219 / test 1,002
- **字段**：`id, context, question, answers[{text, answer_start}]`
- **用途**：中文材料 + `answer_start` 定位证据；自带 challenge set（跨句推理）用于 `strong_on_shallow_rate`。
- **同族**：`hfl/drcd`（繁体中文）做中文泛化对照。

## 三、明确的数据缺口

1. **中文幻觉/忠实度数据集为空**。镜像检索 `chinese hallucination` 仅返回 1 个零下载、无 schema 的仓库（`Elvis0910607/TWM_Chinese_hallucination`），不可用。中文侧只能靠 `hfl/cmrc2018` / `hfl/drcd` 自造负例。
2. **`rating_kappa` 无公开金标准**。需要人工标注 30–50 条本项目真实输出。可先用 faithfulness-qa/HaluEval 的程序化标签做弱金标准跑通流程。
3. **`injection_success_rate` 无公开集**。需自建对抗样本：在材料中内嵌指令，断言出题/评分行为不变。
4. **`unapproved_leak_rate` 无公开集**。需自造知识库夹具（含 `pending_review` 页面），断言检索不返回未审核内容。

## 四、已下载并跑通的测试

本机 shell 无 TLS 出网能力（`curl`/`urllib` 对 hf-mirror 一律 `SSL: UNEXPECTED_EOF`），
因此**没有使用 `datasets.load_dataset()`**，而是通过可用通道取回原始文件后离线加载。
脚本位于工作区根目录，**不导入也不修改本项目任何代码**。

| 脚本 | 数据集 | 状态 |
| --- | --- | --- |
| `run_dataset_eval.py` | TechWolf/skill-extraction-tech | ✅ 已跑通 |
| `run_profile_qa_eval.py` | justinthelaw/profile-qa-synthetic-public-v1 | ✅ 已跑通 |

### 结果 1：skill-extraction-tech（704 行，268 条独立句子）

| 指标 | 实测值 |
| --- | --- |
| `evidence_verbatim_rate` | **1.0**（704/704） |
| `labeled_span_rate` | 0.6094 |
| `LABEL NOT PRESENT` | 223 条（31.7%） |
| `UNDERSPECIFIED` | 52 条（7.4%） |

**两个结论，第二个比第一个重要：**

1. `span` 恒为 `sentence` 的子串，是数据集构造方式决定的。
   所以 `evidence_verbatim_rate = 1.0` 只证明**测量方式可行**，
   不证明任何被测系统做得好——它没有任何区分度，不能作为能力证据。
2. **39.06% 的片段拿不到真正对应的技能标签**（223 + 52）。
   这意味着一个"给每个抽到的 span 都硬塞一个技术名"的实现，
   在这份数据上必然产生约 39% 的假阳性。
   `claim_tech_precision` 必须同时报告这个基数，否则 0.61 会被误读为"精度差"。

### 结果 2：profile-qa-synthetic-public-v1（91 条 test）

完整性校验：91 行、91 个唯一 id、必填字段齐全、`split` 全为 `test` —— **转写无丢失**。

| 指标 | 实测值 |
| --- | --- |
| `refusal_evidence_leak_rate` | **0.0**（0/30） |
| `ungrounded_answer_rate` | **0.0**（0/61） |
| `task` 分布 | single_turn 33 / refusal 30 / education 8 / multi_hop 7 / recommendations 5 / multi_turn 4 / chronology 4 |
| 带多轮 `history` | 4 条 |
| `evidence` ≥ 2 条 | 16 条 |

同样地，两个 0.0 是**数据集的自我一致性**，不是被测系统的成绩。
它的真正价值在 `refusal` 那 30 条：原文 README 记录了 baseline 模型的
`Refusal Accuracy` 仅 **0.40**，说明"材料没给就必须拒答"这个任务有真实区分度。
把候选人的作答替换进去，就能直接测出你的 Evaluator 会不会给编造内容记分
（即 `fabrication_credit_rate`）。

### 通道限制（影响可复现性）

- 单次抓取上限约 50KB 显示 / 100KB 落盘，**大于该体积的数据集无法完整获取**。
  `skill-extraction-tech/test.csv` 实为 170KB，故只拿到前 704 行（约 59%）。
- `datasets-server`（`/rows`、`/parquet`）**不经镜像**，返回 404，无法分页。
- 结论：**必须依赖 CSV/JSONL 这类文本格式**，且优先选 < 100KB 的文件；
  parquet 格式在本机完全不可用。

## 五、已排除的数据集

| 数据集 | 排除原因 |
| --- | --- |
| `Usay/interview-male-new` 及同族 | 实为**韩国 AI Hub 韩语面试录音数据集**。已拉取真实文件核验：内容为「你最近在读什么书」类软技能面谈，附 `.wav` 音频路径，`usedStorage: 0`。**不含任何技术项目深挖内容**，对本项目无测试价值。 |
| `Davichick/InterviewForge_GenDS` | Mistral 本地生成的合成面试 QA。仅可用于 `single_question_pass` / `length_pass` 这类表层格式检查，**不可作为出题质量金标准**。 |
| `Elvis0910607/TWM_Chinese_hallucination` | 零下载、无 dataset card、无 schema 声明，无法构成可用基准。 |
