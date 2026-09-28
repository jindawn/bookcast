# Phase 19 Final RC

**状态：PHASE_19_COMPLETE / RC_READY。** 功能 checkpoint `d1220f78c7f4045dcb480618dff3c1b13dd55c81`；本报告仅记录已完成的离线验证与此前真实受控验收，不发起新模型请求。Phase 19 不再进行模型、Prompt 或架构实验。

## 最终生产策略与兼容边界

| 场景 | consistency 行为 |
| --- | --- |
| 新 Job，未指定模式 | `two_tier`：Qwen3.7-Flash Tier1；PASS 直接形成生产 verdict，REVIEW 经 targeted DeepSeek 后合并标准 `ConsistencyReview` |
| 新 Job，`--consistency-mode full` | 原 Full DeepSeek 路径，作为一键回滚 |
| 新 Job，`--consistency-mode two-tier` | 显式 Two-Tier |
| 旧 manifest 缺 `consistency_mode` | `full`，由 `ContentOptions` 旧字段默认值解释；不改 schema 默认 |
| 已固化 `full` / `two_tier` 的 Job | resume/retry 继承 manifest 中的模式，不静默覆盖 |
| 显式旧 `--shadow-consistency` | 保持 Full production / Two-Tier shadow 的原方向 |

新 Job 的默认值只在 `Pipeline.generate()` **创建 manifest** 时设为 `two_tier`。`consistency_canary_audit` 默认 `false`，正常生产不执行 Full audit 双跑；Full 实现、配置与 CLI override 保留。非 Mock Two-Tier 要求显式 Qwen3.7-Flash cheap 路由和 DeepSeek-Flash high_quality 路由，配置不满足时提前失败；旧配置要继续单 Provider Full 路径，可显式选择 `--consistency-mode full`。Dialogue 仍采用已验证的历史 Baseline，没有启用失败的 B/C/D 实验配置。

## 真实验收证据与成本边界

- Phase 19.3B：Two-Tier Consistency Live Validation PASS，冻结正负样本的 Qwen 初筛与定向 DeepSeek 复核有效；见 [Phase 19.3B 交接](../HANDOFF.md) 与已有实验产物。
- Phase 19.3C：Shadow Mode `SHADOW_ACCEPTED`；2 段生产 Full 与旁路 Two-Tier 裁决一致，见 [Shadow 报告](phase19-3c-shadow.md)。
- Phase 19.3D Natural Canary：`CANARY_ACCEPTED`，2 段 Tier1 均 PASS，潜在假阴性为 0。该单个 3 分钟 Job 的 Two-Tier consistency 成本约 ¥0.0024，Full DeepSeek 反事实约 ¥0.0817，观测节省约 **97.06%**；这个比例不能外推为所有生产任务的固定节省。
- Phase 19.3D Positive Path：`POSITIVE_PATH_ACCEPTED`，冻结数字错误样本触发 Tier1 REVIEW → targeted DeepSeek `contradicted` → 标准生产 `ConsistencyReview` 保留风险；Full audit 也识别同一风险，潜在假阴性为 0。Two-Tier 生产约 ¥0.0045，Full audit 约 ¥0.0090；证明 REVIEW 升轨路径在当前生产集成中真实可用。详见 [Canary 报告](phase19-3d-canary.md)。

成本视图复用 Job 固化的 `ProvidersConfig.pricing`、Attempt journal、`usage_snapshot` 与 `calculate_cost_summary`。Canary audit 成本只计入实际双跑，不与 production 反事实再次相加；physical request telemetry 供次数与延迟核对，不重复计费。Qwen 未报告 reasoning tokens 时，不能宣称整体 reasoning 降幅。

## Final RC 离线 Gate

新增或确认的测试覆盖新 Job 默认、legacy 缺字段、已有 Full/Two-Tier 恢复、CLI 两种显式模式、audit 默认关闭、Tier1 PASS 不调用 DeepSeek、Tier1 REVIEW 定向复核、Full 回滚路径和成本无双计。Two-Tier 合并结果在恢复时若内容相同不重写，保留既有 Job 幂等性。

- 相关 targeted：**183 passed**；Provider/Skill/Web 重点复测：**90 passed**。
- 完整离线 pytest：**763 passed、1 skipped、7 deselected、0 failed，10 subtests passed**。
- `python3 -m compileall -q src scripts`、`python3 scripts/validate_project.py`、`git diff --check` 均通过。
- 本轮没有真实 API、TTS Live 或 push；未跟踪 `examples/mind_and_judgment.txt` 保留且未提交。

## 真正用户实测（仅供用户以后执行，本轮未运行）

```sh
.venv/bin/bookcast generate examples/mind_and_judgment.txt \
  --config examples/model-routing-cloud.toml \
  --output-dir output/user-acceptance-phase19-final \
  --mode two_host --minutes 5
```

这条命令对全新输出目录使用生产默认 Two-Tier，且不启用 Canary audit 或 Full 双跑。若用户需要对**新 Job**回滚，使用同一命令并加 `--consistency-mode full`，同时换用另一个全新输出目录；已有 Job 的固化模式不能在 resume/retry 时覆盖。
