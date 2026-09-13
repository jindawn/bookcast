# 给下一位 AI Agent 的交接

快照更新：2026-09-13T02:48:17Z。本快照对应 **Phase 1 功能已提交并验证，完成交接快照**；机器状态见 [STATE.json](STATE.json)。

## 当前目标

Phase 1 功能目标已完成并创建功能提交：实现用户本地 EPUB/PDF/TXT → NormalizedBook → Mock 摘要 → Mock 双人脚本 → 测试音调 WAV → FFmpeg MP3 的可运行 CLI，并支持章节级幂等和恢复。代码已验证并推送到远程 GitHub。准备进入 Phase 2。

## 刚刚完成了什么

- 采用 Python 3.12+、Typer、Pydantic、EbookLib、BeautifulSoup、PyMuPDF、FFmpeg，并用 `uv.lock` 固定依赖。
- 实现 `bookcast generate` 和 `bookcast status`；默认 Mock Provider 不需要 API key 或网络。
- 实现 TXT（UTF-8/UTF-16 BOM）、EPUB（spine 顺序和 HTML 清理）、PDF（按页文本提取）解析，保留章节来源位置并报告图片/OCR 警告。
- 以 Pydantic 模型表示 NormalizedBook、ChapterAnalysis、PodcastScript、Manifest 和 StepRecord。
- 每个步骤使用输入/Provider 指纹、SHA-256、原子写入和 manifest checkpoint；中断、失败或损坏产物可用 `--resume` 恢复。
- Mock TTS 生成主持人/嘉宾不同频率的 24 kHz PCM16 WAV，FFmpeg 合并为 96 kbps MP3；导出 JSON 明确这是测试音调。
- 添加 30 项 pytest 测试（含 10 个参数化子场景）和自制示例 `examples/example.txt`。

## 修改的关键文件

- [pyproject.toml](../pyproject.toml)、[uv.lock](../uv.lock)：Python 包元数据、依赖和 CLI 入口。
- [src/bookcast/cli.py](../src/bookcast/cli.py)、[pipeline.py](../src/bookcast/pipeline.py)：命令和可恢复编排。
- [models.py](../src/bookcast/models.py)、[parsers.py](../src/bookcast/parsers.py)：数据模型与三种本地格式解析。
- [providers.py](../src/bookcast/providers.py)、[audio.py](../src/bookcast/audio.py)、[storage.py](../src/bookcast/storage.py)：Provider 契约、Mock 音频、FFmpeg 合并、原子存储和锁。
- [tests/test_phase1.py](../tests/test_phase1.py)：解析、Provider、流水线状态、幂等、恢复、完整性和 CLI 测试。
- [README.md](../README.md)、[ARCHITECTURE.md](ARCHITECTURE.md)、[ROADMAP.md](ROADMAP.md)、[DECISIONS.md](DECISIONS.md)、[STATE.json](STATE.json)、本文件和 [WORKLOG.md](WORKLOG.md)：文档与状态。

## 当前代码状态

可以运行：

```sh
uv sync --extra dev
uv run bookcast generate examples/example.txt
uv run bookcast status <book_id> --json
```

产物目录为 `output/{book_id}/`，包含 `source/`、`metadata.json`、`chapters/`、`analysis/`、`scripts/`、`audio/`、`manifest.json` 和 `podcast.mp3`。`bookcast generate` 不带 `--resume` 时允许已完成任务快速复用有效检查点；失败任务需加 `--resume`。Provider 配置变更会拒绝覆盖旧任务。

尚未实现互联网找书、真实 LLM/TTS、OCR、M4B、GUI、CI 或开源许可证。PDF 扫描页和 EPUB 图片页只报告警告或部分覆盖，不伪装成完整文本。

## 已运行的测试及结果

验证环境：macOS，Python 3.12.14，FFmpeg 可用。以下命令在当前工作区通过：

| 检查 | 结果 |
| --- | --- |
| `UV_CACHE_DIR=/tmp/bookcast-uv-cache uv run pytest -q` | 30 passed，5 个 PyMuPDF 兼容性弃用警告 |
| `python3.12 -m compileall -q src tests` | 通过 |
| `python3 scripts/validate_project.py` | 通过 |
| `git diff --check` | 通过 |
| CLI TXT 端到端 + `status --json` | 通过，生成 MP3，完整性为 `ok` |

自动化测试不需要网络；依赖安装曾因沙箱 DNS 失败，获准后完成并生成 `uv.lock`。真实服务、长书籍和多平台 FFmpeg 尚未测试。

## 未解决问题与技术债

- 无代码失败，无阻塞项；Phase 1 功能提交已完成并推送到远程 GitHub。
- Mock TTS 是测试音调，不是自然语音；真实 Provider 需要在后续阶段实现且不能绕过接口。
- TXT 分章依赖常见中文/英文标题模式；无标题文本会作为单章，复杂排版需要 Phase 2 策略。
- PDF 按页生成章节，扫描件需要 OCR；EPUB 的目录层级和复杂资源仍需增强。
- 尚未建立 CI、跨平台音频验证、M4B 封装和许可证/贡献说明。

## 下一步建议

1. 先按 [AGENTS.md](../AGENTS.md) 阅读所有入口文档，运行完整测试并核对实际 Git。
2. Phase 2 评估 EPUB/PDF 深度解析、目录层级、OCR 支持范围和授权样本，先在 DECISIONS 记录取舍。
3. 增加真实 Provider 前，定义配置、隐私提示、超时/重试和成本边界；保持 Mock 测试可用。
4. 完成后同步 README、STATE、HANDOFF、WORKLOG 并小步提交；不要扩展互联网找书到 Phase 2。

## 不要重复做的事情

- 不要重新初始化 Git、重建 Phase 0 文档或维护与 AGENTS 冲突的规则。
- 不要把 Mock 音调称为真实语音，也不要声称已实现联网找书、OCR、M4B 或真实 AI。
- 不要删除输出目录、用户书籍或凭证；不要提交 `output/`、私有输入和音频。
- 不要绕过 LLMProvider/TTSProvider 直接绑定厂商，也不要移除 manifest、指纹、哈希和原子写入。
- 不要在没有记录决策的情况下推翻本地优先、合法来源、版本区分和可恢复处理原则。

## 最近 Git commit

- `9e6e5aaa0db1a61320be9c28815caaf7c4b3f5cc`（`feat: add local ebook mock podcast pipeline`）：Phase 1 完整功能实现与相关测试/文档。
- 本交接快照提交通过 `git log -1 --oneline` 查询，遵守 D-006，不把快照自身哈希写入同一次提交。
