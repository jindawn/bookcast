# Phase 19.3D STEP 1–3：Canary 离线实现与待验收 Gate

功能 checkpoint：`a4316ae2efa3e741447cbd184141ae78c723b6b4`。本轮没有真实 API、Live Canary、真实 TTS 或 push。默认生产一致性仍为 `full`；旧 Job 未存模式字段时也读取为 `full`。**严格最终 Gate 尚未通过，因此状态不是 LIVE_READY；Live Canary 尚未运行。**

## 已实现的离线接线

- `ContentOptions` 固化 `consistency_mode=full|two_tier`、`consistency_canary_audit=false`；恢复时继承已存值，显式变更需新输出目录。旧 `--shadow-consistency` 仍表示 Full production / Two-Tier shadow，不能与新 Canary 模式混用。
- Full 路径仍调用现有 `consistency:{segment}` 任务、原 prompt/schema、评审产物及质量门。Two-Tier 生产路径复用 `shadow_consistency.py` 的初筛契约和定向 prompt：Qwen Tier1 PASS 时直接合成标准 `ConsistencyReview`，REVIEW 时只给 DeepSeek Tier2 发送可疑 source turns 及其引用证据；Tier2 漏项/错项按 schema 错误失败，不静默放行。Tier1/2 通过 `runner.ai_operation` 分别留下持久化 Attempt，任务与产物身份分离。
- Canary 仅在 `two_tier` + `consistency_canary_audit=true` 时执行 Full DeepSeek audit。Audit 使用独立短生命周期 `ProviderChain` 和原 Full prompt，结果只写旁路比较；失败不会禁用生产高质量路由链，不影响生产 verdict、质量门、repair 或 Job 最终成功状态。失败比较记为 `audit_status=failed`、`potential_false_negative=null`。
- 比较同一 segment 当前 script、claims/evidence、revision 的结果。Full audit 的 `contradicted` 或 `unverifiable` 发言未被 Two-Tier 标记时，`potential_false_negative=true`。不完整 audit 为 `indeterminate`。`evaluation/canary_comparison.json` 分别列出 Qwen Tier1、targeted DeepSeek、Full audit 的 usage、reasoning、latency 与价格，并给出 production、audit 反事实、实际实验成本及节省额/比例。仅从 `AIAttempt` 通过 `calculate_cost_summary` 与 Job 保存的 `ProvidersConfig.pricing` 计费；physical telemetry 只供延迟，不重复计费。缺价格/usage 为未知。
- `generate`、`acquire --generate` 增加 `--consistency-mode full|two-tier`、`--consistency-canary-audit`；`jobs resume/retry` 继承 Job 选项。非 Mock Two-Tier 前置校验 cheap 路由仅使用 `qwen3.7-flash`、high_quality 路由仅使用 `deepseek-flash`。

## 离线验收与当前阻塞

Canary 专项 16 passed；content、pipeline 恢复、routing、旧 Shadow、cost/usage、CLI 等相关回归总计 **221 passed**。`python3 -m compileall -q src scripts`、`python3 scripts/validate_project.py`、`git diff --check` 通过；功能提交上专项16项再次通过。完整离线 pytest 两次均只在旧 `tests/test_llm_reasoning_dialogue.py::test_d_success_gate_requires_usage_and_one_receipt` 失败：最终一次为 **757 passed、1 failed、1 skipped、7 deselected、10 subtests passed**。该测试用当前时间给模拟请求计价，并与冻结的历史离峰成本门槛比较；运行时是北京时间峰段。其代码未被本阶段修改。按本轮“失败只修本阶段相关问题”和严格 Gate，未修改旧 Dialogue 测试，也**不宣称 CANARY_IMPLEMENTED / LIVE_READY**。

## Pre-Live 只读检查与待执行命令

预计使用历史 19.3C 接受样本的两段规划：每段 Qwen Tier1 一次、Full DeepSeek audit 一次；若 Tier1 REVIEW，另有定向 DeepSeek Tier2 一次。因此无 repair/重试时，一致性层预计 **4–6 次**真实请求：Qwen 2、Full audit 2、targeted DeepSeek 0–2。新 Job 的实际规划段数或修复次数可能改变该数量；完整 Job 还会有正常 Dialogue 和 TTS 请求。本轮未执行下面的命令，也未读取/修改未跟踪书稿内容。

严格 Gate 全绿并取得用户下一轮明确授权后，唯一 Live 命令为：

```sh
.venv/bin/bookcast generate examples/mind_and_judgment.txt \
  --config examples/model-routing-cloud.toml \
  --output-dir output/acceptance-canary-phase19-3d \
  --mode two_host --minutes 3 \
  --consistency-mode two-tier --consistency-canary-audit
```

**当前不要执行。**下一步先让旧 Dialogue D 测试与时段无关或在可验证的离峰时段重跑完整离线 pytest；只在全绿后更新 Gate 状态，再由维护者单独授权真实 Canary。

回退：新 Job 省略两个 Canary 选项即使用 `full`；现有 Two-Tier Job 不允许原地改模式，应使用新的输出目录。必要时本地审查并撤销功能提交 `a4316ae`，不要改写旧 Job 的 manifest，也不要 push。
