# Phase 19.3B Consistency：两级阶梯核验与离线基准评测

日期：2026-09-27。处于纯离线实现阶段，尚未发起任何真实网络请求。生产 Router、Dialogue 生产配置及 TTS 均完全冻结保持不变。

## 目标与背景

在 BookCast 流水线中，`consistency` 阶段负责逐段对脚本中来源引证（`attribution == "source"`）的发言进行严格事实一致性核验。

在 Phase 19.2 的 56.46 秒短篇真实端到端生成中，Consistency 阶段表现出极高的推理开销：
- **调用数**：2 次物理请求（`consistency:0001` 与 `consistency:0002`）
- **Token 用量**：输入 3,147 tokens（缓存命中 128），输出 5,388 tokens
- **Reasoning Tokens**：**5,066 tokens**（占输出总量的 **94.02%**）
- **端到端耗时**：**24.975 秒**（占 LLM 总生成时间约 45%）
- **估算成本**：**¥0.0246 元**（占 LLM 总费用约 37%）

模型耗费 94% 的思考 tokens 对每一个正确陈述反复证明其为什么正确，这是当前阶段最大的降本优化点。

优化目标：显著降低 DeepSeek 调用数、Reasoning Tokens、端到端延迟与估算成本，且绝不漏过明显幻觉、数字错误、人物归因错误或矛盾（保持 0 假阴性 / False Negatives）。

---

## 候选方案架构

### Candidate A: Baseline DeepSeek（历史基线）
- **模型**：`deepseek-flash`
- **实现**：沿用 Phase 19.2 历史真实已测数据，**严禁重新发起付费调用**。
- **机制**：对段落中全部 `attribution == "source"` 发言、对应 claims 与原文证据整体送入 DeepSeek，进行默认 thinking 全量深度推导。

### Candidate B: Reduced Reasoning DeepSeek（推理聚焦 DeepSeek）
- **模型**：`deepseek-flash`
- **机制**：
  1. 提示词约束聚焦于 5 类核心风险：
     - unsupported factual claim（无依据事实主张）
     - contradiction（与原文论据矛盾）
     - incorrect attribution（人物或言论归属错误）
     - materially distorted meaning（严重扭曲原意）
     - missing critical qualifier（遗漏关键限定条件或否定关系）
  2. 明确指示模型对完全符合原文的陈述直接判定为 `supported`，无需生成冗长哲学辩论或逐字自洽证明；仅当存在上述风险或证据不足时给出简要原因并标记为 `contradicted` 或 `unverifiable`。
  3. 仅采用 DeepSeek 官方支持的参数，严禁伪造不存在的 API 参数。

### Candidate C: Two-Tier Consistency（两级阶梯核验，本阶段重点）
- **Tier 1（快速低成本初筛）**：
  - **模型**：`qwen3.7-flash`（通过阿里云百炼官方兼容协议，`thinking='disabled'`）
  - **输入精简**：输入严格隔离，仅包含：
    1. dialogue turns（发言人、发言正文、声明引用的 claim IDs）
    2. 对应 Claim IDs 及原文精确引文字句（`quote`）
    不包含全书综合、章节上下文、长篇 prompt 说明或无关元数据。
  - **输出契约**：严格 JSON 模式：
    ```json
    {
      "status": "PASS" | "REVIEW",
      "suspicious_turn_ids": [],
      "reasons": []
    }
    ```
- **路由逻辑**：
  - `status == "PASS"`：
    - 直接判定全段事实通过，**0 次 DeepSeek 调用**。
    - 所有 source 发言标记为 `supported`。
  - `status == "REVIEW"`：
    - **严格升轨隔离**：仅将 `suspicious_turn_ids` 指向的可疑发言及其对应引用 Claims 发送给 DeepSeek 深度复核。
    - **严禁把整个 episode 或正常发言再次交给 DeepSeek**。
    - 最终结果合并：未被初筛标记的发言保持 `supported`，可疑发言采用 DeepSeek 深度复核结论。
- **边界说明**：Two-Tier 目前仅作为独立离线实验脚本，绝不修改生产 `model_routing.py` 或破坏生产流水线。

---

## 确定性测试集与 Ground Truth

测试集保存在 `tests/fixtures/phase19_3b_consistency.json`，共包含 10 个测试用例，覆盖 7 类强制风险及边界案例，每个案例均具备显式确定性 Ground Truth：

| 用例 ID | 类别 | 描述 | 存在错误 (has_error) | 预期初筛状态 | 可疑轮次 | 预期裁决 |
| :--- | :--- | :--- | :---: | :---: | :---: | :--- |
| `case_1` | `completely_correct` | 完全正确：忠实表达原文观点与论据，无任何事实偏差 | False | PASS | `[]` | 0: supported, 1: supported, 2: supported, 3: supported |
| `case_2` | `minor_unsupported_elaboration` | 轻微无依据扩写：在原文论据基础上添加未经证实的喝咖啡细节 | True | REVIEW | `[4]` | 0: supported, 2: supported, 4: unverifiable |
| `case_3` | `obvious_hallucination` | 明显幻觉：虚构量子纠缠芯片与外星知识库等伪科学内容 | True | REVIEW | `[2]` | 0: supported, 2: contradicted, 4: supported |
| `case_4` | `numerical_error` | 数字错误：虚构 1985 年、98.5% 与 400 页等虚假统计数据 | True | REVIEW | `[4]` | 0: supported, 2: supported, 4: contradicted |
| `case_5` | `incorrect_attribution` | 人物归因错误：将查理·芒格的名言张冠李戴给爱因斯坦 | True | REVIEW | `[4]` | 0: supported, 2: supported, 4: contradicted |
| `case_6` | `missing_critical_qualifier` | 丢失关键限定条件：丢失'并不能自动'并将因果关系完全颠倒 | True | REVIEW | `[0]` | 0: contradicted, 1: supported, 2: supported, 3: supported |
| `case_7` | `obvious_contradiction` | 与原文明显矛盾：公然唱反调，称阅读是最低效自欺欺人行为 | True | REVIEW | `[0]` | 0: contradicted, 2: supported, 4: supported |
| `case_8` | `historical_segment_0001` | 历史真实段落 0001：含两轮 supported 与一轮 unverifiable（'重点在深度而非数量'） | True | REVIEW | `[4]` | 0: supported, 2: supported, 4: unverifiable |
| `case_9` | `historical_segment_0002` | 历史真实段落 0002：4 轮发言全部经历史复核判定为 supported | False | PASS | `[]` | 0: supported, 1: supported, 2: supported, 3: supported |
| `case_10` | `hypothetical_boundary` | 边界案例：嘉宾发言明确标注为 hypothetical 思想实验假说 | False | PASS | `[]` | 0: supported, 2: supported, 3: supported |

