# BookCast

BookCast 是一个以开源为目标的本地工具：从书名或用户提供的 EPUB、PDF、TXT 出发，确认正确书籍版本，获取合法来源，解析整本书，再用 AI 生成高质量中文音频、精读内容或双人播客，最终导出 MP3 / M4B。

**当前阶段：Phase 5 可靠性、断点恢复与任务系统。** 已实现持久化 Job/Step/Artifact/Attempt、六态任务、按 ID 恢复/重试和进度日志。新任务执行分块分析 → 全书综合 → 全局规划 → 分段脚本 → 一致性复核 → TTS，支持 `summary`、`deep_read`、`two_host`。书名来源入口和 Provider 故障恢复继续可用；默认 Mock 无需密钥，内置 TTS 仍是测试音调。真实服务质量、真实人声、OCR、M4B 与 UI 尚未验收或实现。证据见 [HANDOFF.md](docs/HANDOFF.md)、[STATE.json](docs/STATE.json)、[内容指南](docs/CONTENT.md) 和 [任务指南](docs/JOBS.md)。

## 开始接手

先读 [AGENTS.md](AGENTS.md)，再按其中顺序阅读项目文档。Claude Code 通过 [CLAUDE.md](CLAUDE.md) 使用同一套规则。Codex、Pi 或其他 Agent 若不自动加载 AGENTS.md，应手动读取；本仓库不假设任一工具的自动发现行为。

| 文件 | 用途 |
| --- | --- |
| [PRODUCT.md](docs/PRODUCT.md) | 产品目标、用户路径、质量与来源边界 |
| [CONTEXT.md](CONTEXT.md) | 作品、版本、源文件、解析书稿等领域术语 |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | 实际仓库结构与尚未实现的目标架构 |
| [ROADMAP.md](docs/ROADMAP.md) | 分阶段范围及验收条件 |
| [DECISIONS.md](docs/DECISIONS.md) | 已确定原则、原因和待选型事项 |
| [PROVIDERS.md](docs/PROVIDERS.md) | Provider 配置、故障分类、调用审计与扩展方式 |
| [JOBS.md](docs/JOBS.md) | Job ID、崩溃恢复、缓存、日志与旧任务迁移 |
| [CONTENT.md](docs/CONTENT.md) | 分层内容、三种模式、质量指标与定向修订 |
| [SOURCES.md](docs/SOURCES.md) | 书籍身份、合法来源、安全下载与获取检查点 |
| [HANDOFF.md](docs/HANDOFF.md) | 给下一位 Agent 的最新交接快照 |
| [WORKLOG.md](docs/WORKLOG.md) | 追加式开发事实记录 |
| [STATE.json](docs/STATE.json) / [STATE.schema.json](docs/STATE.schema.json) | 机器可读工作状态及版本化契约 |

## 如何运行与测试

在仓库根目录运行；需要 **Python 3.12+、FFmpeg、Git**。依赖由 `uv` 管理，也可以使用兼容 PEP 621 的工具安装 `pyproject.toml`。Mock 模式无需 API key 或网络。

```sh
python3.12 --version
git --version
uv sync --extra dev
uv run bookcast config providers
uv run bookcast doctor
uv run bookcast acquire "The Wealth of Nations" --list
uv run bookcast acquire "The Wealth of Nations" --edition gutenberg:3300
uv run bookcast generate examples/content-demo.txt --mode two_host --minutes 6 --output-dir output/phase4-demo
uv run bookcast generate examples/example.txt --provider auto
uv run bookcast generate examples/example.txt --provider auto --resume
uv run bookcast jobs --json
uv run bookcast status JOB_ID --json
uv run bookcast resume JOB_ID
uv run bookcast retry JOB_ID
uv run pytest -q
python3 scripts/validate_project.py
git diff --check
```

`bookcast generate ./books/example.epub` 是主入口；也支持 `.pdf` 和 `.txt`。产物在 `output/{book_id}/`，包含 `source/`、`metadata.json`、`chapters/`、`analysis/`、`synthesis/`、`plans/`、`scripts/`、`evaluation/`、`audio/`、`logs/events.jsonl`、`manifest.json` 和 `podcast.mp3`。新任务具有独立 Job ID；用 `jobs` 查找后执行 `resume JOB_ID`，校验并复用导入副本，即使原文件移走也可继续。永久错误修复后显式 `retry JOB_ID`；两者都保留有效完成检查点。`status` 显示持锁/stale、进度、最近错误和完整性，`doctor` 同时检查任务存储。`--output-dir` 指定查找根目录，也可直接传任务目录。

`--mode summary|deep_read|two_host` 选择模式，`--minutes` 设置脚本时间预算；同一本书比较不同模式时使用不同输出目录。质量报告包含重复率、实际章节覆盖、长度、角色比例、空泛表达和事实检查。阻断项会在 TTS 前停止；警告可以继续，Mock 的语义核验始终需人工复核。报告通过不代表内容已达到真实播客质量。

用 `--resume --revise-segment 0002` 可重写指定片段，保留分析和规划，并按输入变化重建下游。旧任务保持原流水线；不会因升级代码重做整本书。规则、缓存和限制见 [CONTENT.md](docs/CONTENT.md)，实际运行样本见 [PHASE4_DEMO.md](docs/PHASE4_DEMO.md)。

