# Phase 19.3D Live Canary：CANARY_ACCEPTED（2026-09-28）

仅执行一次授权的 3 分钟 Job，Job ID `e85c2a6dfcbc4e9c9fb122a662fec49d`，产物目录 `output/acceptance-canary-phase19-3d/4ee2619ca81608020c508633`。`manifest.status=completed` / `state=SUCCEEDED`，36 个步骤完成、2 个旧 Full consistency 步骤因不在当前计划中跳过（合计 38）；质量报告 `checks_passed`，0 blocking issue。最终音频 `output/acceptance-canary-phase19-3d/4ee2619ca81608020c508633/podcast.mp3`，导出时长 179.72 秒；共 2 段。生产模式明确为 `two_tier`，Full DeepSeek 仅作独立 audit；默认模式仍为 `full`，回退可对**新 Job**省略 Canary 选项，不能覆盖既有 Job 模式。

| 一致性路径 | 请求 | 输入 token | 输出 token | reasoning token | 延迟 | 成本 CNY |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Qwen Tier1（生产） | 2 | 2291 | 56 | 未报告 | 1.677s | 0.0024 |
| targeted DeepSeek（生产） | 0 | 0 | 0 | 0 | 0s | 0 |
| Full DeepSeek（audit） | 2 | 3481 | 9405 | 8928 | 44.969s | 0.0817 |

两段 Tier1 均 PASS，REVIEW 0、可疑轮次 0，targeted DeepSeek 调用规避 2/2（100%）。两段 Full audit 均为 `supported`，标记风险轮次 0；逐段 verdict agreement 与 flagged-turn agreement 都为 2/2。`potential_false_negative=0`；audit-only warnings 0、two-tier-only warnings 0，audit 成功完成。因为本次均为 PASS，真实 Tier2 升轨路径未由这次 Live Job 覆盖；已有离线/先前受控实验覆盖，不能由本 Job 推断正样本召回。Qwen 未报告 reasoning token，总体 reasoning 降幅不能可靠计算；Full audit 报告的 DeepSeek reasoning 8928 token 在生产一致性路径中降为 0，即 DeepSeek reasoning 规避 100%。

一致性生产 Two-Tier 成本 ¥0.0024；Full audit 成本 ¥0.0817；Canary 双跑实际一致性实验成本 ¥0.0841。仅用 Full 生产的反事实成本 ¥0.0817，仅用 Two-Tier 生产的反事实成本 ¥0.0024；差额 ¥0.0793，节省 97.06%。整 Job LLM ¥0.1568、TTS ¥0.0659、合计 ¥0.2227；双跑审计已包含在 LLM 与整 Job 成本内，未再次加总。价格来自 Job 固化的 `ProvidersConfig.pricing`、`llm_usage.json` 与 `cost_summary.json`；11 次 LLM 用量全部有计价，physical telemetry 只核验请求/延迟，不重复计费。

26 条物理请求全部 HTTP 200 且 `succeeded`：Qwen LLM 6、DeepSeek LLM 5、Qwen TTS 15；`physical_attempt_index` 全为 0，`retry_reason` 全为空。Provider retry 0、transport retry 0、sandbox/infrastructure recovery 0、fallback 0。Audit 无错误，未触发 repair，生产质量门与 Job 状态正常。验收 Gate 八项均通过，决定 **CANARY_ACCEPTED**。本次不改默认配置、不进入后续阶段、不 push。

---

# Phase 19.3D STEP 1–3：Canary 离线实现与最终 Gate

功能 checkpoint：`a4316ae2efa3e741447cbd184141ae78c723b6b4`；最终离线 Gate 测试 checkpoint：`c44bc0badc96e7557a5e46f752136534d10ba604`。没有真实 API、Live Canary、真实 TTS 或 push。默认生产一致性仍为 `full`；旧 Job 未存模式字段时也读取为 `full`。**当前状态：CANARY_IMPLEMENTED / LIVE_READY / LIVE_NOT_RUN。**

## 已实现的离线接线

