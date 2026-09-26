# Phase 19 本地干净历史重建（2026-09-26）

用户暂停所有 Phase19.3B 实验并要求仅在本地清理 Git 可推送祖先链。旧 `main` 原样保留为恢复点，禁止直接推送；新分支 `codex/phase19-clean-history` 从只读查询确认的最新 `origin/main`（`7f43ad733f1d9261bf458046261b274437158615`）创建。没有 push、force push、删除分支或改写旧历史。

重放方法：新分支从 `origin/main` 出发，直接应用旧 `main` **已移除硬编码 Gemini 值后的最终跟踪树**，合并为新提交 `18d86c2`。应用时暂存树与旧 `main` 的最终树完全相同。没有原样 cherry-pick 含旧值的 `8d6303d`、`9f3cfba` 或它们的后代。旧提交 SHA 仅作为审计来源，不能视为可安全推送的祖先。

保留的有效功能包括 Phase19 R0 审计/R1–R6 模型与 TTS 路由、Qwen LLM/TTS 适配、物理请求与成本遥测、Web TTS 设置传递；Phase19.1 Qwen 协议/预检；Phase19.2 价格、E2E 证据与遥测修正；Phase19.3A TTS A/B 资产与计价修正；Phase19.3B Dialogue **离线夹具和运行护栏**；以及 Gemini 硬编码回退的移除。所有这些是旧分支的**最终代码效果**，新分支不保留旧提交身份或旧提交中间状态。未执行任何真实 API 实验。

验证结果及新 HEAD 以本报告最后更新和 `docs/STATE.json` 为准。新分支仅适合显式推送该分支；旧 `main`、其他本地分支以及 `--all`/`--mirror` 推送均不在安全结论内。Provider 侧轮换仍由维护者完成。

## 凭证与代码等价验证

- 新 `HEAD` 第一安全提交 `18d86c2` 的唯一父提交为最新 `origin/main`；旧含值提交不在新分支祖先链。
- 创建第一安全提交前，暂存树与旧 `main` 已净化的最终跟踪树完全相同；之后仅改交接状态与本报告。`src/`、`tests/`、`scripts/`、`examples/`、`evaluation/` 及架构/决策/Provider/路线文档和旧 `main` 最终树均无文件差异，因此未发现功能遗漏。
- 对旧值作精确字节扫描：新分支可达历史中为 0；扫描新分支 1,352 个可达对象中的全部 810 个 blob（均不超过 1 MB），同时检查 Gemini、DeepSeek、DashScope/Qwen、OpenAI、Bearer/通用 token 模式。命中项位于测试与无密钥示例，属于模拟值、错误脱敏用文本或环境变量名，未发现另一处生产硬编码。最终交接提交后再次执行同类扫描。

## 验证结果

- Phase19 相关专项：196 passed。
- 默认完整离线 `pytest -q`：713 passed、1 skipped、7 deselected、10 subtests passed；无真实 API。
- `python3 scripts/validate_project.py`、`python -m compileall src tests scripts -q` 与 `git diff --check` 均通过。
- 交接提交后的最终 HEAD 还将接受一次只读可达历史及全部 tracked files 复核；结果在本次任务答复中给出。不 push。
