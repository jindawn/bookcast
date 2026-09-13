# Coding Agent 入口

本文件适用于 Codex、Claude Code、Pi 及其他 Coding Agent；项目状态必须保存在仓库内。

## 开始工作

1. 依次阅读 [README.md](README.md)、[ARCHITECTURE.md](docs/ARCHITECTURE.md)、[ROADMAP.md](docs/ROADMAP.md)、[HANDOFF.md](docs/HANDOFF.md)、[STATE.json](docs/STATE.json)。
2. 修改架构前阅读 [DECISIONS.md](docs/DECISIONS.md)；涉及产品边界或术语时阅读 [PRODUCT.md](docs/PRODUCT.md) 和 [CONTEXT.md](CONTEXT.md)。
3. 查看 `git status --short --branch`、`git log -5 --oneline` 和实际相关代码，再判断现状。文档可能滞后，禁止猜测代码状态；发现偏差先记录并修正。
4. 从 `next_actions` 选择一个原子任务，更新 `STATE.json` 的当前任务、负责人及涉及文件。并行 Agent 先划分文件所有权，由主 Agent 负责状态文件和最终整合。

## 工作与验证

- 只完成当前阶段授权范围；规划不等于实现，不擅自推翻已确定决策。
- 每完成一个原子任务运行对应测试；失败、未运行及原因必须如实记入状态。当前命令见 README。
- 持续更新 `STATE.json`，在任务开始、完成、失败或阻塞时写入；契约见 [STATE.schema.json](docs/STATE.schema.json)。
- 每完成一个有意义的阶段创建 Git commit。优先小提交，提交前检查差异和测试，保证其他 Agent 随时能安全接手。
- 仅提交当前任务相关文件，不覆盖或撤销用户及其他 Agent 的未提交修改。
- 禁止 force push、删除用户数据或执行不可逆远程操作。书籍、密钥和生成音频不得意外进入 Git。

## 交接与停止

在结束会话、上下文即将耗尽、模型额度即将耗尽或主动停止前：

1. 更新 [HANDOFF.md](docs/HANDOFF.md)：目标、完成内容、关键文件、代码现状、测试结果、问题、下一步、不要重复做的事、最近已存在的 Git commit。
2. 同步 `STATE.json`，清理已结束的 `in_progress`，保留真实阻塞；向 [WORKLOG.md](docs/WORKLOG.md) 追加可验证事实，不记录冗长思考过程。
3. 运行对应测试及 `python3 scripts/validate_project.py`，检查文档一致性，提交一个可交接阶段；无法测试或提交时写明原因及未提交文件。
4. `last_verified_commit` 只记录实际验证过的完整提交哈希。快照自身的提交通过 `git log -1` 获取，避免自引用；详见 DECISIONS 的 D-006。

重要信息不得仅留在聊天窗口。接手依据是实际代码、Git 历史和仓库文档。
