# 架构

## 当前实际实现（Phase 2）

当前支持 EPUB、PDF、TXT 解析、离线 Mock 播客输出，以及兼容远程/本地 LLM 的统一接入、逐章故障切换与恢复。内置 TTS 只有测试音调；没有互联网找书、真实语音、OCR 或 M4B。兼容端点已做离线协议测试，未做真实服务验收。

```text
AGENTS.md / CLAUDE.md      Agent 统一入口
README.md                 项目状态与运行、测试说明
CONTEXT.md                领域词汇表
.gitignore                本地数据、凭证、生成产物忽略规则
docs/
  PRODUCT.md              产品范围
  ARCHITECTURE.md          实际结构与架构规划
  ROADMAP.md               阶段与验收条件
  DECISIONS.md             已确定决策与待选项
  PROVIDERS.md             Provider 配置、错误策略、审计和扩展指南
  HANDOFF.md               最新交接快照
  WORKLOG.md               追加式事实记录
  STATE.json              当前开发状态
  STATE.schema.json         状态契约 v1
scripts/
  validate_project.py     标准库离线校验
src/bookcast/
  cli.py                  Typer generate/status/config providers/doctor 入口
  models.py               Pydantic 任务与书稿模型
  parsers.py              TXT/EPUB/PDF 本地解析
  provider_api.py         与厂商无关的接口、能力、状态和错误契约
  provider_config.py      严格 TOML 配置及安全端点校验
  provider_registry.py    类型工厂注册、组合与注入
  provider_chain.py       有界故障切换及调用状态回调
  providers.py            Mock 实现（兼容旧接口导入）
  adapters/compatible.py  兼容远程/本地 LLM HTTP 适配器
  prompts.py              版本化领域提示词
  pipeline.py             章节级幂等流水线与恢复
  storage.py              原子写入、指纹、锁和 SHA-256
  audio.py                WAV 校验与 FFmpeg MP3 合并
tests/
  test_validate_project.py 交接校验器回归测试
  test_phase1.py          Phase 1 解析、Provider、恢复和 CLI 测试
  test_providers.py       Phase 2 协议、故障注入、强制退出恢复及配置测试
examples/
  example.txt             可再分发的自制示例书
  providers.toml          无凭证的三层 Mock 优先级配置
```

应用运行环境为 Python 3.12+、Typer、Pydantic、EbookLib、BeautifulSoup、PyMuPDF 和 FFmpeg；`uv.lock` 固定依赖解析结果。`scripts/validate_project.py` 仍是标准库工具，应用测试用 pytest。没有数据库，任务状态保存在每个输出目录的 `manifest.json`。

## Pipeline 与边界

应用仍是单机本地工具，不预设微服务。当前 Pipeline 由 `Pipeline` 编排，Provider 与具体厂商解耦；以下表格同时标示已实现和后续边界。

| 边界 | 输入与输出 | 必须保留的语义 |
| --- | --- | --- |
| 书目识别 | 书名 → Work / Edition 候选 → 用户确认 | 后续阶段；当前不联网找书 |
| 来源与导入 | 用户 EPUB/PDF/TXT → SourceAsset | 已实现本地拷贝、摘要和格式检查 |
| 整书解析 | SourceAsset → NormalizedBook | 已实现 TXT/EPUB/PDF；章节顺序、文本、源位置、警告 |
| 内容生成 | Chapter → ChapterAnalysis → PodcastScript | Mock 与可选兼容 LLM；Pydantic 和章节身份校验 |
| 语音合成 | 脚本 → WAV 片段 | 已实现 Mock 测试音调；真实 TTS 待后续 Provider |
| 封装导出 | WAV 片段 → MP3 | 已实现 FFmpeg concat；M4B 待后续阶段 |
| 任务编排 | 输入与配置 → manifest.json | 已实现步骤指纹、原子写入、重试、恢复和状态命令 |

