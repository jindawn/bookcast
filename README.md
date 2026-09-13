# BookCast

BookCast 是一个以开源为目标的本地工具：从书名或用户提供的 EPUB、PDF、TXT 出发，确认正确书籍版本，获取合法来源，解析整本书，再用 AI 生成高质量中文音频、精读内容或双人播客，最终导出 MP3 / M4B。

**当前阶段：Phase 0，项目初始化与多 Agent 接力机制。** 当前仓库包含开发规范、产品与架构规划、可读交接记录、机器可读状态及离线校验工具。尚无应用入口、书籍下载器、解析器、AI 生成、语音合成或音频导出功能。最终测试与提交证据见 [HANDOFF.md](docs/HANDOFF.md) 和 [STATE.json](docs/STATE.json)。

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

在仓库根目录运行；当前只需要 **Python 3.10+ 和 Git**，无需安装第三方依赖、配置 API key 或访问网络。Python 仅用于骨架校验，不代表已选定应用运行语言。

```sh
python3 --version
git --version
python3 scripts/validate_project.py
python3 -m unittest discover -s tests -v
git diff --check
```

第一条校验命令检查必需文件、文档本地链接、STATE 的字段与状态约束、时间、路径和 Git 引用。它使用标准库校验当前 Schema 用到的关键字，并非完整 JSON Schema 引擎；遇到不支持的关键字会报错。单元测试验证校验器能拒绝损坏或矛盾的交接信息。业务测试目前不适用，因为尚无业务实现。

**当前没有可启动的 BookCast 服务或 CLI。** 不存在 `npm start`、音频演示或下载命令。下一阶段将先选择技术栈并建立本地 TXT 导入 → 结构化书稿 → 任务状态的最小骨架，详见 ROADMAP 的 Phase 1。

## 数据与开发约定

原始书籍、个人数据、生成产物与凭证保存在本地，常见数据目录和文件类型已加入 [.gitignore](.gitignore)。未来测试只使用可再分发的小型自制或明确授权样本；`.gitignore` 不能替代提交前检查。调用外部 AI/TTS 时应让用户明确知道会发送的内容并选择启用。

本地优先、合法来源、版本可区分、处理结果可追溯，以及分阶段可恢复是已确定方向；运行语言、UI、数据库、解析库和模型供应商尚未选型。具体状态以 DECISIONS 为准。

许可证尚未选定，本仓库暂未附带开源许可证；公开发布前须补齐许可证与贡献说明。该事项不阻塞当前本地 Phase 0 工作。
