# BookCast 路线图

路线图描述开发顺序和各阶段的验收目标，不把计划能力视为已经实现。当前执行状态以 [STATE.json](STATE.json) 为准，最新接力信息见 [HANDOFF.md](HANDOFF.md)。进入一个阶段前先核对实际代码，完成一个原子任务后运行对应测试。

## Phase 0：项目初始化与 Agent 接力

**状态：已完成；验证结果和基础提交记录见 STATE.json 与 HANDOFF.md。**

目标是让没有聊天记录的新 Agent 仅凭仓库了解产品目标、架构规划、当前阶段、测试方法和下一步。

交付与验收：

- 建立简短统一的 Agent 入口，以及产品、架构、路线图、决策、交接、开发记录和机器可读状态文件。
- 明确代码现状与规划能力的区别，记录未确定的技术选择。
- 建立文档和状态的一致性检查、Git 忽略规则与基础提交。
- 新 Agent 按入口文档即可找到运行方法、测试结果、已知问题和下一步任务。

此阶段不包含应用入口、下载器、书籍解析、内容生成或音频导出。

## Phase 1：本地电子书 → Mock AI 播客 CLI MVP

**状态：已完成并提交（功能提交 9e6e5aa，交接提交 4bc8123）。**

Phase 1 接受用户自己的 EPUB、PDF、TXT，按 Parser → NormalizedBook → Chapter Analysis → Podcast Script → TTS → Audio Merge → Output 运行。使用 Mock LLM/TTS 时无需 API key；没有互联网找书、真实 AI/TTS、OCR 和 M4B。

验收目标：

1. `bookcast generate path` 支持 EPUB、PDF、TXT；保存 source、metadata、chapters、analysis、scripts、audio、manifest 和 podcast.mp3。
2. Pydantic 模型统一 NormalizedBook、分析、脚本、步骤和 manifest；Parser 保留章节顺序和来源位置线索，报告扫描 PDF 或图片 EPUB 警告。
3. 通过 LLMProvider/TTSProvider 契约提供 Mock 实现；无 API key 可完成摘要、双人脚本、测试音调和 MP3 合并。
4. 每一步以原子文件写入、指纹和 SHA-256 记录；`--resume` 跳过仍有效的完成步骤，损坏产物会重建；`status` 提供进度和完整性。
5. 30 项 pytest 覆盖项目校验、EPUB、PDF、TXT、Mock Provider、流水线状态、幂等、失败恢复、损坏产物恢复和 CLI；以自制小样本验证。
6. 更新 README、STATE.json、HANDOFF.md 和 WORKLOG.md，提交可独立接手的 MVP。

后续工作中 Provider 边界按用户最新要求纳入 Phase 2；更多解析样本和跨平台 FFmpeg 验证单独规划。

## Phase 2：Provider 抽象、模型故障与额度切换

**状态：已完成并验证（功能提交 2f06b01）；交接快照见 STATE.json 和 HANDOFF.md。**

1. LLMProvider 统一 generate、generate_structured、health_check、capabilities；TTSProvider 同样独立于厂商。Pipeline 不 import 具体适配器或 SDK。
2. Registry 支持 Mock、OpenAI-compatible 和 local LLM；内置 Mock TTS，允许工厂注册扩展而不改 Pipeline。
3. 按配置优先级切换额度耗尽、限流、临时不可用、超时；认证、输入、schema、业务错误停止。无隐藏重试、隐式 Mock 回退或无限切换。
4. 每次调用持久化 pending/running/completed/failed_retryable/failed_permanent，记录实际 provider/model、提示版本、输入输出哈希和时间；不保存 Secret。
5. 最小任务恢复保留已完成章节，兼容 v1 任务；`config providers`、`doctor` 和 `generate --provider auto` 可运行。
6. 完整测试 78 passed：含第七章切换、三级链、TTS 接管、进程强制退出及调用完成窗口恢复、schema/业务错误不切换、HTTP 协议与配置保护。真实服务尚未联网测试。

本阶段不增加互联网找书、真实语音、OCR、M4B 或内容质量优化。原路线图将深度解析编号为 Phase 2 的规划由本阶段替代，深度解析保留为后续候选。

## 后续候选：深度解析与真实服务验收

**状态：规划中，未开始，阶段编号与范围待用户确认。**

用授权小样本验收真实兼容 LLM；独立评估真实 TTS 与成本边界。解析方向可补充 EPUB 目录层级、PDF 复杂排版、扫描页 OCR 策略及跨平台 FFmpeg 样本。协议兼容性测试不等同于中文内容质量验收。

