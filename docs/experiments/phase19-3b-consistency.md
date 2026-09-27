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

---

## 最小真实 Live Smoke 实验结果（2026-09-27）

在获得用户明确授权后，执行了针对 2 个冻结用例的最小受控真实请求（Live Smoke），总物理请求严格限制为 3 次，无任何自动重试。结果归档于 `output/llm-reasoning-ab/consistency/live-smoke-20260927/`：

### 1. 逐用例执行明细

#### Case 1: `case_1_completely_correct`（完全正确负样本）
- **Tier 1 (Qwen3.7-Flash)**：
  - HTTP 状态：`200`（耗时 0.915s，0 重试）
  - 初筛结论：`status = "PASS"`，`suspicious_turn_ids = []`，`reasons = []`
  - Token 用量：输入 964，输出 28，Reasoning 未启用，缓存命中 0
  - 估算成本：¥0.001020
  - Schema 校验：有效（Pydantic 严格校验通过）
- **Tier 2 (DeepSeek-Flash)**：
  - **0 次调用（直接跳过，DeepSeek 规避达成 100%）**
- **最终一致性结果**：4 轮发言全部判定为 `supported`，无事实错误。

#### Case 4: `case_4_numerical_error`（数字错误正样本）
- **Tier 1 (Qwen3.7-Flash)**：
  - HTTP 状态：`200`（耗时 2.498s，0 重试）
  - 初筛结论：`status = "REVIEW"`，`suspicious_turn_ids = [4]`（精准锁定 turn 4，无多余嫌疑轮次）
  - 初筛理由：指出 turn 4 虚构了 1985 年权威调研、98.5% 富豪及 400 页阅读量等原文不存在的数值与归属
  - Token 用量：输入 934，输出 128，缓存命中 0
  - 估算成本：¥0.001190
- **Tier 2 (DeepSeek-Flash，定向深度复核)**：
  - HTTP 状态：`200`（耗时 1.510s，0 重试）
  - 发送轮次：**仅 1 轮**（`turn_4`，严禁发送完整 episode）
  - 发送 Claims：仅与 turn 4 关联的 2 条引据（`0001:0001:arguments:1` 与 `0001:0001:evidence:0`）
  - 复核裁决：`verdict = "contradicted"`
  - 复核理由：“原文仅称芒格表示杰出的人几乎都坚持每日深度阅读；发言中的‘1985年权威调研’‘98.5%’‘每天400页以上’‘杰出富豪’等具体数字与出处均无原文依据，属数字与归属错误。”
  - Token 用量：输入 781（缓存命中 128），输出 213，**Reasoning Tokens 118**（对比 Baseline 2,500+ 大幅缩减）
  - 估算成本：¥0.001508
  - Schema 校验：有效（ConsistencyReview 严格校验通过）
- **最终一致性结果**：第 0、2 轮保持 `supported`，第 4 轮判定为 `contradicted`，成功检出数字与归属事实错误。

### 2. Live Smoke 统计与指标汇总

| 指标项 | 本次 Live Smoke 实际观测值 | 说明 |
| :--- | :---: | :--- |
| **Qwen3.7-Flash 物理请求数** | **2** | Case 1 与 Case 4 初筛各 1 次，HTTP 200 |
| **DeepSeek-Flash 物理请求数** | **1** | 仅 Case 4 升轨复核 1 次，HTTP 200 |
| **总真实物理请求数** | **3** | 严格达到上限，0 次自动重试，0 失败 |
| **实际升轨复核率 (Escalation Rate)** | **50.0%** (1/2) | Case 1 PASS，Case 4 REVIEW |
| **DeepSeek 调用规避率 (Avoided %)** | **50.0%** (1/2) | Case 1 完全免除 DeepSeek 调用 |
| **真阳性 (TP)** | 1 | Case 4 成功检出 |
| **真阴性 (TN)** | 1 | Case 1 成功放行 |
| **假阳性 (FP)** | 0 | 无无病呻吟的误报 |
| **假阴性 (FN)** | **0** | **零漏报！未漏过数字错误** |
| **Live 样本精确率 (Precision)** | 100.0% | 仅限本次 2 个真实样本 |
| **Live 样本召回率 (Recall)** | 100.0% | 仅限本次 2 个真实样本 |
| **总耗时 (请求间隔总和)** | 4.923s | Qwen 0.915s + 2.498s，DeepSeek 1.510s |
| **总估算成本 (CNY)** | **¥0.003718** | Case 1 ¥0.001020 + Case 4 ¥0.002698 |

