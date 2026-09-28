# Phase 19.3D STEP 1–3 implementation plan（设计审查，未实现）

基线：STEP 0 pricing telemetry 已在 `0cc5a45` 完成，交接提交为 `d457726`。Phase 19.3C 的 Two-Tier Shadow 已接受；Phase 19.3D 的生产接线和 Live Canary 尚未开始。本文件只固定实施边界，不授权真实请求或修改生产默认行为。

## 配置与执行边界

- 在 `ContentOptions` 增加 `consistency_mode: Literal["full", "two_tier"] = "full"`。Job 创建时固化有效值；旧 manifest 缺少该字段时读取为 `full`。`LLMRouting` 仍只负责 Provider routing，不负责 consistency algorithm selection。
- `ContentFlow` 在当前生成 `ConsistencyReview` 的位置按模式切换：`full` 沿用现有 Full DeepSeek；`two_tier` 执行 Qwen Tier1 初筛与针对可疑发言的 DeepSeek Tier2 复核。两条路径向现有质量门、修复流程提供同一 `ConsistencyReview` 契约。默认 `full` 的行为保持不变。
- 优先复用 `src/bookcast/shadow_consistency.py` 及 Phase 19.3B 已验证的 contract、prompt、models 和 comparison logic，不复制第二套算法。现有 Shadow 直接调用 Provider；生产 Two-Tier 的每个逻辑调用须走 `runner.ai_operation`，保留持久化尝试、恢复与计量语义。Full 与 Two-Tier 使用可区分的任务身份、输入/version 指纹和产物，防止缓存串用。

## Canary 与比较

- Canary 的 Production = Two-Tier；Audit Shadow = Full DeepSeek。Full audit 保存为独立 sidecar，只用于审计比较，不得改变 production verdict、Job 状态、音频或 repair。现有 19.3C Shadow 是 Full production / Two-Tier shadow，方向相反；实施时必须明确区分两者语义。
- `potential_false_negative` 只在同一 segment、同一 input/version 的两份完整结果间计算：Full audit 标记 factual risk，而 Two-Tier 未标记对应风险时计入。比较应基于对应发言/检查项，不能因 Tier2 缺项而默认 `supported`。
- Full audit 缺失或失败时，比较状态必须是 `indeterminate` / `audit_failed`，不能记作 FN=0，也不能据此宣告 Canary 通过。

## CLI、telemetry 与 pricing

- `generate` 和触发生成的 `acquire` 提供 `--consistency-mode full|two-tier`、`--consistency-canary-audit`，仅作为配置 override。内部模式值为 `two_tier`；CLI 负责映射。`resume/retry` 默认继承 Job 已固化的模式与 audit 设置，不得因 CLI 默认值静默覆盖。
- 复用 `ProvidersConfig.pricing`、`runner.ai_operation`、AI/physical telemetry、`usage_snapshot` 和 `calculate_cost_summary`。不新增独立价格公式。Production 与 audit 的真实请求都要可追溯；报告区分主流程、audit 与反事实成本，同一请求只能计费一次。价格或用量缺失应保持未知，不以零代替。

## 预计文件与实施顺序

预计修改 `src/bookcast/content_models.py`、`src/bookcast/pipeline.py`、`src/bookcast/content.py`、`src/bookcast/shadow_consistency.py`、`src/bookcast/cli.py` 及相关 targeted tests。`src/bookcast/provider_config.py` 原则上无需修改。

1. A. mode schema 与 legacy manifest 兼容。
2. B. Two-Tier production path。
3. C. Full audit sidecar。
4. D. comparison / potential FN。
5. E. CLI overrides。
6. F. telemetry / pricing。
7. G. targeted tests。
8. H. full offline regression。

主要回归风险：Full/Two-Tier 缓存串用；Tier2 漏项错误变成 `supported`；audit failure 被误统计为 zero FN；resume 覆盖已固化模式；Full audit 与 production 重复计费；旧 Shadow 方向与新 Canary 方向混淆。实施时针对这些边界验证，且保持默认 Full 与既有 Job 恢复行为。
