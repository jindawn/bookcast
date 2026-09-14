# 给下一位 Agent 的交接

更新时间：2026-09-14T04:12:39Z；当前分支 `main`。接手先按 AGENTS 阅读文档、检查 Git 与实际代码。

## 当前目标

Phase 4：提高书到播客的内容处理质量。功能、自动测试及实际 Mock demo 已完成，当前剩余工作是功能提交后的验证及最终交接快照。没有开始下一阶段。

## 刚刚完成

- 新任务由分块九类分析、章节/整书综合树、全局规划、分段脚本、逐段一致性复核、质量门禁组成，再进入 TTS。
- 支持 summary、deep_read、two_host 和分钟预算；双人角色分工、论据归属、前段实际结尾均进入写作提示。
- 全部生成调用复用 ProviderChain 审计和原子检查点，新增最小块/综合/复核任务；旧任务保留原路径和迁移能力。
- `--resume --revise-segment` 定向改写，保留分析与规划，按结尾和产物变化更新下游。
- 新增内容指南、决策 D-013、领域词汇及三章自制中文 demo；全部已与 README/架构/路线图对照。

## 关键文件与当前代码

`src/bookcast/content_models.py` 定义有界 schema；`content.py` 编排分层生成与本地 Planner；`quality.py` 计算诊断和阻断项；`content_mock.py` 提供规则示例。`pipeline.py` 保留旧运行器并按版本分流；`cli.py` 的 generate/acquire --generate 都支持模式与预算。新任务仍为 manifest schema v2，pipeline_version=2；STATE v1、acquisition v1 不变。

测试集中在 `tests/test_content.py`；`test_providers.py` 的故障注入已适配新调用顺序，仍验证永久错误停止、强制进程退出、旧任务迁移与无重复处理。没有新增依赖或厂商 SDK。

## 实际验证

- `.venv/bin/python -m pytest -q`：187 passed、10 subtests passed；5 个既有 PyMuPDF/SWIG 弃用警告。
- `.venv/bin/python -m compileall -q src tests scripts`：通过。
- `python3 scripts/validate_project.py`、`git diff --check`：通过。
- 实际 CLI demo：`examples/content-demo.txt --mode two_host --minutes 6`，产物在 `output/phase4-final-demo/5bdad5ca96f5e42cd019ff30/`。1285 字符、3/3 章来源覆盖、重复率 4.93%、Host B 27.47%，无阻断项。16 次调用，恢复后全部字节和 mtime 不变。
- ffprobe：MP3、24kHz、单声道、15.25 秒测试音调；这不是估计 321.2 秒的人声播客。语义复核明确 needs_review。详情及第一段实际话语见 [PHASE4_DEMO.md](PHASE4_DEMO.md)。

## 未解决问题与技术债

自动测试没有未解决失败。Mock 是规则提取和模板对话，不具备完整理解或语义事实核验；真实兼容 LLM 与真实 TTS 尚未验收。不要把结构测试通过当作内容达到发布质量。

分块是固定字符边界；上层代表性压缩会丢细节，Planner 仅精确文本去重、最多24片段；预算覆盖不足会显式报告。预算是估计，极长单段可能触发有界脚本限制；没有自动扩写、无限改写或语义去重。整书解析仍会占用内存，有界的是模型输入。

质量规则只检查部分引用、数字、角色和复述问题，不能证明所有事实正确或满足版权要求。修订失败时旧音频可能仍在目录中，应以 manifest 状态和质量报告为准。用户授权来源、Secret、下载防护及原文件保留原则继续有效；开源许可证、OCR、M4B、UI 等待后续范围。

## 下一步建议

1. 完成功能提交后，完整测试与项目校验通过再记录实际完整提交哈希；按 D-006 提交最终快照。
2. 新 Agent 先核对仓库，再按用户授权范围选择下一阶段；建议用小型授权样本和人工评审验收真实中文写作、论证保留与语义复核。
3. 改动内容契约或提示时更新版本及缓存依赖，不让模型切换重做已完成内容。

## 不要重复做

不要重新初始化项目或添加另一套 Agent 规则，不要用旧聊天猜代码。不要静默升级旧任务、重新获取 Phase 3 公有领域 demo 或改写已完成章节。已有来源、Provider 注册/故障切换、质量门禁和恢复都有回归测试。书稿、生成 JSON 和 MP3 留在忽略目录，不提交；不要 force push。

## 最近已存在的 Git commit

`ac2c3ad2e11b8cd2ea2ef76845d08c10cb376d26` — docs: finalize Phase 3 verification and handoff。
当前 Phase 4 变更尚未提交；最近已验证功能提交仍是 STATE 中的 `9bdf53ca5aef40b710142ba332849390592f6422`。本快照的提交应使用 `git log -1` 查询，禁止自引用哈希。