### 3. 证据链与边界声明（防过度外推）

- **独立物理遥测证据**：见 `output/llm-reasoning-ab/consistency/live-smoke-20260927/physical_requests.jsonl`，包含 3 条完整脱敏记录（http_status 200、attempt 0、latency、endpoint、tokens）。
- **独立单次收据**：见 `twotier-case_1_completely_correct.json` 与 `twotier-case_4_numerical_error.json`。
- **严禁过度外推**：
  > [!IMPORTANT]
  > 本次 Live Smoke 仅针对 2 个精心构造的极限/典型用例（`case_1` 与 `case_4`）进行了最小可行性概念验证。**严禁将本次 2 个样本的评测结果外推为“两级阶梯真实全量召回率达到 100%”或“生产成本必定下降 77.5%”**。离线基准评测（10 个用例）与 Live Smoke 评测（2 个用例）必须严格分开记录。
- **生产隔离性**：生产 `model_routing.py`、生产 Dialogue 配置、TTS 模块完全保持冻结，未作任何侵入式修改。

---

## Live Validation Set 实验结果与生产候选 Gate 评估（2026-09-27）

在最小 Live Smoke（2 样本）验证通过后，按照受控流程执行了包含 6 个典型用例的 Live Validation Set，目标深入验证 Tier 1（Qwen3.7-Flash）是否会对不同类型事实风险发生漏检（Zero False Negatives）。

### 1. 受控请求与硬上限执行情况

- **测试集构成（6 个冻结用例）**：
  - 负样本（2 个）：`case_1_completely_correct`（完全正确）、`case_10_hypothetical_boundary`（思想实验/假说边界）
  - 正样本（4 个）：`case_3_obvious_hallucination`（伪科学幻觉）、`case_5_incorrect_attribution`（张冠李戴归因）、`case_6_missing_critical_qualifier`（丢失否定条件/因果颠倒）、`case_7_obvious_contradiction`（公然反向唱反调）
- **复用机制**：`case_1_completely_correct` 直接复用 Live Smoke 真实结果（0 次新物理请求）；`case_4_numerical_error` 保持历史完成状态，不重复调用。
- **物理请求审计**：
  - 新增 Qwen3.7-Flash 请求：**5 次**（`case_10`, `case_3`, `case_5`, `case_6`, `case_7`）
  - 新增 DeepSeek-Flash 定向复核请求：**4 次**（仅 4 个正样本触发 REVIEW 后升轨）
  - **总新增物理请求数**：**9 次**（**严格达到硬上限 9 次，0 次自动重试，0 次失败**）
  - 物理遥测落盘：`output/llm-reasoning-ab/consistency/live-validation-20260927/physical_requests.jsonl`
  - 逐用例收据：`output/llm-reasoning-ab/consistency/live-validation-20260927/twotier-*.json`

### 2. Live Validation 逐用例执行明细表

