# 架构

## 当前实际实现（Phase 0）

本阶段只建立项目规范和可验证交接机制。不存在应用运行时、服务、数据库、下载器、解析器、AI/TTS 接入或音频管线。下列结构是实际仓库；后文模块是规划，不得据此声称已有代码。

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
  HANDOFF.md               最新交接快照
  WORKLOG.md               追加式事实记录
  STATE.json              当前开发状态
  STATE.schema.json       状态契约 v1
scripts/
  validate_project.py     标准库离线校验
tests/
  test_validate_project.py 交接校验器回归测试
```

校验脚本运行环境为 Python 3.10+ 和 Git，无第三方依赖。应用技术栈仍待 Phase 1 记录选型；当前未创建占位业务模块，以免新 Agent 误判实现进度。

## 目标架构（尚未实现）

目标是单机本地工具，先通过清晰模块边界组织，不预设微服务。以下名称描述职责，不承诺未来目录、接口签名或具体库。

| 边界 | 输入与输出 | 必须保留的语义 |
| --- | --- | --- |
| 书目识别 | 书名 → Work / Edition 候选 → 用户确认 | 作品与语言、译者、出版社、年份等版本信息分离；模糊时请求确认 |
| 来源与导入 | 已确认版本的合法资源，或用户文件 → SourceAsset | 来源、获取方式、授权依据或用户提供声明、文件摘要；可先导入再补版本元数据 |
| 整书解析 | SourceAsset → ParsedBook | 章节顺序、文本、源位置、覆盖范围与失败项；不得用局部摘要冒充全书解析 |
| 内容生成 | ParsedBook + 模式 → 带来源关联的脚本 | 中文音频、精读、双人播客分别定义质量要求；保留章节/片段映射 |
| 语音合成 | 脚本与声音配置 → 音频片段 | 模型或供应商通过适配器接入，支持替换；记录参数、失败与重试 |
| 封装导出 | 音频片段与章节元数据 → AudioArtifact | MP3 / M4B、顺序、时长、章节、导出路径与可播放性校验 |
| 任务编排 | 输入与配置 → GenerationJob | 阶段结果、可恢复进度、取消、错误记录及重试，贯穿上述边界 |

正常流程为“书名识别 → 版本确认 → 合法来源/用户导入 → 解析 → 内容生成 → TTS → 导出”；直接导入用户文件也是独立入口，不强迫先联网搜书。扫描型 PDF 的 OCR 需求必须显式检测与报告，具体实现排期见 ROADMAP。

## 数据与失败边界（设计约束）

- SourceAsset 是输入资产；ParsedBook 是解析结果；脚本和 AudioArtifact 是派生产物，不能混为一个可覆盖文件。术语定义见 [CONTEXT.md](../CONTEXT.md)。
- 原文件默认保留，派生结果与原文件分开；任务记录应能追溯输入摘要、配置与阶段结果。大文件存本地文件系统，元数据存储格式尚未确定。
- 整书处理必须记录全部可识别章节的覆盖情况。解析失败、缺页或未处理章节要显式出现，不能标记为完整成功。
- 外部书目查询、下载、AI、TTS 都属于外部适配边界；失败须可观察，已有本地结果尽可能可继续利用。具体缓存、并发、重试策略待实现验证。
- “本地优先”指用户数据和任务控制在本地，不承诺所有模型离线可用。发送书稿至外部服务前让用户知情并选择启用，凭证不得提交。

## 开发状态与运行状态分开

[STATE.json](STATE.json) 仅描述仓库开发状态，**不是未来用户 GenerationJob 的持久化格式**。`task_status` 是当前开发任务的 `not_started / in_progress / blocked / completed`；`current_phase` 保留最近所处阶段，完成 Phase 0 后不自动把未开始的 Phase 1 标为进行中。

- `schema_version` 是兼容性版本；不兼容字段变化必须升版并同步 Schema、校验器、测试与文档。
- `completed_tasks`、`next_actions` 等字符串列表可供程序直接显示；`in_progress` 还包含负责人及文件范围，避免并行写入冲突。
- `tests` 保存命令、状态、范围、结果摘要与 UTC 时间；`not_run` 用 `checked_at: null`，不能用空列表暗示测试通过。
- `known_failures` 记录已观测失败；未实现功能和技术债写入 ROADMAP / HANDOFF，不伪装成已运行失败。`blockers` 只放阻止当前任务继续的事项。
- `branch` 和 `last_verified_commit` 是快照数据，接手时必须与实际 Git 比对；切换分支后及时更新。首次提交前后规则见 [DECISIONS.md](DECISIONS.md) D-006。
- 时间统一使用 UTC `YYYY-MM-DDTHH:MM:SSZ`；`important_files` 使用仓库相对路径，不依赖某台机器的绝对路径。

自动校验覆盖结构、引用和部分状态约束，不能证明产品范围与架构叙述一致；每阶段结束仍须人工交叉阅读 README、ROADMAP、HANDOFF、STATE 和 DECISIONS。

## 下一步架构工作

Phase 1 先检查实际仓库和既有决策，再比较、确定最小运行栈，建立本地 TXT → ParsedBook → 任务状态的无 AI 骨架，并用自制文本验证章节顺序、失败输入和任务状态。具体实现方案形成后写入 DECISIONS；不要在初始化阶段预先锁定解析库、数据库或模型供应商。