- `ContentOptions` 固化 `consistency_mode=full|two_tier`、`consistency_canary_audit=false`；恢复时继承已存值，显式变更需新输出目录。旧 `--shadow-consistency` 仍表示 Full production / Two-Tier shadow，不能与新 Canary 模式混用。
- Full 路径仍调用现有 `consistency:{segment}` 任务、原 prompt/schema、评审产物及质量门。Two-Tier 生产路径复用 `shadow_consistency.py` 的初筛契约和定向 prompt：Qwen Tier1 PASS 时直接合成标准 `ConsistencyReview`，REVIEW 时只给 DeepSeek Tier2 发送可疑 source turns 及其引用证据；Tier2 漏项/错项按 schema 错误失败，不静默放行。Tier1/2 通过 `runner.ai_operation` 分别留下持久化 Attempt，任务与产物身份分离。
- Canary 仅在 `two_tier` + `consistency_canary_audit=true` 时执行 Full DeepSeek audit。Audit 使用独立短生命周期 `ProviderChain` 和原 Full prompt，结果只写旁路比较；失败不会禁用生产高质量路由链，不影响生产 verdict、质量门、repair 或 Job 最终成功状态。失败比较记为 `audit_status=failed`、`potential_false_negative=null`。
- 比较同一 segment 当前 script、claims/evidence、revision 的结果。Full audit 的 `contradicted` 或 `unverifiable` 发言未被 Two-Tier 标记时，`potential_false_negative=true`。不完整 audit 为 `indeterminate`。`evaluation/canary_comparison.json` 分别列出 Qwen Tier1、targeted DeepSeek、Full audit 的 usage、reasoning、latency 与价格，并给出 production、audit 反事实、实际实验成本及节省额/比例。仅从 `AIAttempt` 通过 `calculate_cost_summary` 与 Job 保存的 `ProvidersConfig.pricing` 计费；physical telemetry 只供延迟，不重复计费。缺价格/usage 为未知。
- `generate`、`acquire --generate` 增加 `--consistency-mode full|two-tier`、`--consistency-canary-audit`；`jobs resume/retry` 继承 Job 选项。非 Mock Two-Tier 前置校验 cheap 路由仅使用 `qwen3.7-flash`、high_quality 路由仅使用 `deepseek-flash`。

## 离线验收与已解决的旧测试时段依赖

Canary 专项 **16 passed**；先前相关回归 **221 passed**。最后一项旧 Dialogue D 测试原本使用当下时钟，从冻结的 Phase 19.3B 价格快照选峰/离峰价，却与固定历史基线 ¥0.008122 比较；北京时间峰段时模拟成本 ¥0.0102，导致断言随时钟变化。仅在测试中读取已有 Phase 19.3B 请求时间，核验其按同一冻结价格快照复算 A 基线仍为 ¥0.008122，然后固定该测试的计价时钟；未改生产计价、测试的成功阈值或历史 D 结论。

最终顺序验证：失败单测 **1 passed**；Dialogue reasoning/experiment **27 passed**；pricing/cost/usage **105 passed**；Canary **16 passed**；完整离线 pytest 在测试提交 `c44bc0b` 上 **759 passed、1 skipped、7 deselected、10 subtests passed，0 failed**。`python3 -m compileall -q src scripts`、`python3 scripts/validate_project.py`、`git diff --check` 均通过。默认 `full` 的契约由 Canary 专项及相关回归覆盖；离线测试禁止真实网络连接。

## Pre-Live 只读检查与待执行命令

预计使用历史 19.3C 接受样本的两段规划：每段 Qwen Tier1 一次、Full DeepSeek audit 一次；若 Tier1 REVIEW，另有定向 DeepSeek Tier2 一次。因此无 repair/重试时，一致性层预计 **4–6 次**真实请求：Qwen 2、Full audit 2、targeted DeepSeek 0–2。新 Job 的实际规划段数或修复次数可能改变该数量；完整 Job 还会有正常 Dialogue 和 TTS 请求。本轮未执行下面的命令，也未读取/修改未跟踪书稿内容。

严格离线 Gate 已通过；取得用户下一轮明确授权后，唯一 Live 命令为：

```sh
.venv/bin/bookcast generate examples/mind_and_judgment.txt \
  --config examples/model-routing-cloud.toml \
  --output-dir output/acceptance-canary-phase19-3d \
  --mode two_host --minutes 3 \
  --consistency-mode two-tier --consistency-canary-audit
```

**当前不要执行。** 下一步仅等待维护者明确授权真实 Canary；本阶段停止，不自动进入 Live。

回退：新 Job 省略两个 Canary 选项即使用 `full`；现有 Two-Tier Job 不允许原地改模式，应使用新的输出目录。必要时本地审查并撤销功能提交 `a4316ae`，不要改写旧 Job 的 manifest，也不要 push。
