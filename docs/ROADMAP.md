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

**状态：功能实现与验证完成；Git 功能提交待工具权限恢复。当前代码、测试和交接记录见 README、STATE.json 与 HANDOFF.md。**

Phase 1 接受用户自己的 EPUB、PDF、TXT，按 Parser → NormalizedBook → Chapter Analysis → Podcast Script → TTS → Audio Merge → Output 运行。使用 Mock LLM/TTS 时无需 API key；没有互联网找书、真实 AI/TTS、OCR 和 M4B。

验收目标：

1. `bookcast generate path` 支持 EPUB、PDF、TXT；保存 source、metadata、chapters、analysis、scripts、audio、manifest 和 podcast.mp3。
2. Pydantic 模型统一 NormalizedBook、分析、脚本、步骤和 manifest；Parser 保留章节顺序和来源位置线索，报告扫描 PDF 或图片 EPUB 警告。
3. 通过 LLMProvider/TTSProvider 契约提供 Mock 实现；无 API key 可完成摘要、双人脚本、测试音调和 MP3 合并。
4. 每一步以原子文件写入、指纹和 SHA-256 记录；`--resume` 跳过仍有效的完成步骤，损坏产物会重建；`status` 提供进度和完整性。
5. 28 项 pytest 覆盖 EPUB、PDF、TXT、Mock Provider、流水线状态、幂等、失败恢复、损坏产物恢复和 CLI；以自制小样本验证。
6. 更新 README、STATE.json、HANDOFF.md 和 WORKLOG.md，提交可独立接手的 MVP。

Phase 2 前的后续工作：真实 Provider 的配置边界、更多格式样本和跨平台 FFmpeg 验证；不要在本阶段扩展互联网来源发现。

## Phase 2：EPUB 与 PDF 解析

**状态：规划中。**

扩展源文件导入和统一书稿结构，保留目录、章节、正文及原文位置线索。对复杂排版、缺失文本和扫描 PDF 明确呈现支持范围；是否以及如何引入 OCR 在本阶段决策，不默认承诺所有 PDF 都可解析。

验收重点：以授权明确的 EPUB、文本型 PDF 和不可直接解析的样例验证结构、顺序与错误报告，不静默丢失内容。

## Phase 3：书目识别与合法资源发现

**状态：规划中。**

建立 Work 与 Edition 的候选匹配、用户版本确认、来源记录及合法资源发现流程。资源不可用或版本无法确认时，保留用户手动提供文件的路径。资源提供方、接口和下载策略尚未选定。

验收重点：覆盖同名作品、不同译本、信息不足和无可用资源场景；可用资源应能显示来源及可获得的授权信息，获取失败应可诊断。

## Phase 4：中文内容生成

**状态：规划中。**

基于完整 ParsedBook 实现中文音频稿、精读内容和双人播客脚本，建立原文依据、分段处理、术语一致性和任务恢复机制。模型及供应商保持待选，不在前期骨架中固化。

验收重点：能够从输出定位相关章节或原文线索，区分原文与生成解释，检出关键内容缺失，并通过小规模人工质量评估。

## Phase 5：语音合成与 MP3 / M4B 输出

**状态：规划中。**

接入语音合成，将内容稿转换为音频，支持章节信息、双人声音区分和 MP3 / M4B 导出。具体语音服务、编码器与封装工具在本阶段选择。

验收重点：音频可播放、章节及顺序正确、角色稳定，能够报告与恢复分段生成失败；以完整小书样例贯通从源文件到最终音频产物的流程。

## 开发纪律

阶段编号不构成实现承诺或发布时间表。若范围或顺序改变，应同步本文件与 STATE.json；涉及已确定架构约束时先阅读 DECISIONS.md，记录变更理由。每一阶段都沿用 [AGENTS.md](../AGENTS.md) 的测试、提交和交接要求。
