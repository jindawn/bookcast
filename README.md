# BookCast

BookCast 是一个以开源为目标的本地工具：从书名或用户提供的 EPUB、PDF、TXT 出发，确认正确书籍版本，获取合法来源，解析整本书，再用 AI 生成高质量中文音频、精读内容或双人播客，最终导出 MP3 / M4B。

**当前阶段：Phase 1 MVP。** BookCast 已支持用户提供的 EPUB、PDF、TXT，使用离线 Mock Provider 生成章节摘要、双人播客脚本、测试音调 WAV，并用 FFmpeg 合并为 MP3。尚未实现互联网找书、真实 AI/TTS、OCR 或 M4B；最终测试与提交证据见 [HANDOFF.md](docs/HANDOFF.md) 和 [STATE.json](docs/STATE.json)。

## 开始接手

先读 [AGENTS.md](AGENTS.md)，再按其中顺序阅读项目文档。Claude Code 通过 [CLAUDE.md](CLAUDE.md) 使用同一套规则。Codex、Pi 或其他 Agent 若不自动加载 AGENTS.md，应手动读取；本仓库不假设任一工具的自动发现行为。

| 文件 | 用途 |
| --- | --- |
| [PRODUCT.md](docs/PRODUCT.md) | 产品目标、用户路径、质量与来源边界 |
| [CONTEXT.md](CONTEXT.md) | 作品、版本、源文件、解析书稿等领域术语 |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | 实际仓库结构与尚未实现的目标架构 |
| [ROADMAP.md](docs/ROADMAP.md) | 分阶段范围及验收条件 |
| [DECISIONS.md](docs/DECISIONS.md) | 已确定原则、原因和待选型事项 |
| [HANDOFF.md](docs/HANDOFF.md) | 给下一位 Agent 的最新交接快照 |
| [WORKLOG.md](docs/WORKLOG.md) | 追加式开发事实记录 |
| [STATE.json](docs/STATE.json) / [STATE.schema.json](docs/STATE.schema.json) | 机器可读工作状态及版本化契约 |

## 如何运行与测试

在仓库根目录运行；需要 **Python 3.12+、FFmpeg、Git**。依赖由 `uv` 管理，也可以使用兼容 PEP 621 的工具安装 `pyproject.toml`。Mock 模式无需 API key 或网络。

```sh
python3.12 --version
git --version
uv sync --extra dev
uv run bookcast generate examples/example.txt
uv run bookcast status <book_id> --json
uv run pytest -q
python3 scripts/validate_project.py
git diff --check
```

`bookcast generate ./books/example.epub` 是主入口；也支持 `.pdf` 和 `.txt`。产物在 `output/{book_id}/`，包含 `source/`、`metadata.json`、`chapters/`、`analysis/`、`scripts/`、`audio/`、`manifest.json` 和 `podcast.mp3`。失败后用相同输入加 `--resume`，只重跑缺失、损坏或指纹改变的步骤；`bookcast status <job>` 显示进度和产物完整性。`--output-dir` 可指定产物根目录。

`python3 scripts/validate_project.py` 仍用于 Phase 0 文档/状态入口校验。`uv run pytest -q` 运行解析、Provider、流水线、幂等、恢复和 CLI 测试；当前不需要访问网络。

Mock TTS 生成的是主持人/嘉宾可区分的测试音调，并在导出元数据中标注“非人声”；它用于验证音频管线，不是自然语言朗读。PDF 当前按页形成章节，扫描页会产生 OCR 警告；EPUB 按 spine 顺序提取 HTML 正文。不会联网找书，也不绕过 DRM 或访问控制。

## 数据与开发约定

原始书籍、个人数据、生成产物与凭证保存在本地，常见数据目录和文件类型已加入 [.gitignore](.gitignore)。未来测试只使用可再分发的小型自制或明确授权样本；`.gitignore` 不能替代提交前检查。调用外部 AI/TTS 时应让用户明确知道会发送的内容并选择启用。

本地优先、合法来源、版本可区分、处理结果可追溯，以及分阶段可恢复是已确定方向。Phase 1 选定 Python 3.12+、Typer、Pydantic、EbookLib、BeautifulSoup、PyMuPDF 和 FFmpeg；Provider 仅通过 `LLMProvider` / `TTSProvider` 契约接入，当前默认实现是 Mock。具体状态以 DECISIONS 为准。

许可证尚未选定，本仓库暂未附带开源许可证；公开发布前须补齐许可证与贡献说明。该事项不阻塞当前 Phase 1 本地 MVP。
