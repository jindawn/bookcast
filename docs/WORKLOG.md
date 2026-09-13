# 开发工作日志

本文件只追加重要、可验证的开发事实。已写入的历史条目不重写；纠错另加条目。时间使用 UTC，后续条目包含任务、结果、测试与关联提交（若当时已存在）。

## 2026-09-12T21:18:00Z — Phase 0 初始化开始

- 检查工作目录为空，没有已有应用代码、AGENTS.md 或 Git 历史。
- 使用 `git init -b main` 成功创建本地仓库，未配置或操作远程仓库。
- 开始创建统一 Agent 入口、产品与架构规划、交接快照、版本化状态契约及离线校验工具。
- 测试和首个提交尚未完成；具体最终结果在后续条目追加。

## 2026-09-13T02:18:17Z — Phase 0 骨架与校验完成，待基础提交

- 创建全部要求的入口与 docs 文件，补充 CONTEXT 术语表、STATE.schema.json、.gitignore、离线校验脚本及回归测试。
- `python3 scripts/validate_project.py` 通过；`python3 -m unittest discover -s tests -v` 的 20 项测试全部通过。
- Git 忽略抽查：6 个私有路径被忽略、3 个项目与示例路径可跟踪。
- 完成跨文档独立审阅，修正“状态契约变化”措辞并让 GenerationJob 定义覆盖导入阶段。
- 尚无应用入口或业务功能；运行栈和开源许可证待后续决定。当前正在创建首次基础提交。

## 2026-09-13T02:19:26Z — Phase 0 基础提交验证与交接完成

- 已创建基础提交 `60a55951301bed56aa00aa9d8e55a30746bc2fc3`（`chore: initialize BookCast agent-ready project skeleton`），共 15 个文件。
- 在该提交上运行离线项目校验、20 项回归测试和 `git diff --check`，全部通过；提交后工作区干净。
- 更新 STATE 为 Phase 0 completed，记录实际已验证基础提交，清空进行中任务并列出 Phase 1 下一步。
- 更新 HANDOFF、README 和 ROADMAP；记录未选技术栈、未定许可证、未建 CI 及校验器支持范围，不将未实现业务标成已测试能力。
- 本次未实现任何业务管线，未配置远程或发布；交接快照单独提交，其自身哈希按 D-006 通过 Git 查询。

## 2026-09-13T02:38:43Z — Phase 1 MVP 实现与验证

- 采用 Python 3.12+、Typer、Pydantic、EbookLib、BeautifulSoup、PyMuPDF 和 FFmpeg；`uv sync --extra dev` 成功并生成 `uv.lock`。
- 实现 `bookcast generate` / `status`，支持本地 TXT、EPUB、PDF；默认 Mock LLM/TTS 无需 API key，输出章节摘要、双人脚本、测试音调 WAV 和 MP3。
- 实现每步原子落盘、输入/Provider 指纹、SHA-256、进程锁和 `--resume`；损坏产物能被状态命令识别并恢复。
- 新增自制 `examples/example.txt` 和 Phase 1 测试；当前 `uv run pytest -q` 为 30 passed，compileall、Phase 0 校验和 CLI TXT 端到端冒烟均通过。
- 一次带临时目录清理的冒烟命令被自动审查因工具额度限制拦截，未执行删除；随后使用新临时目录完成相同验证。
- 当前没有开始 Phase 2；互联网找书、真实 Provider、OCR、M4B、CI 和许可证仍是后续事项。

## 2026-09-13T02:40:00Z — Phase 1 功能验证完成，提交受阻

- `uv run pytest -q`：30 passed；`python3.12 -m compileall -q src tests`、`python3 scripts/validate_project.py`、`git diff --check` 均通过。
- `bookcast generate examples/example.txt` 和 `bookcast status <job> --json` 端到端冒烟通过，生成 MP3 且完整性为 `ok`。
- 功能文件已暂存，但自动审查因当前 Codex 工具额度限制拒绝 `git commit`；普通模式也无法写入受保护的 `.git`，因此 `origin/main` 尚未包含 Phase 1。
- STATE/HANDOFF 已明确该 blocker；恢复 Git 提交权限后创建功能提交、推送并再固定最终交接提交。未开始 Phase 2。

## 2026-09-13T02:48:17Z — Phase 1 功能提交与交接快照完成

- 创建 Phase 1 功能提交 `9e6e5aaa0db1a61320be9c28815caaf7c4b3f5cc`（`feat: add local ebook mock podcast pipeline`），包含 20 个文件：EPUB/PDF/TXT 解析、Mock Provider、流水线、存储、音频合并、CLI、测试及设计文档。
- 在该提交上运行离线项目校验、30 项 pytest 测试和 `git diff --check`，全部通过。
- 清理 STATE 中的 blocker 与 in_progress，更新 last_verified_commit 为该功能提交，标记 Phase 1 completed。
- 更新 HANDOFF 与 WORKLOG，准备提交交接快照并推送到 GitHub `origin/main`。

## 2026-09-13T13:06:34Z — Phase 2 Provider 与恢复实现，完整测试通过

- 核对 AGENTS、架构、交接和实际 Git：main 为 4bc8123，Phase 1 功能提交 9e6e5aa 已存在，原路线图的提交阻塞描述过期。
- 实现 Provider API、Registry、TOML 配置、Mock/兼容 HTTP/local LLM、受控 failover、manifest v2 审计和逐任务恢复；无新增依赖。
- 新增 48 项 Provider 场景测试，覆盖第七章切换、三级链、真实子进程强制退出、调用完成窗口、TTS 和 v1 迁移。
- 新测试首次 5 项失败源于时间断言只接受 Z，修正为校验 UTC ISO 8601；当前全量 78 passed、10 subtests passed，5 个既有 PyMuPDF 弃用警告。
- compileall 与 git diff --check 通过；同步 README、产品/架构/路线图、新增 PROVIDERS 指南和 D-009/D-010。
- 未连接真实 AI 服务，未实现真实语音、找书、OCR、M4B，未开始后续阶段；功能提交与最终快照待创建。

- 2026-09-13T13:07:04Z：文档状态校验通过；三级 Mock CLI 冒烟生成 MP3，doctor ready，status integrity=ok，resume 不修改 manifest。