`python3 scripts/validate_project.py` 仍用于 Phase 0 文档/状态入口校验。`uv run pytest -q` 运行解析、Provider、流水线、幂等、恢复和 CLI 测试；当前不需要访问网络。

Mock TTS 生成的是主持人/嘉宾可区分的测试音调，并在导出元数据中标注“非人声”；它用于验证音频管线，不是自然语言朗读。PDF 当前按页形成章节，扫描页会产生 OCR 警告；EPUB 按 spine 顺序提取 HTML 正文。联网获取只走明确的公开来源，不绕过 DRM 或访问控制。

## 从书名或用户来源开始

`acquire` 默认只获取并解析，产物保存在 `imports/{acquisition_id}/`；加 `--generate` 才生成音频。多个候选时必须使用 `--edition <候选ID>` 选择，可用 `--author` 和 `--language` 筛选。ISBN、印刷版次和出版年份未知时保留 null，不把目录发行日期冒充出版年份。

首个公开来源使用 Project Gutenberg 官方镜像的 CSV/RDF，接受明确声明为美国公有领域的书籍并记录地区范围，目前下载 UTF-8 TXT。首次目录约 21 MB，之后缓存；慢网络可设置 `--download-timeout 600`，更新目录用 `--refresh-catalog`。专门的开放许可库和出版社目录适配器尚未实现。

```sh
uv run bookcast acquire "我的书" --file ./books/my-book.epub
uv run bookcast acquire "授权资料" --url https://publisher.example/book.pdf --format pdf --rights-confirmed
uv run bookcast acquire "The Wealth of Nations" --edition gutenberg:3300 --generate
```

示例 URL 需换成有权使用的真实直接链接。用户来源保留用户声明，不伪装成独立核验的授权。下载限制大小、MIME、公开网络地址和文件结构；全部源文件、书稿及缓存默认被 Git 忽略。获取失败后重复命令即可恢复；音频阶段失败时加 `--resume`。详细限制、Fake-IP 网络的安全处理和真实演示见 [SOURCES.md](docs/SOURCES.md) / [PHASE3_DEMO.md](docs/PHASE3_DEMO.md)。

## Provider 配置与恢复

新建任务默认读取工作目录的 `bookcast.toml`；没有该文件时仅使用 Mock。恢复默认沿用任务内保存的无密钥配置快照，不读取可能无关的当前目录配置；修改配置时显式传 `--config`。可从 [examples/providers.toml](examples/providers.toml) 复制配置，或通过 `--config examples/providers.toml` 使用示例。示例的 primary、secondary、tertiary 都是 Mock，可安全离线运行。`llm_priority` / `tts_priority` 决定链顺序；`--provider <配置名称>` 与 `--tts-provider <配置名称>` 各自限定为单一 Provider。详细字段见 [PROVIDERS.md](docs/PROVIDERS.md)。

显式配置外部 LLM 会将当前文本块、精选来源证据、分层分析、片段脚本、提示词及 JSON Schema 发往所配置端点；自动切换时也可能发送给链中的备用端点。请只启用愿意接收这些内容的服务。密钥仅通过 `api_key_env` 指向环境变量，禁止直接写进 TOML；运行日志和 manifest 不记录凭证或服务原始错误正文。

仅 rate limit、quota、临时不可用和 timeout 可触发切换；认证、输入、schema 和业务错误会停止。第 7 章分析失败时从其未完成文本块接续，前六章分析保持不变；综合节点、片段脚本、复核和 TTS 同样独立恢复。切换到另一 Provider 保留已完成任务的真实归属；同名 Provider 的模型/配置变化会使该实例的旧调用缓存失效，提示或输入变化也会失效。若要整本使用新模型，选择新的 `--output-dir`。详细策略见任务指南和 D-014。

manifest v3 保存步骤、产物、尝试记录、配置摘要和运行所有者，保留 provider、model、prompt_version、输入/输出 SHA-256 和 UTC 时间。旧 v1/v2 任务恢复前原样备份，保留原 pipeline 版本和有效产物；缺失的历史审计不伪造。崩溃后只有获得内核文件锁才能恢复遗留 RUNNING，不通过 PID 或等待时长抢占活动进程。外部请求已被服务处理但本地结果尚未落盘时，重启可能再次请求该未完成任务；不承诺外部请求恰好一次或无重复计费。

## 数据与开发约定

原始书籍、个人数据、生成产物与凭证保存在本地，常见数据目录和文件类型已加入 [.gitignore](.gitignore)。未来测试只使用可再分发的小型自制或明确授权样本；`.gitignore` 不能替代提交前检查。调用外部 AI/TTS 时应让用户明确知道会发送的内容并选择启用。

本地优先、合法来源、版本可区分、处理结果可追溯，以及分阶段可恢复是已确定方向。Phase 1 选定 Python 3.12+、Typer、Pydantic、EbookLib、BeautifulSoup、PyMuPDF 和 FFmpeg；Provider 仅通过 `LLMProvider` / `TTSProvider` 契约接入，当前默认实现是 Mock。具体状态以 DECISIONS 为准。

许可证尚未选定，本仓库暂未附带开源许可证；正式开源发布前须补齐许可证与贡献说明。该事项不阻塞当前本地 CLI 开发。