## Phase 3：书名识别与合法 Source Resolver

**状态：已完成并验证（功能提交 9bdf53c）；真实演示和交接证据见 STATE.json、HANDOFF.md 与 PHASE3_DEMO.md。**

1. BookIdentity 包含书名、作者、语言、ISBN、版次、出版年份，未知值为 null；目录发行日期单列。
2. BookSourceProvider/SourceRegistry 支持官方 Gutenberg CSV/RDF 和用户来源，允许后续工厂扩展。用户 URL 的使用权仅记为用户声明。
3. 多候选必须用 --edition 选择，可按作者/语言筛选；不会把不同目录条目静默合并。
4. 核对书籍版权，拒绝未知或版权未满足条件的条目；元数据 CC0 不当作书籍许可。官方适配器只接受明确的美国公有领域声明。
5. 限制下载大小、MIME、网络地址和容器内容；避免不安全文件名、路径穿越、私网重定向、实体声明和异常压缩数据。
6. 获取成功后解析，重复命令复用检查点；--generate 可接入既有 AI Pipeline 并保留来源。
7. 160 项全量离线测试通过；真实《国富论》demo 单独记录命令、版权依据、源文件哈希和解析结果。

本阶段不做 UI、盗版爬虫、DRM/访问控制绕过、真实语音、OCR 或内容质量扩展；不承诺全目录及所有印刷版次可准确定位。

## Phase 4：分层内容、播客质量与评估

**状态：已完成并验证（功能提交 d0bfde2）；交接与实际证据见 STATE.json / HANDOFF.md。**

1. 分块提取九类分析，每项具有可核验的原文偏移与证据；不存在的信息不猜造。
2. 章节及整书使用有界综合树，禁止整本长书进入单次上下文；所有调用与中间产物可恢复。
3. 全局规划先于脚本，按重要性、章节覆盖、主题去重、时间预算和上下文连续性安排片段。
4. 支持 summary、deep_read、two_host；Host A 解释，Host B 追问、反例与应用。
5. 逐段一致性复核加本地重复率、覆盖率、长度、角色、空泛表达和来源检查；严重问题在 TTS 前阻断。
6. 明确来源转述与主持人讨论/假设，拦截长段逐字复述；检查结果不是版权许可或事实准确的保证。
7. 支持指定片段修订，保留已完成分析；原 Provider 故障切换与旧任务恢复继续验证。
8. 自制三章中文 demo 完成，脚本/报告/MP3 证据见 [PHASE4_DEMO.md](PHASE4_DEMO.md)。详细限制见 [CONTENT.md](CONTENT.md)。

Mock 仍是规则生成和测试音调，没有真实模型质量或人声验收。分块边界、语义去重、根主题代表性以及极端预算下脚本长度需要进一步改进。后续任务可靠性由 Phase 5 覆盖；内容质量改进仍待用户授权。

## Phase 5：可靠性、断点恢复与任务系统

**状态：已完成并验证（功能提交 c05af03）；204项测试、10个子测试及实际CLI恢复demo通过。证据见 STATE.json、HANDOFF.md 和 PHASE5_VERIFICATION.md。**

1. manifest v3 的 Job、Step、Artifact、Attempt 和六态投影；旧 v1/v2 原样备份、保留原内容流程。
2. 最小 AI 调用与本地步骤独立保存；内核锁排除并发，崩溃遗留 RUNNING 安全恢复。
3. 输入、提示版本、产物与 Provider 配置哈希验证缓存；正常重启不重新消耗已完成调用。
4. jobs/status/resume/retry/doctor；按 ID 从导入副本和保存的配置接续，永久错误需显式重试。
5. 分阶段登记任务，显示书籍、阶段、章节、Provider、完成/剩余及错误；追加 JSONL 日志。
6. 真实 SIGKILL 覆盖解析、章节调用中、调用已落盘但步骤未完成；额度接管、缓存变更、损坏修复、终端断开和旧任务迁移均有测试。
7. 实际 CLI demo 与恢复证据见 [PHASE5_VERIFICATION.md](PHASE5_VERIFICATION.md)，使用见 [JOBS.md](JOBS.md)。

本阶段不实现后台调度、自动开机恢复、数据库、真实服务验收或 UI。无法对远端已完成但本地未保存的请求保证恰好一次；已持久化结果必须复用。

## Phase 6：封装可选 BookCast Skill

