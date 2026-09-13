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

当前不做 UI、盗版爬虫、DRM/访问控制绕过、真实语音、OCR 或内容质量扩展；不承诺全目录及所有印刷版次可准确定位。

## 后续候选：中文内容质量

**状态：规划中。**

基于完整 ParsedBook 实现中文音频稿、精读内容和双人播客脚本，建立原文依据、分段处理、术语一致性和任务恢复机制。模型及供应商保持待选，不在前期骨架中固化。

验收重点：能够从输出定位相关章节或原文线索，区分原文与生成解释，检出关键内容缺失，并通过小规模人工质量评估。

## 后续候选：真实语音与 M4B 输出

**状态：规划中。**

接入语音合成，将内容稿转换为音频，支持章节信息、双人声音区分和 MP3 / M4B 导出。具体语音服务、编码器与封装工具在本阶段选择。

验收重点：音频可播放、章节及顺序正确、角色稳定，能够报告与恢复分段生成失败；以完整小书样例贯通从源文件到最终音频产物的流程。

## 开发纪律

阶段编号不构成实现承诺或发布时间表。若范围或顺序改变，应同步本文件与 STATE.json；涉及已确定架构约束时先阅读 DECISIONS.md，记录变更理由。每一阶段都沿用 [AGENTS.md](../AGENTS.md) 的测试、提交和交接要求。
