# Phase 19.3C Two-Tier Consistency Shadow Mode Live 验收报告

**日期**：2026-09-27  
**Job ID**：`59efbb777fee45f1bb91d4884b7b4aad`  
**Output Dir**：`output/acceptance-shadow-phase19-3c/4ee2619ca81608020c508633/`  
**验收状态**：✅ **SHADOW_ACCEPTED**

---

## 执行参数

| 项目 | 值 |
|---|---|
| 输入文本 | `examples/mind_and_judgment.txt`（2770 bytes） |
| 配置 | `examples/model-routing-cloud.toml` |
| 模式 | `two_host` |
| 目标时长 | 3 分钟 |
| Shadow Consistency | `--shadow-consistency` |
| 执行约束 | 仅 1 个 Job，无自动重试整体，无多样本，无超时 5 分钟（实际 ~2 分钟） |

---

## 1. Job 完整成功

✅ Job status: `completed`  
✅ 全部 39 个 Steps 均 `completed`  
✅ 生产 verdict 未受 Shadow 影响  
✅ Shadow 逻辑全程静默容错，未阻断主流水线  

---

## 2. 音频输出

| 指标 | 值 |
|---|---|
| 最终音频时长 | **168.66 秒**（约 2 分 49 秒） |
| 格式 | MP3，24000Hz，Mono，96kbps |
| 文件路径 | `podcast.mp3` |
| Segment 总数 | **2**（0001 + 0002） |
| TTS 单元数 | **18** 次 Qwen TTS 请求 |
| TTS 总字符数 | 782 字符 |
| TTS 成功率 | 18/18（0 失败，0 重试） |

---

## 3. Segment 总数

**2 个 Segment**（mind_and_judgment 被分为 2 章/段）

---

## 4–9. Production Full DeepSeek 一致性

| 指标 | Segment 0001 | Segment 0002 | 合计 |
|---|---|---|---|
| 调用次数 | 1 | 1 | **2** |
| input tokens | 2,055 | 1,576 | **3,631** |
| output tokens | 2,045 | 1,305 | **3,350** |
| reasoning tokens | 1,773 | 1,120 | **2,893** |
| latency | 9.045s | 6.122s | **15.167s** |
| estimated cost | 0.0 CNY（unpriced） | 0.0 CNY（unpriced） | **0.0 CNY**（unpriced） |

> [!NOTE] DeepSeek V4 Flash 计价配置缺失（`estimated_cost: null`），无法计算 estimated_cost；actual_experiment_cost 同样标记为 unpriced。这是现有计价层的已知限制，不影响 token/latency 准确性。

---

## 10–16. Two-Tier Shadow 一致性

| 指标 | Segment 0001 | Segment 0002 | 合计 |
|---|---|---|---|
| Qwen Tier1 调用次数 | 1 | 1 | **2** |
| Tier1 PASS 数 | 1 | 1 | **2** |
| Tier1 REVIEW 数 | 0 | 0 | **0** |
| DeepSeek escalation 次数 | 0 | 0 | **0** |
| DeepSeek calls avoided % | 100% | 100% | **100%** |
| Shadow DeepSeek reasoning tokens | 0 | 0 | **0** |
| Shadow Qwen in/out | 1305/28 | 962/28 | **2267/56** |
| Shadow latency | 0.837s | 1.346s | **2.183s** |
| Shadow estimated consistency cost | 0.0 CNY（unpriced） | 0.0 CNY（unpriced） | **0.0 CNY** |

---

## 17–22. Shadow vs Production 对比

| 指标 | 值 |
|---|---|
| reasoning reduction % | **100.0%**（2,893 → 0 tokens） |
| counterfactual cost reduction % | 0.0%（unpriced，无法计算） |
| verdict agreement % | **100.0%**（2/2） |
| flagged-turn agreement % | **100.0%**（2/2） |
| shadow_false_negative_candidate 数量 | **0** |
| shadow-only warnings 数量 | **0** |

---

## 23–26. 可靠性与遥测

| 指标 | 值 |
|---|---|
| Provider retry 数 | **1**（analysis:0001:0001 旧会话残留 sandbox 无网络失败，本次 resume 后自动重试成功） |
| fallback 数 | **0** |
| failed physical requests 数（最终） | **0**（1 次 failed 为旧会话残留，已在 retry 中成功） |
| telemetry 完整 | ✅ physical_requests.jsonl 30 条，llm_usage.json + tts_usage.json 齐全，shadow_consistency_comparison.json 生成 |

---

## 成本口径

### A. Actual Experiment Cost（本次实际双跑）

| 组件 | requests | tokens | estimated cost |
|---|---|---|---|
| Production DeepSeek（全链路） | 5 calls | in:7642 out:17207 reason:15067 | **unpriced** |
| Shadow Qwen Tier1 | 2 calls | in:2267 out:56 reason:0 | **unpriced** |
| Qwen TTS | 18 calls | 782 chars / 160.96s | **unpriced** |
| **合计** | 25 successful calls | — | **unpriced**（DeepSeek & Qwen 计价配置缺失） |

> [!IMPORTANT] 成本均标记为 unpriced，原因：DeepSeek V4 Flash 与 Qwen3.7-Flash 的单价配置在 `provider_config.py` 中未填入，导致 `estimated_cost = null`。这是已知的计价层未完善问题，不影响 token 计量准确性。

### B. Counterfactual Full DeepSeek Production Cost

Full DeepSeek consistency 2 次调用，reasoning 2,893 tokens，unpriced。

### C. Counterfactual Two-Tier Production Cost

Two-Tier Qwen Tier1 2 次调用（规避全部 DeepSeek escalation），reasoning 0 tokens，unpriced。  
**注**：本报告 C 的成本不含双跑 Shadow 开销；Shadow 仅记录比较，不计入假设生产成本。

---

## 验收裁决

| 条件 | 结果 |
|---|---|
| Job 正常完成 | ✅ |
| production verdict 未被 Shadow 影响 | ✅ |
| shadow_false_negative_candidate = 0 | ✅ |
| telemetry 完整 | ✅ |
| Shadow 无架构性异常 | ✅ |

### 🟢 SHADOW_ACCEPTED

---

## 架构保持

- 生产 Router 完全冻结，未修改
- Full DeepSeek 生产一致性路径未改动
- Two-Tier 仍为 PRODUCTION_CANDIDATE，未进入 PRODUCTION_ENABLED
- 未触发 Canary，未 push
- 本次 Shadow 结果仅供记录与比较

---

## 关键文件

- `evaluation/shadow_consistency_comparison.json` — 聚合对比报告
- `evaluation/shadow/0001.json` / `0002.json` — 逐段详细报告
- `evaluation/quality.json` — 生产质量检查（checks_passed）
- `usage/physical_requests.jsonl` — 30 条物理请求遥测
- `usage/llm_usage.json` — LLM 分阶段用量
- `usage/tts_usage.json` — TTS 用量
- `podcast.mp3` — 最终音频（168.66s）