**状态：已完成并验证（功能提交0175f44）；19项Skill专门测试、223项全量测试和10个子测试通过，证据见STATE/HANDOFF。**

1. 提供 skills/bookcast/SKILL.md，定义使用条件、意图/模式/预算映射、CLI 参数、版权规则、状态、错误、resume 与示例。
2. 只调用既有 CLI，不复制 parsing、下载、AI pipeline、缓存算法或 FFmpeg 拼接；不依赖厂商 SDK 或私有模块。
3. 明确源书语言与输出语言、分钟预算与实际时长、Mock 与人声的区别；多版本需明确选择。
4. 执行文档示例，通过 Core 完成三种模式、状态与恢复；确认版权资格、版本歧义和永久错误仍由 Core 拦截。
5. 在不含 Skill 的独立应用目录实际运行 CLI，验证生成/查询/恢复可用且有效缓存不变。

不新增 CLI 参数、Provider、人声、UI 或持久格式，也不自动安装宿主 Agent 配置。测试不等同于所有 Coding Agent 的自然语言行为验收；技能规则与维护说明见 [SKILL.md](../skills/bookcast/SKILL.md)。

## Phase 7：最小本地 Web App

**状态：已完成并验证（功能提交82c49da）；236项测试、10个子测试和3项浏览器E2E在提交上通过。最新证据见 STATE/HANDOFF。**

1. Next.js 静态客户端 → FastAPI Application API → 既有 Core；CLI/Skill 独立可用。
2. 书名候选、文件上传、三模式/分钟预算、进度、Provider 状态、Web 历史、MP3 播放及恢复。
3. 提交先落盘、幂等键、独立 worker 与内核锁；复用 Core 的检查点与永久错误保护。
4. API 测试包含上传边界、来源资格、音频 Range、缓存和真实 SIGKILL；Playwright 验证实际生成/播放/恢复及移动布局。
5. [WEB.md](WEB.md) 记录运行、存储、API 和 Tauri 评估；无账户、支付、云同步、开机自动执行或桌面包。

## Phase 8：免费开源中文 TTS

**状态：已完成并验证（功能提交54c05ad）；262项测试、10个子测试、3项浏览器E2E及真实中文合成/恢复通过。证据见 STATE/HANDOFF。**

1. Kokoro 多语言模型 + sherpa-onnx CPU；可选安装，不调用付费 API；保留中立 Provider 接口。
2. 官方固定模型包安装、限量安全展开、SHA-256 与资产完整性检查；双中文音色、语速/线程配置。
3. Core 逐语音单元 Step/Attempt、模型与音色缓存、SIGKILL 后最小恢复；旧 Mock 音频继续按 D-014 处理。
4. CLI/Web 如实区分人声、音调和历史类型；实际 MP3 中文样例及恢复证据见 [TTS.md](TTS.md)。
5. 付费 TTS 仅保留为未来显式配置的选项，本阶段未实现；真实 LLM 中文内容、主观听感与跨平台仍需独立验收。

## Phase 9：真实 LLM Provider 验收

**状态：进行中；离线实现已完成，真实生成/质量验收等待 DEEPSEEK_API_KEY。不能以 Phase 8 Mock 脚本人声替代。**

1. 核验官方 DeepSeek Flash 协议，复用兼容适配器及现有中立 Provider/Core。
2. 严格 thinking/effort/token预算、中央任务策略、服务端 usage、有效配置缓存和审计。
3. 离线错误与接管/恢复测试；收费测试仅在显式环境变量启用时执行。
4. 自制中文文本→真实分层内容→Kokoro→约5–10分钟MP3；人工验收与 resume 核对待执行，见 [PHASE9_REAL_LLM.md](PHASE9_REAL_LLM.md)。
5. 本阶段不主动 push，不开始 Phase 10。

## 后续候选：语音质量与 M4B 输出

**状态：规划中。**

在已实现本地中文人声基础上，试听并改进专有名词、停顿和长节目自然度，考虑支持 M4B 的章节信息与封装。付费 Provider 只能作为用户显式选择的扩展。

验收重点：音频可播放、章节及顺序正确、角色稳定，能够报告与恢复分段生成失败；以完整小书样例贯通从源文件到最终音频产物的流程。

## 开发纪律

阶段编号不构成实现承诺或发布时间表。若范围或顺序改变，应同步本文件与 STATE.json；涉及已确定架构约束时先阅读 DECISIONS.md，记录变更理由。每一阶段都沿用 [AGENTS.md](../AGENTS.md) 的测试、提交和交接要求。
