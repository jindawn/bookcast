# Phase 19.3B Dialogue A/B：冻结输入与离线基线

日期：2026-09-26。当前仅完成离线准备；新候选尚未调用真实 DeepSeek。TTS 与生产路由冻结。Consistency 实验在 Dialogue 稳定 checkpoint 后独立开展。

## 实施顺序

1. 对照现有 `content.py`、`generation.py`、兼容适配器和 [DeepSeek 官方 thinking 文档](https://api-docs.deepseek.com/guides/thinking_mode/) 核对参数。
2. 从 Phase19.2 的 56.46 秒合成 E2E 冻结两次 Dialogue 的 prompt、输入哈希、source/chapter/book synthesis、claim tree、segment、上段真实收尾、schema、输出和 usage；运行离线质量门禁。
3. B 只改 `thinking.type=disabled`，保留原 prompt 与 DeepSeek Flash；C 保留原默认 thinking，只缩短 dialogue instruction，保留所有资料、schema、事实 grounding 和角色约束。各段最多一次调用，失败留下 receipt，禁止自动重采样。
4. 比较两段汇总和逐段质量；如果任一候选形态失败，停止其后续调用；通过形式门禁后仍须人工审查事实忠实与自然度。再建立 Dialogue checkpoint，进入 Consistency。

## 参数与固定输入

现有真实 E2E 的 `generation` 为 null，故此 Baseline 是 DeepSeek 默认推理，不是 `bookcast-v1` 的 low。`GenerationConfig` 支持 `thinking.enabled/disabled` 与 `reasoning_effort.low/high/max`；`low` 已是官方最低启用强度。B 选择官方 Chat Completions 的 `{"thinking":{"type":"disabled"}}`，不发送不存在的 budget 参数。C 不发送生成参数，只修改 JSON prompt 的 `instruction` 字段；prompt 中的其余字段逐字相同。以上为参数**兼容性**结论，不是候选质量结论。

夹具：[phase19_3b_dialogue.json](../../tests/fixtures/phase19_3b_dialogue.json)。两个原 prompt 的 `fingerprint({prompt,schema})` 与原 Job manifest 的 `input_hash` 完全一致：

| Segment | 原 input_hash | Baseline 字数/turns | Claim ID coverage |
| --- | --- | ---: | ---: |
| 0001 | `c8290791a1f613c067dda206cb800f538c64c5bf4960cd5531c07439e9498a8b` | 133/5 | 75.0% |
| 0002 | `77601e94ca7296765609cd7cc31fe5c9a89ee24be5aaed74d641f7c6cc00b330` | 119/5 | 87.5% |

`chapter_synthesis` 与 `book_synthesis` 在夹具中是**冻结上下文**：实际 Dialogue prompt 使用 plan/claims/上段收尾，未把完整综合结果再次发送。不能把未进入 prompt 的资产说成模型直接输入。所用短书是仓库 E2E 自制文本；没有私有书稿或密钥进入夹具。

## 已实测 Baseline（Phase19.2 资产，未重复付费）

| 指标 | Dialogue A |
| --- | ---: |
| 调用数 | 2 |
| input tokens | 3,353 |
| output tokens | 6,777 |
| reasoning tokens | 6,099 |
| reasoning/output ratio | 90.0% |
| 延迟 | 25.66 秒（原 E2E 汇总，非本次重测） |
| 估算成本 | 0.0302 元，实际账单未知 |
| 生成字数/turns | 252 / 10 |
| schema | 两次有效 |
| 未知 Claim ID / 未支持数字 | 0 / 0 |
| 20字窗口重复率 | 两段均 0 |
| 质量 | 本地报告 `needs_review`，存在语义 `unverifiable`；不能认定事实完全通过 |

原 episode：LLM 估算 0.0666 元，按修正后 Qwen TTS 估算 0.0200 元，**总估算 0.0866 元**；两项均非供应商最终账单。基于 56.46 秒直接乘以 1500/56.46，25 分钟总成本约 **2.300 元**，这只是线性外推，长节目调用数、缓存与音频长度都可能非线性变化，不能当报价。

## 候选结果与证据等级

| 候选 | 唯一主变量 | 新请求 | reasoning / latency / cost | 质量门禁 |
| --- | --- | ---: | --- | --- |
| A Baseline | 已保存生产行为 | 0 | 上表：已实测历史值 | 需要人工语义/自然度核验 |
| B Reduced Reasoning | `thinking.type=disabled` | 0 | 未测，不填降幅 | 未测 |
| C Prompt-focused | 仅缩短 instruction | 0 | 未测，不填降幅 | 未测 |

目前 reasoning/latency/estimated cost 降幅、false positives/negatives、B/C 的新 episode 成本与25分钟投影均**不可计算**。不能用0当作候选结果，也不能宣布 Winner。确定性门禁会校验 schema、segment_id、speaker、claim ID、数字引用、字数偏离、重复；语义忠实和对话机械感需要人工审查，现有基线一致性复核本身有 `unverifiable`。

## 继续命令

```sh
.venv/bin/python scripts/llm_reasoning_dialogue.py
.venv/bin/python -m pytest -q tests/test_llm_reasoning_dialogue.py
# 仅在 DEEPSEEK_API_KEY 已在同一进程环境安全配置后，且人工核对夹具：
.venv/bin/python scripts/llm_reasoning_dialogue.py --candidate reduced --live
.venv/bin/python scripts/llm_reasoning_dialogue.py --candidate focused --live
```

runner 不运行 Pipeline/TTS；每候选最多两个短 Dialogue 请求，输出到忽略目录 `output/llm-reasoning-ab/dialogue/`。开始请求前保存 receipt，若已有未完成/失败 receipt 则拒绝自动重复；物理请求写同目录 `physical_requests.jsonl`。检查 `*-metrics.json`、两个响应的 schema 与人工逐句比对后，再生成实测候选报告。当前 Codex 进程没有有效 DeepSeek key，因此本次仅执行离线部分。

候选 metrics 会聚合 input/output/reasoning tokens、reasoning ratio、延迟、估算成本、字数和轮数；供应商未返回的用量保留为 null，不以零代替。候选完成后还需依据逐轮输出进行人工语义和自然度审查，再计算相对 Baseline 的降幅与 episode/25分钟投影。
