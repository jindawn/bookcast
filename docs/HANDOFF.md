# 给下一位 AI Agent 的交接

快照更新：2026-09-13T02:19:26Z。本快照对应 **Phase 0 已完成、Phase 1 尚未开始**；机器状态见 [STATE.json](STATE.json)。

## 当前目标

Phase 0 的目标已完成：建立 BookCast 项目骨架和跨 Coding Agent 接力机制。下一项工作是 Phase 1 的技术选型与本地 TXT 导入最小闭环；本次没有启动 Phase 1。

## 刚刚完成了什么

- 从空目录初始化 `main` 分支 Git 仓库，创建并验证了首个基础提交。
- 建立全部要求的项目文档：AGENTS、CLAUDE、README，以及产品、架构、路线图、决策、交接、日志和 STATE。
- 补充领域术语表、版本化 JSON Schema、离线校验脚本和 20 项回归测试。
- 完成文档交叉审阅，明确“规划不等于已实现”，修正状态契约更新和生成任务定义的两处歧义。
- 配置并抽查忽略规则，避免本地书籍、音频和凭证意外进入 Git；未配置或操作远程仓库。

## 修改的关键文件

- [AGENTS.md](../AGENTS.md)、[CLAUDE.md](../CLAUDE.md)、[README.md](../README.md)：统一入口和操作方法。
- [PRODUCT.md](PRODUCT.md)、[CONTEXT.md](../CONTEXT.md)、[ARCHITECTURE.md](ARCHITECTURE.md)、[ROADMAP.md](ROADMAP.md)、[DECISIONS.md](DECISIONS.md)：范围、术语、现状、阶段与已确定约束。
- [STATE.json](STATE.json)、[STATE.schema.json](STATE.schema.json)、本文件、[WORKLOG.md](WORKLOG.md)：机器状态、契约和可追溯交接。
- [.gitignore](../.gitignore)、[validate_project.py](../scripts/validate_project.py)、[test_validate_project.py](../tests/test_validate_project.py)：数据忽略和接力机制校验。

## 当前代码状态

实际代码只有交接校验工具及其测试，没有应用入口、下载器、解析器、AI/TTS 或 MP3/M4B 导出。应用语言、框架、数据库和供应商仍未选型；Python 仅用于当前离线校验。目标架构是书目/版本 → 来源/导入 → 解析 → 内容生成 → TTS → 导出，由可恢复任务串联，详见 ARCHITECTURE。

`STATE.current_phase` 保留 `phase-0`，`task_status` 为 `completed`，`in_progress`、`known_failures`、`blockers` 均为空。当前工作区是否干净请实际执行 `git status --short --branch` 检查。

## 已运行的测试及结果

验证环境：macOS，Python 3.14.3，Git 2.50.1。以下检查在基础提交 `60a55951301bed56aa00aa9d8e55a30746bc2fc3` 上通过；STATE 的 `tests.scope=commit` 指该提交。之后的交接快照只更新文档及状态，提交前后运行对应的文档/状态校验。

| 检查 | 结果 |
| --- | --- |
| `python3 scripts/validate_project.py` | 通过：必需文件、状态契约与语义、真实 UTC 时间、本地链接和 Git 引用 |
| `python3 -m unittest discover -s tests -v` | 20 项全部通过 |
| `git diff --check` | 通过；基础提交前也运行 `git diff --cached --check` |
| 忽略规则抽查 | 6 个私有输入/产物路径被忽略，3 个项目/示例路径保持可跟踪 |
| 跨文档审阅 | 产品目标、架构现状、阶段、运行测试方法及下一步一致 |

业务测试不适用：尚无业务代码。可按 README 的命令离线复验，不需要 API key 或下载书籍。

## 未解决问题与技术债

- 无已观测失败、无阻塞 Phase 0 的问题。
- 应用技术选型和全部业务功能待后续实现，不属于本阶段失败项。
- 开源许可证与贡献说明尚未确定，需在首次公开发布前完成；目前未附带开源许可证。
- 尚未建立 CI 或跨平台/多 Python 版本测试；最低版本目标为 Python 3.10，当前实测版本见上文。
- 校验器仅覆盖当前 Schema 子集和常见 Markdown 本地链接，不验证锚点，也不能自动证明文档语义一致。扩展契约时同步脚本与测试，阶段结束仍需人工审阅。

## 下一步建议

1. 按 [AGENTS.md](../AGENTS.md) 阅读入口文档，检查 Git 与实际代码，运行上述校验。
2. 将 STATE 更新为 Phase 1 进行中，并声明原子任务、负责人和文件范围。
3. 先在 DECISIONS 记录最小运行栈、书稿输入输出约定及任务状态持久化方式，再实现本地 TXT → ParsedBook → 可查询任务状态。
4. 用自制小样本覆盖成功、空文件、编码错误与读取失败；更新 README、STATE、HANDOFF、WORKLOG 后小步提交。验收要求见 ROADMAP Phase 1。

## 不要重复做的事情

- 不要重新初始化 Git、重新搭建 Phase 0，或维护与 AGENTS 冲突的另一套 Agent 规范。
- 不要把目标模块当作现有实现；不要从下载器、AI、TTS 或完整 UI 开始扩张范围。
- 不要未经记录推翻本地优先、合法来源、版本区分、整书覆盖可追溯及分阶段恢复原则。
- 不要提交用户书籍、凭证或音频，也不要覆盖其他 Agent 的未提交修改。
- 不要为了将快照自身哈希写进文件而反复 amend；遵守 DECISIONS D-006。

## 最近 Git commit

已验证基础提交：`60a55951301bed56aa00aa9d8e55a30746bc2fc3` — `chore: initialize BookCast agent-ready project skeleton`。

本文件与 STATE 随后的交接快照提交标题为 `docs: finalize Phase 0 verification and handoff`。其实际哈希用 `git log -1 --format='%H %s'` 获取；快照不能包含自身哈希，因此 `last_verified_commit` 保留上述已验证基础提交，不代表业务进度未完成。