| 用例 ID | 类别 | Ground Truth 风险 | Qwen Tier 1 状态 | 可疑轮次定位 | 升轨复核 | DeepSeek 裁决 (仅可疑轮) | 最终一致性裁决 | 是否正确 |
| :--- | :--- | :---: | :---: | :---: | :---: | :--- | :--- | :---: |
| `case_1_completely_correct` | completely_correct | 无错误 (has_error: False) | **PASS** (复用) | `[]` | 否 (0 调用) | - (免除调用) | 0..3: supported | ✅ 正确 (TN) |
| `case_10_hypothetical_boundary` | hypothetical_boundary | 无错误 (has_error: False) | **PASS** (0.911s) | `[]` | 否 (0 调用) | - (免除调用) | 0,2,3: supported | ✅ 正确 (TN) |
| `case_3_obvious_hallucination` | obvious_hallucination | 幻觉 (turn 2: 量子纠缠芯片/外星知识库) | **REVIEW** (3.597s) | `[2]` (精准) | 是 (1.033s) | turn 2: `contradicted` (Reasoning: 64) | 0,4: supported<br>2: contradicted | ✅ 正确 (TP) |
| `case_5_incorrect_attribution` | incorrect_attribution | 归因错误 (turn 4: 芒格名言冠给爱因斯坦) | **REVIEW** (1.707s) | `[4]` (精准) | 是 (1.847s) | turn 4: `contradicted` (Reasoning: 309) | 0,2: supported<br>4: contradicted | ✅ 正确 (TP) |
| `case_6_missing_critical_qualifier` | missing_critical_qualifier | 否定丢失 (turn 0: 宣称被动吸收自动变智慧) | **REVIEW** (2.394s) | `[0]` (精准) | 是 (1.895s) | turn 0: `contradicted` (Reasoning: 130) | 1,2,3: supported<br>0: contradicted | ✅ 正确 (TP) |
| `case_7_obvious_contradiction` | obvious_contradiction | 严重矛盾 (turn 0: 抨击阅读为最被动自欺欺人) | **REVIEW** (2.280s) | `[0]` (精准) | 是 (2.224s) | turn 0: `contradicted` (Reasoning: 262) | 2,4: supported<br>0: contradicted | ✅ 正确 (TP) |

### 3. 统计汇总与混淆矩阵

```
                  Ground Truth Positive     Ground Truth Negative
Detected Positive         4 (TP)                    0 (FP)
Detected Negative         0 (FN)                    2 (TN)
```

- **真阳性 (TP)**：**4**（伪科学幻觉、人物归因错误、否定限定丢失、反向矛盾 100% 检出）
- **真阴性 (TN)**：**2**（完全正确与思想实验边界案例 100% PASS 放行）
- **假阳性 (FP)**：**0**（负样本无一误报，特异度 Specificity = 100.0%）
- **假阴性 (FN)**：**0**（**零漏检！核心事实风险检出召回率 Recall = 100.0%**）
- **精确率 (Precision)**：**100.0%** (4/4)
- **召回率 (Recall)**：**100.0%** (4/4)
- **可疑轮次定位精确度 (Turn Localization Accuracy)**：
  - 精确匹配率 (Exact Match Rate)：**100.0%** (4/4 错误轮次集合与初筛嫌疑集合完全一致，无多标、无漏标)
  - 覆盖率 (Covered Rate)：**100.0%** (4/4)
- **调用流控表现**：
  - 升轨复核率 (Escalation Rate)：**66.7%** (4/6)
  - DeepSeek 调用规避率 (Avoided %)：**33.3%** (2/6 案例完全 0 DeepSeek 调用)
  - 负样本 DeepSeek 规避率：**100.0%** (2/2 负样本均未发起 Tier 2 调用)
- **推理 Token 与耗时**：
  - DeepSeek 平均 Reasoning Tokens：**191.25 tokens**（相较 Baseline 2500+ 下降 **92.4%**）
  - 本轮 9 次新增物理请求总耗时：**17.888s**（平均单次请求约 1.98s）
  - 本轮 9 次新增物理请求总成本：**¥0.011522**（Qwen ¥0.005065 + DeepSeek ¥0.006457）
  - 6 个用例全生命周期总估算成本：**¥0.012542**（平均单篇约 ¥0.00209）

