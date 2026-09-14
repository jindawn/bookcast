# 架构

## 当前实际实现（Phase 6）

Phase 6 增加可选的 BookCast Skill 文档，只调用原 CLI。Phase 5 的持久化任务、按 ID 恢复/重试、stale 恢复、缓存与日志保持原实现。内容新任务采用分块分析、章节与全书综合树、全局节目规划、分段写作和一致性复核，支持三种内容模式及质量门禁。当前支持书名候选解析、官方公开来源安全获取、用户 URL/本地文件导入，以及 EPUB/PDF/TXT → 可恢复 Mock 播客输出。公开来源首批为 Gutenberg CSV/RDF 与官方镜像 TXT；专门开放许可库和其他官方目录待扩展。内置 TTS 只有测试音调，真实语音、OCR、M4B 和 UI 未实现。LLM 兼容端点仅做过离线协议测试。

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
  SOURCES.md               书名、来源、安全下载和获取恢复说明
  CONTENT.md              分层内容、质量规则、模式和修订
  JOBS.md                 任务模型、恢复、缓存与可观测性
  PHASE5_VERIFICATION.md  强制终止测试与实际 CLI 恢复证据
  PHASE4_DEMO.md           内容 demo 的实际指标与音频证据
  PHASE3_DEMO.md           真实公开书籍 demo 的命令与证据
  HANDOFF.md               最新交接快照
  WORKLOG.md               追加式事实记录
  STATE.json              当前开发状态
  STATE.schema.json         状态契约 v1
scripts/
  validate_project.py     标准库离线校验
skills/bookcast/
  SKILL.md                可移植 Agent 指令，只有 CLI 调用，无实现代码
src/bookcast/
  cli.py                  Typer acquire/generate/jobs/status/resume/retry/config/doctor
  jobs.py                 只读任务发现、ID 解析、持锁/stale 与完整性投影
  models.py               Pydantic Job/Step/Artifact/Attempt、六态与书稿模型
  parsers.py              TXT/EPUB/PDF 本地解析
  source_api.py           BookIdentity、BookSourceProvider 与获取记录契约
  sources.py              Gutenberg/用户来源适配器、Source Registry
  source_http.py          有界 HTTPS、固定公网 IP、TLS 与重定向校验
  source_validation.py    MIME 对应关系、文件签名和 EPUB 容器安全检查
  acquisition.py          候选选择、下载/导入和解析检查点
  provider_api.py         与厂商无关的接口、能力、状态和错误契约
  provider_config.py      严格 TOML 配置及安全端点校验
  provider_registry.py    类型工厂注册、组合与注入
  provider_chain.py       有界故障切换及调用状态回调
  providers.py            Mock 实现（兼容旧接口导入）
  adapters/compatible.py  兼容远程/本地 LLM HTTP 适配器
  prompts.py              版本化领域提示词
  pipeline.py             原子检查点、旧任务兼容与新流程入口
  content_models.py       有界分析、主题、规划、脚本和复核模型
  content.py              分层内容编排、全局规划、独立调用缓存
  content_mock.py         离线规则分析与对话示例
  quality.py              指标、来源检查与 TTS 前门禁
  storage.py              原子写入、指纹、锁和 SHA-256
  audio.py                WAV 校验与 FFmpeg MP3 合并
tests/
  test_validate_project.py 交接校验器回归测试
  test_phase1.py          Phase 1 解析、Provider、恢复和 CLI 测试
  test_providers.py       Phase 2 协议、故障注入、强制退出恢复及配置测试
  test_content.py         长书、三种模式、质量门禁、分块恢复和修订
  test_job_recovery.py    真实 SIGKILL、并发排除、缓存失效与最小任务恢复
  test_job_cli.py         Job 命令、快照配置、迁移、损坏隔离与日志故障
  test_skill.py           Skill 示例调用、Core 委托、无 Skill 独立运行
  test_sources.py         书籍身份、来源资格、获取恢复与安全容器测试
  test_source_http.py     下载限额、MIME、重定向、公网 IP 与 TLS 测试
examples/
  example.txt             可再分发的自制示例书
  content-demo.txt         三章自制协作主题内容样本
  providers.toml          无凭证的三层 Mock 优先级配置