正常流程为“书名识别 → 版本确认 → 合法来源/用户导入 → 解析 → 内容生成 → TTS → 导出”；直接导入用户文件也是独立入口，不强迫先联网搜书。扫描型 PDF 的 OCR 需求必须显式检测与报告，具体实现排期见 ROADMAP。

Provider 的依赖方向为 CLI → Registry → 具体适配器，Pipeline → 中立接口/ProviderChain。业务代码不导入适配器或厂商 SDK。LLM 契约为 generate、generate_structured、health_check、capabilities；TTS 契约含 synthesize、health_check、capabilities。配置和错误策略详见 [PROVIDERS.md](PROVIDERS.md)。

运行 manifest 升至 v2，steps 保留原有步骤状态；ai_calls 独立持久化 pending/running/completed/failed_retryable/failed_permanent，以及真实 provider/model、提示版本、输入输出哈希、UTC 时间。调用成功前原子落盘产物，失败只切换配置允许的四类临时/额度故障。认证、输入、结构与业务错误立即停止。各章节的 analysis、script、tts 独立恢复，已完成步骤不会因切换 Provider 失效。旧 v1 manifest 原样备份后验证并迁移；无法补齐的历史调用不伪造。

## 数据与失败边界（设计约束）

- SourceAsset 是输入资产；ParsedBook 是解析结果；脚本和 AudioArtifact 是派生产物，不能混为一个可覆盖文件。术语定义见 [CONTEXT.md](../CONTEXT.md)。
- 原文件默认保留，派生结果与原文件分开；任务记录追溯输入摘要、配置和阶段结果。大文件存本地文件系统，元数据及检查点使用 JSON。
- 整书处理必须记录全部可识别章节的覆盖情况。解析失败、缺页或未处理章节要显式出现，不能标记为完整成功。
- 外部书目查询、下载、AI、TTS 都属于外部适配边界；已有本地结果尽可能继续利用。当前串行处理、哈希缓存、有限 failover，无后台重试。远程调用完成但本地未落盘的中断窗口可能重复请求，不承诺外部调用恰好一次。
- “本地优先”指用户数据和任务控制在本地，不承诺所有模型离线可用。发送书稿至外部服务前让用户知情并选择启用，凭证不得提交。

## 开发状态与运行状态分开

[STATE.json](STATE.json) 仅描述仓库开发状态，**不是用户 GenerationJob 的持久化格式**。`task_status` 是当前开发任务的 `not_started / in_progress / blocked / completed`；`current_phase` 标示当前实现阶段，必须与 ROADMAP 和 HANDOFF 一致。

- `schema_version` 是兼容性版本；不兼容字段变化必须升版并同步 Schema、校验器、测试与文档。
- `completed_tasks`、`next_actions` 等字符串列表可供程序直接显示；`in_progress` 还包含负责人及文件范围，避免并行写入冲突。
- `tests` 保存命令、状态、范围、结果摘要与 UTC 时间；`not_run` 用 `checked_at: null`，不能用空列表暗示测试通过。
- `known_failures` 记录已观测失败；未实现功能和技术债写入 ROADMAP / HANDOFF，不伪装成已运行失败。`blockers` 只放阻止当前任务继续的事项。
- `branch` 和 `last_verified_commit` 是快照数据，接手时必须与实际 Git 比对；切换分支后及时更新。首次提交前后规则见 [DECISIONS.md](DECISIONS.md) D-006。
- 时间统一使用 UTC `YYYY-MM-DDTHH:MM:SSZ`；`important_files` 使用仓库相对路径，不依赖某台机器的绝对路径。

自动校验覆盖结构、引用和部分状态约束，不能证明产品范围与架构叙述一致；每阶段结束仍须人工交叉阅读 README、ROADMAP、HANDOFF、STATE 和 DECISIONS。

## 下一步架构工作

Phase 2 范围止于 Provider、配置、故障切换和恢复。后续阶段范围由用户确认；可优先用获授权的小样本验证真实兼容服务，再单独规划真实 TTS、内容质量和深度解析。不得把兼容协议测试视为某个厂商的上线验收。
