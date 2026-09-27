# Phase 19.3B Dialogue A/B：冻结输入与离线基线

日期：2026-09-26。离线准备已完成；后续针对冻结段 `0001` 对 B/C 各发起了唯一一次真实请求，均被服务端以 HTTP 400 拒绝，未生成候选内容。TTS 与生产路由冻结，Consistency 尚未开始。

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
| B Reduced Reasoning | `thinking.type=disabled` | 1（HTTP 400） | 无 usage / 生成延迟 / 成本数据 | 请求失败，内容不可评估 |
| C Prompt-focused | 仅缩短 instruction | 1（HTTP 400） | 无 usage / 生成延迟 / 成本数据 | 请求失败，内容不可评估 |

目前 reasoning/latency/estimated cost 降幅、false positives/negatives、B/C 的新 episode 成本与25分钟投影均**不可计算**。HTTP 400 的短请求延迟不能作为生成延迟；没有服务端用量，估算收费未知，不能用0当作候选结果，也不能宣布 Winner。确定性门禁会校验 schema、segment_id、speaker、claim ID、数字引用、字数偏离、重复；语义忠实和对话机械感需要人工审查，现有基线一致性复核本身有 `unverifiable`。

## 继续命令

```sh
.venv/bin/python scripts/llm_reasoning_dialogue.py
.venv/bin/python -m pytest -q tests/test_llm_reasoning_dialogue.py
```

runner 不运行 Pipeline/TTS；本轮将冻结输入限制为 `0001`，每候选仅一次请求。输出在忽略目录 `output/llm-reasoning-ab/dialogue/`；开始请求前保存 receipt，已有失败 receipt 会拒绝自动重复。物理请求写同目录 `physical_requests.jsonl`。本轮**不得再次运行上述 `--live` 命令**：它们是后续新实验的通用入口，不是本轮继续命令。

候选 metrics 会聚合 input/output/reasoning tokens、reasoning ratio、延迟、估算成本、字数和轮数；供应商未返回的用量保留为 null，不以零代替。候选完成后还需依据逐轮输出进行人工语义和自然度审查，再计算相对 Baseline 的降幅与 episode/25分钟投影。

## 冻结段 0001：本轮单次请求结果

机器可读的统一指标、两份失败 receipt 和两条原始安全物理遥测已保存在 [phase19-3b-dialogue-one-shot.json](phase19-3b-dialogue-one-shot.json)。A 只读取旧 E2E；B/C 各一次请求、0 重试、无 TTS/E2E/Consistency。

| 指标 | A 历史 Baseline（0001） | B thinking off | C prompt-focused |
| --- | ---: | ---: | ---: |
| input / output / reasoning tokens | 1666 / 1614 / 1281 | 未返回 | 未返回 |
| reasoning/output | 79.37% | 不可计算 | 不可计算 |
| 生成耗时 | 约 7.495 秒（旧 Attempt 日志间隔） | 未生成 | 未生成 |
| 估算成本 | 0.008122 元（同价格快照） | 未知 | 未知 |
| 字数 / turns | 133 / 5 | 无输出 | 无输出 |
| schema / Claim coverage | 有效 / 75% | 不可评估 | 不可评估 |
| 未知 Claim ID / 无依据数字 / 重复 | 0 / 0 / 0 | 不可评估 | 不可评估 |
| 历史一致性复核 | 2 supported、1 unverifiable | 不可评估 | 不可评估 |
| 物理请求 | 0（本轮） | 1、HTTP 400、0.153 秒 | 1、HTTP 400、0.111 秒 |
| 质量门禁 | 形式通过，语义待审 | 请求失败，内容不可评估 | 请求失败，内容不可评估 |

B/C 的 reasoning、成本和生成延迟降幅均无法计算；B 是否损伤事实忠实度与自然度、C 是否减少 reasoning、三者质量差异都没有可用证据。两次 HTTP 400 均由适配器归类为不可重试 `input_error`，没有服务端 usage；未保存上游原始错误正文，故无法仅凭现有安全遥测确定具体拒绝原因。本轮遵守一次调用上限，不探测、不重试。下一位接手者应先离线核对模型/端点/请求格式及 API 文档，再由用户重新授权新的真实请求；不要复用本轮失败 receipt 发起隐式重试。

两次请求的完整字段级离线重建、与历史成功请求的差异表，以及响应正文未保存的证据链见 [HTTP 400 离线审计](phase19-3b-http400-audit.md)。审计没有定位可证实的代码根因，因此没有修改实验 runner 或生产 Provider；当前不能安全重跑。