```

应用运行环境为 Python 3.12+、Typer、Pydantic、EbookLib、BeautifulSoup、PyMuPDF 和 FFmpeg；`uv.lock` 固定依赖解析结果。`scripts/validate_project.py` 仍是标准库工具，应用测试用 pytest。没有数据库，任务状态保存在每个输出目录的 `manifest.json`。

## Pipeline 与边界

可选入口为用户目标 → Agent 加载 Skill → 既有 CLI → BookCast Core。Skill 选择命令和参数、读取状态/质量报告并反馈；版本候选、版权资格、文件验证、解析、生成、恢复算法和音频拼接都由 Core 执行。Skill 不导入私有模块或厂商 SDK，不写 manifest/缓存，不维护第二套任务状态。Core 对 Skill 没有反向依赖，包的构建配置只分发原应用，命令行用户不需要安装 Skill。

技能目录只有 SKILL.md；可单独给 Agent 加载，不绑定某个宿主的全局安装路径。命令示例由测试直接传入真实 CLI，外部来源边界用自制样本隔离；这验证调用边界，不证明任意 Agent 一定遵守每条自然语言指令。

应用仍是单机本地工具，不预设微服务。当前 Pipeline 由 `Pipeline` 编排，Provider 与具体厂商解耦；以下表格同时标示已实现和后续边界。

| 边界 | 输入与输出 | 必须保留的语义 |
| --- | --- | --- |
| 书目识别 | 书名 → BookIdentity / EditionCandidate → 明确选择 | 已实现官方 CSV 目录搜索，多个候选不自动选择；未知印刷版次不猜填 |
| 来源与导入 | SourceOffer → SourceAsset | 已实现官方 RDF 书籍版权判定、镜像 TXT、用户 URL/文件、安全下载和摘要 |
| 整书解析 | SourceAsset → NormalizedBook | 已实现 TXT/EPUB/PDF；章节顺序、文本、源位置、警告 |
| 内容生成 | Chapter → 分块分析 → 分层综合 → 全局规划 → 分段对话 → 一致性复核 | 九类 finding、证据定位、预算与去重、三种模式和质量门禁；详见 CONTENT |
| 语音合成 | 脚本 → WAV 片段 | 已实现 Mock 测试音调；真实 TTS 待后续 Provider |
| 封装导出 | WAV 片段 → MP3 | 已实现 FFmpeg concat；M4B 待后续阶段 |
| 任务编排 | 输入与配置 → manifest.json | 已实现 Job/Step/Artifact/Attempt、原子写入、六态恢复、缓存与任务命令 |

正常流程为“书名识别 → 版本确认 → 合法来源/用户导入 → 解析 → 内容生成 → TTS → 导出”；直接导入用户文件也是独立入口，不强迫先联网搜书。扫描型 PDF 的 OCR 需求必须显式检测与报告，具体实现排期见 ROADMAP。

Provider 的依赖方向为 CLI → Registry → 具体适配器，Pipeline → 中立接口/ProviderChain。业务代码不导入适配器或厂商 SDK。LLM 契约为 generate、generate_structured、health_check、capabilities；TTS 契约含 synthesize、health_check、capabilities。配置和错误策略详见 [PROVIDERS.md](PROVIDERS.md)。

Source Resolver 是 AI Pipeline 之前的独立边界：CLI → SourceRegistry / BookSourceProvider → EditionCandidate / SourceOffer → Acquirer → 既有 Parser。身份解析用本地缓存的官方目录，不调用 LLM。acquire 默认仅解析；--generate 将源文件与 BookMetadata seed 注入既有 Pipeline，metadata.acquisition 保留身份、来源依据和下载哈希。更换 Source Adapter 不修改业务 Pipeline。

获取任务保存独立 acquisition.json v1（pending/downloading/downloaded/parsing/parsed/failed），默认位于 imports。下载成功即保存凭据与哈希，解析失败可复用源文件；完成解析记录各 JSON 哈希。网络层和容器层都检查边界，不执行、浏览或解压运行下载内容。详细数值限制及网络例外选项见 [SOURCES.md](SOURCES.md)。

运行 manifest v3 是 Job 的权威状态，持有 Steps、Artifact 索引、AI Attempts、随机 job_id、导入前路径、来源 metadata seed、无密钥 Provider 配置快照与运行所有者。旧 status/error_kind 为兼容写入字段，state 是只读六态投影；不双写两套状态。每次 AI 调用和本地步骤各自落盘；文件及 manifest 使用临时文件、fsync、原子替换和父目录同步。

启动恢复先取得内核独占文件锁，再将遗留 RUNNING 步骤和 pending/running 尝试标为 interrupted/FAILED_RETRYABLE。RunOwner 的 PID、主机和会话 ID 只用于诊断；不根据超时抢锁。有效完成 Attempt 可修复尚未写成成功的 Step。新计划不再使用的历史步骤标为 SKIPPED，保留文件与调用历史。

缓存同时验证输入/提示版本、产物 SHA-256 和具体 Provider 配置摘要。同名实例配置改变使其旧调用失效；显式替换为其他实例保留已完成结果和原始归属，保证额度切换不重做前六章。规则和 D-010 的局部替代见 D-014 与 [JOBS.md](JOBS.md)。认证、输入、schema、业务错误仍不会自动切模型，需修复后 retry。

任务清单分阶段注册 PENDING，规划前显示已知剩余量与数量待定标志。jobs/status/doctor 不修改状态；损坏任务单独列出。进度写 stderr，事件追加至 logs/events.jsonl；日志不是恢复依据，日志或终端写入失败不能使已持久化 AI 成功变成失败。

## 分层内容与兼容

[CONTENT.md](CONTENT.md) 是当前内容契约与数值限制的使用指南。`ContentFlow` 复用 `_Runner.step/ai_operation`，所有随机生成结果由 ProviderChain 持久化调用记录。全局规划是确定性预算算法；每段只收到精选证据、前后主题及上一段有限结尾。质量报告先于 TTS 落盘，严重引用/矛盾/复述/角色问题停止，其他指标警告明确显示。

新 manifest 使用 schema v3，以 `pipeline_version=2` 区分内容契约，记录模式、预算与片段修订号。旧 pipeline_version=1 自动沿用旧路径，无静默重生成；迁移 manifest v1/v2 时原样备份，旧产物校验后复用，缺失历史字段不伪造。内容版本、Provider 配置、开发 STATE 与获取 acquisition 是不同概念，见 D-013。

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

Phase 6 范围止于可选 Skill、命令契约/依赖边界验证和文档；核心模块与持久格式不变。真实重启后的用户操作是重新运行 resume，未实现开机自动执行。真实中文写作与语义复核仍需授权样本及人工验收。句子边界分块、语义去重、根综合代表性、时间预算精度、真实语音等属于后续候选，不能从 Mock 测试推断已达到出版质量。