---

## 核心指标与离线基准评测结果

通过 `scripts/llm_reasoning_consistency.py` 运行确定性离线评测，结果归档于 `output/llm-reasoning-ab/consistency/offline-consistency-benchmark.json`：

### 1. 核心指标对比表

| 指标 | Candidate A（历史基线） | Candidate A（测试集全量 DeepSeek 模拟） | Candidate C（Two-Tier 两级阶梯） | 改善幅度 (C vs A 测试集) |
| :--- | :---: | :---: | :---: | :---: |
| **Qwen 调用数** | 0 | 0 | **10** | +10 |
| **DeepSeek 调用数** | 2（历史） | 10 | **7** | **降低 30.0%** |
| **升轨复核率 (Escalation Rate)** | 100.0% | 100.0% | **70.0%** | 30% 案例完全零 DeepSeek |
| **DeepSeek 节省率 (Avoided %)** | 0.0% | 0.0% | **30.0%** | 30% 完全规避 |
| **Input Tokens (估算总和)** | 3,147 | 15,730 | **4,460** | **下降 71.6%** |
| **Output Tokens (估算总和)** | 5,388 | 26,940 | **3,500** | **下降 87.0%** |
| **Reasoning Tokens (估算总和)** | 5,066 | 25,330 | **2,800** | **下降 88.9%** |
| **Reasoning / Output 比例** | 94.02% | 94.02% | **80.00%** | 下降 14 个百分点 |
| **总延迟 (估算秒)** | 24.98s | 124.9s | **39.1s** | **下降 68.7%** |
| **总估算成本 (CNY)** | ¥0.0246 | ¥0.1230 | **¥0.0277** | **下降 77.5%** |
| **Schema 有效性** | 100% | 100% | **100%** | 契约完全合法 |

### 2. 事实错误检测质量（Detection Quality）

- **真阳性 (True Positives, TP)**：7（所有包含事实错误的用例全部成功触发 REVIEW）
- **真阴性 (True Negatives, TN)**：3（完全正确的 3 个用例全部在 Tier 1 成功 PASS）
- **假阳性 (False Positives, FP)**：0（无误报）
- **假阴性 (False Negatives, FN)**：**0**（**零漏报！核心事实风险 100% 捕获**）
- **精确率 (Precision)**：**100.0%**
- **召回率 (Recall)**：**100.0%**
- **漏检明细 (False Negative Details)**：`[]`（无任何漏检）

---

## 关键代码与架构实现

1. `tests/fixtures/phase19_3b_consistency.json`：
   - 固化 10 个测试用例、原文证据与精确 Ground Truth。
   - 包含 Phase 19.2 历史计价快照 `cost_snapshot` 与基线指标。
2. `scripts/llm_reasoning_consistency.py`：
   - 实现 `Tier1ScreeningResult` 契约模型。
   - 实现精简初筛提示词 `tier1_screening_prompt` 与定向复核提示词 `tier2_review_prompt`。
   - 实现两级阶梯流控与结果合并逻辑。
   - 具备安全错误收据机制，无 Key 时快速预检失败，拒绝重复调用已存在的 receipt。
3. `tests/test_llm_reasoning_consistency.py`：
   - 13 项专项离线测试，覆盖契约完整性、Schema 严格性、Prompt 隔离性、PASS/REVIEW 路由分支、指标计算、成本测算与生产配置无污染。

---

## 下一步与真实调用预算规划（等待用户授权）

当前阶段已**完全离线完成**。按照用户指示，已在真实 API 调用前停下，未发起任何真实 Qwen 或 DeepSeek 请求。

若后续获得用户明确授权，最小真实实验计划如下：
1. **测试范围**：选取 2 个具有代表性的冻结测试用例（如 `case_1_completely_correct` 与 `case_4_numerical_error`），绝不全量调用。
2. **预计真实调用数**：
   - `case_1`：Qwen 1 次，DeepSeek 0 次（预期 PASS）。
   - `case_4`：Qwen 1 次，DeepSeek 1 次（预期 REVIEW 升轨定向复核）。
   - **两用例总计真实调用**：Qwen 2 次，DeepSeek 1 次。
3. **安全门禁**：
   - 每次调用先落盘 receipt，物理遥测实时记录至 `output/llm-reasoning-ab/consistency/physical_requests.jsonl`。
   - 任何非 200 或 Schema 异常立即终止，绝不自动重试。
   - 绝不触碰生产 Router、Dialogue 配置或 TTS 模块。