### 4. False Negative (FN) 专项深度分析

在本次受控验证中，**FN = 0**。深入分析 4 类典型风险的识别机理：
1. **伪科学幻觉（`case_3`）**：Qwen 初筛精准抓住“量子纠缠芯片”“外星知识库”等完全超出原文范围的荒谬词汇，给出 REVIEW 并定位 turn 2；DeepSeek 仅以 64 reasoning tokens 即确认 contradicted。
2. **人物归因（`case_5`）**：Qwen 初筛明确指出引述名言存在主体错位（芒格被写成爱因斯坦），定位 turn 4；DeepSeek 迅速完成裁决。
3. **否定条件丢失与因果颠倒（`case_6`）**：Qwen 初筛识别出“被动吸收并不能自动转变成智慧”被错误翻转为“只要被动吸收就能自动变成智慧”，指出否定关系严重失真，定位 turn 0；DeepSeek 裁决准确。
4. **与原文核心主张直接唱反调（`case_7`）**：Qwen 初筛捕获“严厉抨击阅读是人类最被动、最低效自欺欺人行为”与原文“拓展认知边界最高效方式”截然相反，定位 turn 0；DeepSeek 裁决准确。
5. **上下文裁剪与定向投喂有效性**：DeepSeek Tier 2 **仅接收被初筛标记的单一发言轮次及对应引述 Claims**，未注入完整 episode，但 4 次定向复核均产出了与全量输入等价甚至更为精准简练的裁决与原因，且推理开销从数千 tokens 缩减至 64~309 tokens，证实了上下文裁剪定向复核的可行性。
6. **假说/思想实验边界（`case_10`）**：剧本中包含嘉宾提出的 `attribution="hypothetical"` 思想实验（“设想一个人读完全部图书却闭门不出”），Tier 1 提示词严格仅抽取 `attribution="source"` 的发言送检，未对假说进行无病呻吟的虚假事实核验，3 轮 source 发言全部顺畅 PASS，证明了边界过滤机制的稳健性。

### 5. 生产候选 Gate (Production Candidate Gate) 逐项判定

| 门禁条件 | 验证指标 / 表现 | 判定结果 |
| :--- | :--- | :---: |
| 1. 本轮新增正样本 FN = 0 | 4 个正样本全部被检出，FN = 0，Recall = 100.0% | **PASS** |
| 2. 四类典型错误全部送入 REVIEW | 幻觉、归因、限定词、矛盾 4 类全部返回 REVIEW 并精准定位 turn_index | **PASS** |
| 3. DeepSeek 定向复核未受裁剪干扰 | 4 次定向复核裁决（100% contradicted）均符合 Ground Truth，原因详实准确 | **PASS** |
| 4. 负样本未出现严重误报 | case_1 与 case_10 全部 PASS，FP = 0，0 次多余 DeepSeek 调用 | **PASS** |
| 5. Schema 全部有效 | Pydantic 严格模式下，Tier 1 与 Tier 2 所有 JSON 结构校验 100% 通过 | **PASS** |
| 6. 物理遥测与成本审计完整 | 9 条物理记录与收据完整，字段齐备，计价快照核算无遗漏 | **PASS** |

### 6. 状态标记与生产边界

- **架构状态**：由于 6 项门禁条件全部严格满足，Two-Tier Consistency 方案在当前仓库中正式标记为：
  ```
  STATUS: PRODUCTION_CANDIDATE
  ```
- **生产配置不变式**：
  > [!IMPORTANT]
  > 标记为 `PRODUCTION_CANDIDATE` 仅表示两级阶梯核验已具备生产集成的质量与成本资格，**绝不等于已在生产中启用（NOT PRODUCTION_ENABLED）**。
  > 生产 `src/bookcast/model_routing.py`、生产 Dialogue 配置、TTS 模块完全保持原有配置不变，未做任何修改。未来是否合并入生产流水线由维护者审阅决定。
