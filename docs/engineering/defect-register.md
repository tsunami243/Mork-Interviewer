# 缺陷登记册（Defect Register）

本文档集中记录已确认的缺陷、根因、拟修方案及修复验证结果。缺陷状态只有在实际完成相应代码变更和验证后才更新；“拟修方案”不代表已经实施。

## 状态定义

- **已确认 / 未修复**：复现路径和根因已确认，代码尚未修复。
- **修复中**：已开始实现，但还未通过验证。
- **待验证**：代码改动完成，尚未完成约定的验证。
- **已关闭**：修复和验证结果均已记录。

---

## DEF-2026-001：知识库只读 API 每次请求重复扫描 Markdown 并重建图谱

- **状态**：已关闭（核心重复扫描问题已修复并完成回归验证）
- **严重度**：中（性能与可扩展性；目前文件量较小时主要表现为重复 I/O/YAML 解析和延迟，文件增长后影响加重）
- **发现日期**：2026-10-14
- **引入提交**：`1e0f5da` — `feat: add wiki workbench and rebuild retrieval pipeline`（2026-10-07）
- **影响范围**：工作台启动、文件列表轮询、文件详情、知识图谱及关联卡片

### 现象

用户观察到 `file_listing`、`file_details`、`graph_data` 请求都会重新遍历约 26 个 Markdown 文件，并反复调用 `yaml.safe_load`；文件详情中的关联信息还会触发整张图谱重新计算。

### 调用链与触发流程

1. **工作台启动**：`/api/bootstrap` → `file_listing()`。
2. **运行期间轮询**：前端每约 2.2 秒请求 `/api/library` → `file_listing()`。
3. **文件列表重复扫描**：`file_listing()` 先调用 `source_aliases(root)`，再调用 `catalog(root)`；`source_aliases()` 在没有传入已解析文档时也会自行调用 `catalog(root)`。`catalog()` 对条目调用 `entry()`，`entry()` 调用 `metadata()`，其中执行 `yaml.safe_load`。
4. **图谱请求重复扫描**：`graph_data()` 调用 `documents(root)` 扫描目录并读取 Markdown/解析 frontmatter，随后构造节点时又调用 `catalog(root)`。因此一次图谱请求内部也重复枚举和解析目录元数据。
5. **详情请求放大重算**：`file_details()` 调用 `relation_cards()`；`relation_cards()` 每次重新调用 `graph_data()`。详情流程随后还会调用 `source_aliases(root)`。也就是说，查看一个文件详情可能重建整张图谱并再次扫描来源映射。

对应实现位置：

- `src/wiki_browser.py`：`metadata()`、`entry()`、`catalog()`
- `src/wiki_details.py`：`file_listing()`、`file_details()`
- `src/wiki_relations.py`：`documents()`、`source_aliases()`、`graph_data()`、`relation_cards()`
- `src/api_server.py`：`/api/bootstrap`、`/api/library`、`/api/library/file`、`/api/library/graph`
- `frontend/src/App.tsx`：定时轮询 `/api/library`（约 2.2 秒）

### 根因

不是单纯“相同代码重复写了几份”，而是**扫描/解析函数被复用，但解析结果没有被复用**：

- `catalog(root)`、`source_aliases(root)` 和 `graph_data(root)` 都是每次调用即重新读取的计算函数，没有按知识库版本共享快照或缓存。
- 上层函数为了获得不同视图再次调用这些函数，形成嵌套全量计算。
- 文件列表轮询没有利用目录版本、ETag 或变更修订号短路未变化请求。
- 文件监控和 API 展示读取之间没有共用一个目录快照/失效机制。

### 关于“能复用就复用”的要求

检查代码库和 Git 历史后，相关代码集中在单个提交 `1e0f5da`，没有发现文档中要求“每次重新解析”或禁止缓存的约束。实现确实选择了复用 `catalog()`、`source_aliases()`、`graph_data()` 等函数，但这是**复用计算入口，不是复用计算结果**。如果此前“能复用就复用”的要求影响了设计，它最多解释为何采用共享 helper；它不能解释或要求每次重新读文件与 `yaml.safe_load`。无法仅从代码仓库证实这条对话要求与该缺陷之间存在直接因果关系，确定的工程根因仍是缺少共享快照/缓存和请求间短路。

### 修复方案与实施范围

1. 引入按 Wiki 根目录隔离的只读 `VaultSnapshot`/目录服务：一次扫描生成目录条目、frontmatter 元数据、来源别名、关系边和节点；列表、详情、图谱均消费同一快照，避免相互嵌套重建。
2. 使用文件版本签名（相对路径、大小、`mtime_ns`，必要时内容哈希）识别变更；稳定文件变更后重建一次快照并原子替换。详情正文仍可按需读取单个文件，但其 metadata、alias 和 relation 使用该快照。
3. 轮询接口返回修订号/ETag；客户端已持有当前修订时返回 `304` 或轻量未变化响应，不重复请求完整文件列表。
4. 目录监控负责触发失效或重建，不另行维护一套与 API 不一致的扫描结果。重建失败时保留上一份有效快照并记录错误。
5. 增加计数型回归测试：无文件变化时，多次请求 `/api/library`、图谱、详情不应重复全量 `yaml.safe_load`/构图；修改、新增、删除和发布事务完成后应只刷新一次，并验证各 API 视图一致。
6. 以当前 26 个文件建立请求耗时、文件读取次数、YAML 解析次数基线；修复后对照基线，同时用较大目录验证增长趋势。

### 修复结果与验证

- **代码修复**：已实施。
  - 新增 `src/wiki_snapshot.py`，按 Wiki 根目录维护进程级 `VaultSnapshot`；一次构建同时保存文件目录、frontmatter、来源映射、关系图和解析文档数据。
  - `/api/bootstrap`、`/api/library`、`/api/library/file`、`/api/library/graph` 改为消费同一快照；监控发现文件变化后主动失效并后台重建。
  - 快照使用相对路径、文件大小和 `mtime_ns` 指纹；构建期间继续服务上一份有效快照，完成后原子切换；连续变化合并为一个重建任务。
  - 文件详情对“磁盘中已存在但尚未进入旧快照”的新文件执行一次同步兜底，并让同步调用等待正在执行的构建任务，避免并发重复构建。
  - 文件正文仍按需读取，读取时只去除 frontmatter，不再次执行 YAML 解析。
- **测试/验证**：
  - `$env:PYTHONPATH='src'; ..\.venv\Scripts\python.exe tests/test_web_api.py`：10 tests OK。
  - `$env:PYTHONPATH='src'; ..\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py'`：163 tests OK。
  - `frontend\npm.cmd run build`：Vite production build 成功。
  - `git diff --check`：通过（仅保留 Git 的换行符提示）。
- **未纳入本次范围**：前端尚未基于 `snapshot.version` 实现 ETag/304 或停止轮询；因此 HTTP 请求仍会发生，但请求不会重新扫描 Markdown 或重新解析 YAML。后续可单独优化为版本短路。

---
