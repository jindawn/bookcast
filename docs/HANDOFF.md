# Phase 19.3B Baseline A 单次诊断与安全错误观察（2026-09-27）

## Completed

在正式Compatible适配器上为DeepSeek非2xx加有界、脱敏的错误观察点，保留原`error_kind`和生产请求构造。单次诊断脚本先写独立receipt，并只调用冻结段0001的正式Adapter一次。相关专项148 passed，validator、compileall与diff check通过。诊断证据见 [phase19-3b-baseline-diagnostic.md](experiments/phase19-3b-baseline-diagnostic.md)。未修改生产Router/TTS。

## Not Completed / Current Result

Baseline A诊断尝试未收到HTTP响应：内部`temporary_unavailable`、`http_status=null`、usage与上游code/message/request_id均未知。故A当前既不能确认为200，也不能确认为400；没有证据确认response_format、model或payload schema有问题。按照条件门禁，没有运行C；B/Consistency亦未运行。不能把这次传输失败当成DeepSeek拒绝payload。

## Last Stable Commit / Exact Next Step

本轮安全观察功能提交见Git日志；交接快照按D-006不自引用。先检查本机执行环境至DeepSeek的网络可达性。此轮已消耗一次Baseline诊断尝试且留下收据，不要自动重发或删除收据；如需再次真实诊断须由用户明确授权。若未来A收到200，才可按用户授权门禁考虑C一次；A若400则停止并根据安全上游错误分析。不得push。

## Files To Read / Commands To Continue

`docs/experiments/phase19-3b-baseline-diagnostic.md`、`src/bookcast/adapters/compatible.py`、`scripts/llm_reasoning_diagnostic.py`、`tests/test_llm_reasoning_dialogue.py`、`docs/STATE.json`。离线命令：`.venv/bin/python -m pytest -q tests/test_llm_reasoning_dialogue.py tests/test_generation.py tests/test_providers.py tests/test_cost_telemetry.py`；`python3 scripts/validate_project.py`；`.venv/bin/python -m compileall src tests scripts -q`；`git diff --check`。

---

# Phase 19.3B Dialogue HTTP400 纯离线审计（2026-09-27）

## Completed

按用户要求没有任何新真实 API 调用。已核对历史成功 E2E 的保存 ProviderSpec、冻结 prompt/schema、B/C 安全 receipt/物理遥测、当前与 E2E 当时 `CompatibleLLMProvider._chat` 实现。字段级证据见 [phase19-3b-http400-audit.md](experiments/phase19-3b-http400-audit.md)：A→B 仅增加 `thinking={type:disabled}`，A→C 仅精简 user instruction；端点、`deepseek-flash` 模型、system schema消息、`json_object`、stream false、120秒超时和其他 body 字段均相同。runner 直接复用正式Adapter，不存在第二套 HTTP payload 组装。新增离线 wire 回归；相关 Dialogue/Provider 测试145 passed，validator/compileall/diff check通过。生产Provider/Router、TTS和Consistency未改。

## Not Completed / Evidence Gap

两次HTTP400的上游 response body、error code、message **未保存**。`classify_http` 读取body仅作分类，之后丢弃；现有安全遥测只保留内部 `input_error`。用户确认无额外控制台或代理脱敏日志。因此**确切根因不能纯离线确认**，也没有可证实的修复；不能说thinking off不支持，不能把C的失败归于Prompt，不能安全重跑B/C。无新实测reasoning/成本/质量数据。

## Last Stable Commit / Exact Next Step

本次已验证离线审计提交 `fe9139ac6653cbefff44e881829aea5297e7d927`；交接快照按 D-006 不自引用。下一步先尝试找回旧请求的可信**脱敏**上游 code/type/message（当前没有）；或在用户另行授权后，设计独立、有界的诊断请求及安全错误观察点。未取得证据前不修改生产或实验wire、不重跑失败receipt、不进入Consistency。保持 `codex/phase19-clean-history`，不push。

## Files To Read / Commands To Continue

`docs/experiments/phase19-3b-http400-audit.md`、`docs/experiments/phase19-3b-dialogue-one-shot.json`、`scripts/llm_reasoning_dialogue.py`、`src/bookcast/adapters/compatible.py`、`tests/test_llm_reasoning_dialogue.py`、`docs/STATE.json`。离线命令：`.venv/bin/python -m pytest -q tests/test_llm_reasoning_dialogue.py tests/test_generation.py tests/test_providers.py tests/test_cost_telemetry.py`；`python3 scripts/validate_project.py`；`.venv/bin/python -m compileall src tests scripts -q`；`git diff --check`。不要执行`--live`。

---

# Phase 19.3B Dialogue 单次请求检查点（2026-09-26）

## Completed

- 本轮只使用冻结段 `0001`。A 沿用历史真实 E2E，0 新请求：input 1666、output 1614、reasoning 1281，历史 Attempt 日志间隔约7.495秒，同价快照估算0.008122元，133字/5轮，Claim coverage 75%，旧一致性复核2 supported、1 unverifiable。
- B（DeepSeek Flash thinking off）与 C（同模型默认thinking、仅精简instruction）各**恰好1次**物理请求、0重试；均 HTTP 400 `input_error`，没有usage、schema结果或生成文本。安全原始receipt、物理遥测和统一指标已保存于 `docs/experiments/phase19-3b-dialogue-one-shot.json`；本机忽略目录 `output/llm-reasoning-ab/dialogue/` 保留原文件。没有存上游错误正文或Key。
- 相关专项96 passed，项目validator及diff check通过；本轮额度保护下未重跑完整离线suite。clean branch最近完整离线713 passed。没有TTS、音频、完整E2E、Consistency、自动调参或生产配置改动，也没有push。

## Not Completed

B/C 均未生成，reasoning/latency/cost降幅、事实忠实、对话自然度、三者质量差异和episode/25分钟新投影均**不可计算**。HTTP 400 的短请求耗时不是生成延迟，缺usage也不能视作零收费。具体被拒原因未知；适配器只保留安全分类。单次请求预算已经用完，不得自动重试。

## Last Stable Commit

已验证功能提交 `e2c8eac04a5457b3051ca9f4f7baf402fe3b5d44`；交接快照自身按 D-006 不自引用。保持干净历史分支 `codex/phase19-clean-history`；旧 `main` 含历史凭证，绝不可误推。

## Exact Next Step

先**离线**对照现用 `deepseek-flash` 模型、端点、JSON模式和thinking参数与官方当前文档；检查两条安全物理遥测的共同HTTP400路径。不要读取/打印Key或复发真实请求。任何新API探测须用户另行授权并建立新候选/收据，不能覆盖本轮失败数据。用户明确允许后才考虑可用的Dialogue验证，再进入Consistency。

## Files To Read

`docs/experiments/phase19-3b-dialogue.md`、`docs/experiments/phase19-3b-dialogue-one-shot.json`、`tests/fixtures/phase19_3b_dialogue.json`、`scripts/llm_reasoning_dialogue.py`、`src/bookcast/adapters/compatible.py`、`docs/STATE.json`。原E2E中间资产在忽略目录 `output/e2e/a834c024a7d11624be66f19b/`。原始两份失败receipt及`physical_requests.jsonl`在`output/llm-reasoning-ab/dialogue/`。

## Commands To Continue（只读/离线）

`git status --short --branch`；`.venv/bin/python -m pytest -q tests/test_llm_reasoning_dialogue.py tests/test_generation.py tests/test_cost_telemetry.py`；`python3 scripts/validate_project.py`；`git diff --check`。**不要运行 `--live`、TTS或完整E2E。**

## NEXT PHASE（尚未进入）：Phase 19.3B Consistency Experiment

历史基线：2调用、input3147/output5388/reasoning5066、约24.98秒、估算0.0246元；已有原E2E的脚本、Claims和一致性结果，但尚无独立七类Consistency fixture。Reduced Reasoning 候选原拟同DeepSeek模型降低thinking；本轮Dialogue HTTP400提示必须先离线确认参数/端点兼容性。Two-Tier候选原拟Qwen3.7-Flash先给PASS/REVIEW与可疑turn IDs，只有REVIEW才把可疑轮及对应Claims/evidence送DeepSeek。此阶段未实现、未调用，不能改生产默认。

---

# 2026-09-26 Git Secret History Cleanup：干净分支交接

用户暂停全部 Phase19.3B 实验。已在 `codex/phase19-clean-history` 从最新 `origin/main=7f43ad7` 建立新本地祖先链；旧 `main=7473ff8` 原样保留为恢复点，不能直接推送。未 push、未改写旧历史。详见 [CLEAN_HISTORY_2026-09-26.md](CLEAN_HISTORY_2026-09-26.md)。

新分支首个安全功能提交 `18d86c2` 直接重放旧 `main` 已净化的最终跟踪树，创建前树完全相同；R0–R6、19.1、19.2、19.3A、19.3B离线准备与凭证移除的有效代码效果均保留。旧含值提交不是新分支祖先；旧 Gemini 值在新分支全部810个可达 blob 中不存在。其他凭证形似候选均属测试或示例，无另一处生产硬编码。

Phase19专项196 passed；完整离线713 passed、1 skipped、7 deselected、10 subtests；validator、compileall和diff检查通过。新分支仅适合显式推送自身，旧main、其他本地分支、`--all`/`--mirror`不在安全结论内。Provider侧轮换仍由维护者完成；用户当前禁止任何 push。

Exact Next Step：等待维护者轮换旧 Gemini Key，并等待用户明确恢复 Phase19.3B。旧main仅作本地恢复点；不要运行DeepSeek B/C、TTS或其他真实API，不要push。

---

# 2026-09-26 凭证安全审计与 Phase 19.3B 暂停

用户已暂停 Phase19.3B 真实 API 实验。按要求优先处理已跟踪 TTS 实验脚本中的疑似硬编码凭证；完整脱敏审计见 [SECURITY_AUDIT_2026-09-26.md](SECURITY_AUDIT_2026-09-26.md)。不运行 DeepSeek B/C、TTS 或音频，不 push、不改写历史。

- 修复前 `scripts/run_tts_ab.py:44` 将一个 53 字符、非明显占位符的值作为 `GEMINI_API_KEY` 回退；在线有效性未知，按可能真实凭证处理。修复已移除字面值，`main()` 在任何 TTS 请求前要求进程环境变量。
- 该值进入本地 `8d6303d1dcc8` 与 `9f3cfbaba9f0` 的脚本版本，后续 HEAD 继承。只读远端 refs 查询中 `origin` 只有 `main`/`HEAD` 指向 `7f43ad7`，早于引入提交；查询时未见远端暴露。旧本地提交仍含该值，不能直接推送含旧提交的分支。建议立即停用/轮换并检查提供方用量。
- 当前已跟踪文件与本地可达历史的跨 Provider 扫描未发现其他类似生产硬编码；测试中若干形似 key 的字符串具有测试上下文/重复顺序模式。`.gitignore` 已覆盖 `.env`，无须新增不被脚本自动加载的 `.env.example`。
- 受影响 TTS/Gemini 回归 71 passed；无真实请求。项目校验、compileall、提交差异检查结果记录在 STATE/WORKLOG。已验证功能提交 `eb9b4605d613311b405ea15b70de06bde8cff57e`；交接快照按 D-006 不自引用。

Exact Next Step：等待维护者轮换疑似凭证并决定旧本地提交的后续处理；Phase19.3B 真实 API 实验仅在用户明确恢复后继续。不要从旧脚本/历史复制该值，不要为检查有效性发起模型调用。

---

# Phase 19.3B Dialogue 离线实验检查点（2026-09-26）

## 目标与现状

用户要求仅优化 DeepSeek dialogue/consistency reasoning 成本，冻结 Phase19.3A TTS 与生产路由。当前完成 Dialogue **离线准备**，尚未执行新候选 B/C 真实调用；当前进程缺少 DEEPSEEK_API_KEY 和 DASHSCOPE_API_KEY。不要把候选降幅或质量写为已验证，也不要重新执行旧 E2E。

## 已完成与关键文件

- `tests/fixtures/phase19_3b_dialogue.json`：冻结自制短书的两次原 dialogue 输入、原输出与历史 usage；重建 prompt 的两个 input_hash 与原 manifest 完全相同。合成资产作为上下文保存，但并未假称完整综合结果进入原 dialogue prompt。
- `scripts/llm_reasoning_dialogue.py`：离线重算历史 A；B 仅使用 DeepSeek 官方 `thinking.type=disabled`；C 仅缩短原 prompt 的 instruction。显式 `--live` 才允许真实请求；每候选最多两段，每段先写 receipt，失败拒绝自动重试，保留物理请求遥测、缺失 usage 为 null，并在确定性质量失败后停止。
- `tests/test_llm_reasoning_dialogue.py`、`tests/conftest.py`：冻结哈希、单变量、schema/引用/长度/角色门禁、无 key 预检、receipt 防重调和未知用量汇总测试。
- `docs/experiments/phase19-3b-dialogue.md`：A/B/C 方案、已测 A、待测 B/C、估算与外推边界。历史 A dialogue 为 2 调用、6099 reasoning tokens、25.66 秒、估算0.0302元。修正后的整集 56.46 秒 episode 估算0.0866元；25分钟线性外推约2.300元，不是报价。

## 验证与 Git

专项96 passed；默认全量离线711 passed、1 skipped、7 deselected、10 subtests；`python3 scripts/validate_project.py`、compileall、`git diff --check` 通过。无真实 API、TTS、音频或 Pipeline 调用。已验证功能提交 `9a1f8d8213e1fcc5da3f760591f28ebd4a47758d`，交接快照提交按 D-006 不自引用；尚未 push。

## Exact Next Step

先只读核对工作树与 `docs/experiments/phase19-3b-dialogue.md`。在**当前运行进程**安全具备真实 DEEPSEEK_API_KEY 后，人工确认冻结 fixture，再依次运行 `scripts/llm_reasoning_dialogue.py --candidate reduced --live` 和 `--candidate focused --live`，每个候选最多两次请求；不要重跑已有 receipt，失败要检查遥测并有界处理。人工逐轮核对事实和自然度，计算相对基线的 reasoning/latency/cost 降幅及 episode/25分钟投影，提交真实 Dialogue checkpoint。此后再冻结 Consistency 历史输入、创建七种确定性 fixture，独立实验 A/B/C；不改变生产默认。

## 已知问题

真实 B/C 结果与 Consistency 实验尚未完成。原历史 consistency 含 `unverifiable`，不能宣称基线质量全部通过。另在现存已跟踪 TTS 实验脚本中发现疑似硬编码 Google API 凭证，未验证有效性，也未改动冻结的 TTS 阶段；维护者应安排凭证轮换并另行清理，勿在聊天或提交中复述凭证值。

---

# Phase 19.3A TTS 真实 A/B 对比与成本模型校准完成：当前交接（2026-09-26）

## Current State / Completed

Phase 19.3A TTS 真实 A/B 对比（Qwen3-TTS-Instruct-Flash vs Gemini 3.8 Flash-Lite TTS）与计价模型校准已完成：

1. **测试稿锁定**：
   - 冻结唯一固定播客稿 `output/ab/tts_ab_script.json`：22 轮对话、765 个中文字符，覆盖普通陈述、疑问句、长句、短句、强调、数字（2026年、80%、3倍）、英文缩写（AI、API、stress test、feedback）、书名/人名（《思考，快与慢》、丹尼尔·卡尼曼、查理·芒格）、自然停顿与轻微情绪变化。两候选吃完全相同脚本。

2. **计费模型纠偏与正式撤回声明**：
   - **错误撤回**：正式撤回此前将 Gemini Lite 误按字符计费（15 元/百万字）得出的“Gemini 便宜 81.24%”、“25分钟仅需 0.10 元”的错误结论。
   - **官方标准计费单位**：
     * `qwen3-tts-instruct-flash`：80.00 CNY / 1,000,000 字符（0.8 元/万字）
     * `gemini-3.8-flash-lite-tts`：$0.0015 / 10 秒（$150.00 USD / 1,000,000 秒）音频输出 + $0.50 USD / 1,000,000 文本输入 tokens；独立汇率层换算（默认 7.20 CNY/USD）。
   - **架构升级**：
     * `src/bookcast/provider_config.py`：支持 `currency`、`billing_unit`（'characters'/'audio_duration'/'audio_tokens'）、`audio_tokens_per_million` 及 `exchange_rates`。
     * `src/bookcast/cost.py`：TTS 计费根据 `billing_unit` 分发，支持时长计费和汇率转换，保留 `original_cost`、`original_currency`、`exchange_rate`、`converted_cost`、`converted_currency`。

3. **校准后的真实表现与成本对比（0 真实 API 增量调用）**：
   - **Candidate A（Qwen3-TTS-Instruct-Flash）**：
     * 音色：Cherry（Host）+ Ethan（Guest），正式风格 instruction。
     * 单句单元合成：22 次物理请求，无重试、无失败。
     * 耗时：墙上耗时 53.03s，平均延迟 2.41s，P95 延迟 3.63s。
     * 音频：成品时长 157.62s（约 2 分 38 秒），RTF = 0.336。
     * 成本：单价 80.00 元/百万字，本次耗资 **0.06120 元**，约 0.02330 元/成品分，25 分钟投影 **0.5824 元**。
   - **Candidate B（Gemini 3.8 Flash-Lite TTS）**：
     * 音色：Kore（Host）+ Puck（Guest），语义对齐之正式风格 prompt。
     * 多轮片段合成：2 次物理请求（590字 + 175字），无重试、无失败。
     * 耗时：墙上耗时 41.23s，平均延迟 20.61s，P95 延迟 22.23s。
     * 音频：成品时长 170.30s（约 2 分 50 秒），RTF = 0.242。
     * 成本：时长计费（$0.0015/10s）+ 文本 tokens（$0.50/1M tokens），原始成本 **$0.02593 USD**，折合人民币（7.20）为 **0.1867 元**，约 0.0658 元/成品分，25 分钟投影 **$0.2285 USD**（约 **1.645 元**）。
   - **真实对比结论**：在人民币计费视角下，**Qwen3-TTS 实际上比 Gemini 3.8 Flash-Lite TTS 便宜 67.2%**（Gemini 约为 Qwen 成本的 2.82~3.05 倍）。

4. **统一后处理与盲听资产**：
   - 两份音频均经过完全一致的 FFmpeg EBU R128 (-16 LUFS) 响度归一化（输出 24kHz/16-bit PCM 单声道 WAV）及 128kbps MP3 转码。
   - 产物清单：
     * `output/ab/A.wav` (7.2MB), `output/ab/A.mp3` (2.4MB)
     * `output/ab/B.wav` (7.8MB), `output/ab/B.mp3` (2.6MB)
     * `output/ab/ab_mapping.json` (内部盲测映射)
     * `output/ab/metrics.json` (已纠偏更新的双币种客观指标)
     * `evaluation/tts_ab_blind_score.json` (12 维度人工盲听评分表)

## In Progress / Exact Next Step

1. **用户盲听反馈已获得**：
   - 用户试听 A 与 B 后反馈：“A.mp3和B.mp3听起来差不多，都还行”。
2. **路由决策**：
   - 遵照指示，**暂停对默认 TTS 路由的任何调整**，维持方案 1：
     * `standard`: `qwen3-tts-instruct-flash`（高性价比，细粒度单句生成与控制）
     * `high`: `gemini-3.8-flash-lite-tts`（云端长文本多轮连贯，音质稳定）
3. 严格遵守安全规则：不随意切换路由，不自动进入下一阶段，绝不 push 到远程仓库。

## Remaining / Compatibility Notes

旧默认（纯 Mock）、既有 TTS Provider（`kokoro-local`、`gemini-tts`、`qwen-local`、`cosyvoice-v2`）和公共 API 保持 100% 向后兼容。旧 Job 恢复严格遵循历史快照，不发生破坏。

## Tests Passed / Last Stable Commit

- 稳定提交快照：`281f0ac8f6d82c34d2150a0da52556dd3081f608`
- 验证脚本：`python scripts/validate_project.py` 通过
- 离线回归：701 passed, 1 skipped, 7 deselected, 8 warnings, 10 subtests passed
- 成本与遥测专项测试：`tests/test_cost_telemetry.py` 14 passed
- 静态校验：`python3 scripts/validate_project.py` 通过，`git diff --check` 通过。

## Tests Not Yet Run / Known Issues

未执行长音频真实调用。真实 API 调用必须等待用户明确授权。

## Files Changed / Commands To Continue

Phase 19.2 Telemetry & Cost Precision 修正文件：
- `bookcast.toml`
- `examples/model-routing-cloud.toml`
- `src/bookcast/provider_api.py`
- `src/bookcast/adapters/compatible.py`
- `src/bookcast/adapters/qwen_llm.py`
- `src/bookcast/adapters/qwen_cloud.py`
- `src/bookcast/pipeline.py`
- `src/bookcast/model_routing.py`
- `src/bookcast/provider_chain.py`
- `tests/test_cost_telemetry.py`
- `output/e2e/a834c024a7d11624be66f19b/usage/cost_summary.json`
- `output/e2e/a834c024a7d11624be66f19b/manifest.json`
- `output/e2e/a834c024a7d11624be66f19b/e2e_acceptance_report.json`
- `docs/STATE.json`
- `docs/WORKLOG.md`
- `docs/HANDOFF.md`
- `src/bookcast/adapters/qwen_cloud.py`
- `src/bookcast/provider_config.py`
- `examples/qwen-cloud-tts.toml`
- `examples/model-routing-cloud.toml`
- `tests/test_qwen_cloud.py`
- `tests/conftest.py`
- `tests/test_live_dashscope.py`（新增）
- `tests/fixtures/qwen3_tts_instruct_flash_response.json`（新增）
- `docs/PROVIDERS.md`、`docs/HANDOFF.md`、`docs/STATE.json`、`docs/WORKLOG.md`

验证命令：
- 完整离线测试：`GIT_CONFIG_GLOBAL=/dev/null .venv/bin/python -m pytest -q`
- 项目静态校验：`GIT_CONFIG_GLOBAL=/dev/null python3 scripts/validate_project.py`
- 编译检查：`.venv/bin/python -m compileall src tests scripts -q`


---

## 2026-09-26 遵循官网实际要求时间重试与每日配额精准识别

目标：根据服务商官方实际要求时间（Retry-After）进行精准冷却倒计时，识别 Google Gemini 免费层每日配额限制（DAILY_LIMIT），并升级质检门禁繁体支持与 quality-v3。

完成：
1. **官方 Retry-After 与配额识别**：`classify_http` 多源提取官方等待时间（HTTP 头、`RetryInfo.retryDelay`、报错正文秒数），挂载 `retry_after`；识别 `per day` / `daily` 限额为 `ErrorKind.QUOTA` 且 `quota_reason = 'DAILY_LIMIT'`。
2. **全链路透传与自动断点恢复**：`Job`、`Manifest`、`jobs.py`、`web_worker.py` 与 `web_service.py` 完整持久化并透传；Web 前端根据 `retry_after` 倒计时（增加 1s 裕量）并在归零时自动发起恢复（上限 5 次）；识别 `DAILY_LIMIT` 时阻断无意义重试并提供降级与配置指引。
3. **质检门禁繁体中文支持**：`quality.py` 的 `attribution="hypothetical"` 检查扩充支持繁体字（「假設」、「設想」、「比如說」、「假使」、「假若」）与常用自然引词（「比如」、「譬如」、「如果」）；缓存版本升至 `quality-v3`。

验证：离线全量 519 passed、1 skipped、5 deselected、10 subtests 全部通过；Web typecheck / build 通过；项目校验通过。

下一步：引导用户在浏览器中测试断点恢复；如遇 Gemini 免费层每日配额耗尽，按提示更换 Key、升级付费结算或切换至 Kokoro 本地语音引擎。

---

## 2026-09-26 DeepSeek 纠错重试截断碎片消除与强健 JSON 提取器

目标：彻底解决任务《十一家注孙子》在第 98 步（`analysis:0011:0005`）出现的 `schema_error`（`json_invalid`）永久失败问题。

根因排查与解决：
1. **截断碎片污染导致 Attempt 2 语法损坏**：此前 `prepare_schema_retry` 会把上一次失败时记录的 `self._last_raw_response` 粗暴按字符截断取前 2000 个字符拼进重试提示词（`Fix structure while preserving content semantics: {snippet[:2000]}`），向大模型注入了包含未闭合引号和括号的残缺 JSON 碎片，导致 DeepSeek 在 Attempt 2 尝试接续或引用该碎片，引发根节点反序列化失败 `validation_field: "$", validation_reason: "json_invalid"`。
2. **剥离截断碎片**：在 `prepare_schema_retry` 中彻底移除 `[:2000]` 残缺代码片段，保持重试指令清爽明确（如仅保留 `evidence must contain at most 6 items. Regenerate the full JSON with all required fields.`），促使模型以完整正确的结构重新输出。
3. **强健 JSON 提取器升级**：重构 `_strip_markdown_fence`，结合 Markdown 代码块正则匹配与字符级括号匹配计数器（bracket-matching），稳健剥离大模型在 JSON 前后输出的思考过程（Thinking Process）及解释性闲聊，并精准处理字符串内转义引号与花括号，杜绝自然语言夹杂导致 `jiter` / Pydantic 解析异常。
4. **验证**：新增 `test_deepseek_conversational_retry_with_complex_text_and_no_truncated_snippet` 专项单测；离线全量 **516 passed, 1 skipped, 10 subtests passed** 全部通过；`npm run build` 重新构建通过；`python3 scripts/validate_project.py` 校验通过。

下一步：引导用户在浏览器上刷新页面后点击“已修复，重试任务”，断点继续恢复《十一家注孙子》第 98 步及后续章节的生成。

---

## 2026-09-26 Web 任务错误展示友好映射与章节分析 ID 规范化

目标：针对 Web 界面任务报错裸露内部枚举代码 `business_error` 的问题，提供清晰友好的中文错误解释与代码对照；同时在算法层加固 `resolve_analysis` 对章节/分块 ID 前导零表示（如 "10" vs "0010"）的安全规范化对齐。

完成：
1. 前端 `web/app/page.tsx` 新增 `ERROR_DESCRIPTIONS` 字典与 `formatError` 转换函数，覆盖 `business_error`、`schema_error`、`auth_failure`、`quota_exhausted`、`rate_limit`、`timeout`、`temporary_unavailable`、`bad_input`、`interrupted` 等所有核心错误枚举；展示格式为 `业务校验未通过（章节提取或关联证据与原文不匹配） (business_error)`，既清晰易懂又保留底层代码便于排查。
2. 后端 `src/bookcast/content.py` 的 `resolve_analysis` 对模型返回的 `chapter_id` 与 `chunk_id` 在数值等价时自动规范化为权威 `payload` 中的带前导零格式（如 "10" -> "0010"），避免因字符串表现差异导致误判 `business_error`。
3. 离线全量 515 passed、1 skipped、10 subtests passed 全部通过；`npm run build` 成功。

验证：测试套件全量通过，无回归；`python3 scripts/validate_project.py` 验证通过。

下一步：引导用户在前端界面点击“已修复，重试任务”，断点继续《十一家注孙子》任务的生成。

---

## 2026-09-26 Web 书架移除反馈优化与旧服务降级引导

目标：当用户在声音书架执行任务移除时，若本地服务未重启或发生错误，提供就地可见反馈与明确操作指引，并更新前端构建静态产物。

完成：
1. 前端书架新增专属错误提示区域 `#library .notice.error`，移除失败不再打到顶部表单，就地展示失败原因；切换选中卡片或重新操作时自动清空。
2. API 异常处理针对 405 Method Not Allowed（本地仍运行旧版本进程无 DELETE 路由）映射友好提示：“本地服务仍在运行旧版本，请重启 bookcast serve 后重试。”
3. 重新执行 `next build` 产出最新 `web/out` 静态文件；新增 Playwright E2E 测试模拟 405 降级场景并验证报错提示及卡片状态。

验证：功能提交 `73adc9b02f4cce76bdc23cf43193a473d5fcb560`；Web API **20 passed**，Web typecheck 通过，浏览器 E2E **5 passed**，项目校验和提交差异检查全量通过。未调用真实 Provider。

下一步：提示用户重启正在前台运行的 `bookcast serve` 进程，加载最新代码与静态产物完成最终真机验证。

---

## 2026-09-26 Web 声音书架移除任务

目标：让书架卡片可移除。仓库 AGENTS.md 禁止删除用户数据，因此操作仅隐藏 Web 提交记录，保留上传书籍、生成目录和音频。当前用户仍可通过已有任务路径与 CLI 检查产物；正在生成的任务不可移除。

完成：`WebJob.removed_at` 为兼容旧记录的可选字段；`DELETE /api/jobs/{id}` 在 WebService 全局锁中检查活动状态并原子保存移除标记，重复调用成功，书架历史排除标记记录。前端卡片新增独立“移除”按钮与确认提示，选中任务移除后刷新书架和当前视图；移动布局可用。`docs/WEB.md` 明确操作范围。

验证：功能提交 `41ec6b695a0cecee4bfef54f6e9573e1656a4fc2`；Web API **20 passed**，Web typecheck/build 通过，浏览器 E2E **4 passed**，项目校验和提交差异检查通过。测试覆盖生成完成后移除、列表持久隐藏、文件哈希保留、重复删除、活动任务拒绝，以及浏览器确认、刷新和音频文件保留。初始两次 E2E 分别因完成任务标题去扩展名与旧用例按钮定位歧义失败，已修正测试并复测全过。未运行完整 Python suite，也未调用真实 Provider。本交接快照按 D-006 另作提交。

下一步：Phase18 独立 DeepSeek clean run 仍待用户本机凭证；保留既有 blocker。已移除记录仍可从本机任务目录和 CLI 找到，用户数据没有物理删除。

---

## 2026-09-26 Web 新建任务默认目标时长 20 分钟

目标：按用户截图将新建页面默认时长从 40 改为 20 分钟，并让 Web API 在省略时长时使用同一默认值。书架中已有任务继续显示各自保存的目标时长。

完成：`web/app/page.tsx` 初始分钟数设为 20；`src/bookcast/web_service.py` 的 `Submission.minutes` 默认设为 20。`web/e2e/app.spec.ts` 验证页面初值和提交结果，`tests/test_web.py` 验证 API 省略分钟数时持久化为 20。`docs/WEB.md` 记载默认值。未修改 CLI/Core 内容预算默认值或已有任务。

验证：功能提交 `f7c39c835e3b81ec7cfc7f11a7b67f17d4a0c31e`；该提交代码工作树上 `tests/test_web.py` **18 passed**，Web typecheck/build 通过，浏览器 E2E 指定场景 **1 passed**；`python3 scripts/validate_project.py` 与提交 diff check 通过。未运行完整 Python suite；未调用真实 Provider。工作区交接状态与本快照单独提交，按 D-006 的 `last_verified_commit` 仍指已验证功能提交。

下一步：原 Phase18 的独立 DeepSeek clean run 仍需用户本机凭证，保留原 `next_actions` 和 blocker。不要重制或修改已完成的书架 Job。

---

## 2026-09-26 RC Blocker Closure：Gemini 安全错误、物理请求与共享限流

目标：仅关闭最终 RC 交叉审计的三个阻断项；没有调用真实 Gemini/DeepSeek，没有改 prompt、chunk、费用费率、解码器、Kokoro 或 Web UI。

完成：Gemini 上游错误正文和任意 status/reason 不再进入 Attempt、event、manifest 或 API，统一映射内部 `safe_reason` 枚举。Core 在逐段合成时通过 `ProviderRequestContext` 显式提供 Job/output/logical chunk 身份与该 Job 的 `usage/physical_requests.jsonl`；每次物理请求记录 HTTP/重试/usage/billing evidence。没有明确 usage 时 billing 为 `unknown`；日志写失败只置 `telemetry_degraded` 并写安全 `telemetry_diagnostic`，成功音频仍完成。

Gemini 所有首次和重试 TTS 请求使用同一用户本地 SQLite 文件原子预约，最小间隔 25 秒；发送前复核实际发送时间。Retry-After 秒数/HTTP-date 与指数退避都转成单调时钟截止，按共享限流与重试截止的较晚者发送。长 Retry-After 先本地等待，避免崩溃占据远期槽；事务崩溃释放，没有永久锁。独立 FakeClock 测试覆盖多实例、两线程、延迟调度、崩溃、重试时序和配额。

验证：功能提交 `5a04985ff3330d88671eeaa55c946506c473bcf8`、HTTP 400 分类提交 `9f9fd41fa413f0f8902f75e9c07f3aeaeb55cec8` 与物理记录安全字段提交 `8f94f6553a427203b3db77a8ec6586c7aa92cd7c` 均已验证；最终代码工作树完整默认离线 suite **512 passed、1 skipped、5 deselected、10 subtests passed**（77.18 秒），最终功能提交上 Gemini 专项 **87 passed**。`python3 scripts/validate_project.py`、`npm --prefix web run typecheck`、`python -m compileall src tests scripts`（通过 `.venv/bin` 在 PATH 中提供 Python）、`git diff HEAD^ HEAD --check` 均通过。失败的首轮全量为两个旧脚本 monkeypatch 未接受新的可选 context 参数，已用无 context 时兼容调用修复并复验；没有遗留本阶段失败。真实 Gemini 系统时钟/跨进程行为及账单判定只离线验证，发布前需在用户明确授权的独立任务上观察。本阶段不应重复改音频解码或重跑旧完成任务。交接快照自身提交按 D-006 使用 `git log -1` 查询，不在 STATE 中自引用。

---

## 2026-09-26 RC Stage 6：统一 Gemini TTS Audio Decoder

目标：解决 Gemini TTS 音频解码路径并存（`decode_audio` 与 `decode_pcm`）及校验缺失隐患，统一为单一 canonical decode path。
1. **单一规范解码器与结构契约**：
   - 彻底移除废弃的 `decode_pcm`；生产环境仅保留统一入口 `decode_audio(result, destination=None) -> AudioPayload`。
   - 定义不可变 `AudioPayload` 契约结构（`audio_bytes`/`bytes`/`data`、`container`、`codec`、`sample_rate`、`channels`），消除不同入口不同类型的歧义。
2. **规范解码与格式校验**：
   - Interactions 响应中按 `steps[].content[]` 严格定位单一 `type == 'audio'` 块；支持合法多 step 搜寻与 text+audio 混合内容过滤；多 audio block 视为异常拦截。
   - 严格 Base64 解码，非法编码安全分类为 `decode_error`。
   - 容器/MIME 校验：仅放行合法 WAV MIME 或在明确声明 raw PCM 时包裹标准头，杜绝无头 raw 伪装。
   - 严格 WAV 校验：基于标准库 `wave` 校验 RIFF 容器、1 通道单声道、16 位采样、24,000 Hz 采样率、非空且帧长不截断；校验完全通过后方写入 destination，失败绝不残留目标文件。
3. **安全脱敏与日志保护**：
   - 错误统一为 `ProviderError(ErrorKind.SCHEMA, error_type="decode_error")`，严禁在 `validation_reason`、错误消息或终端日志中记录 Base64 数据、原始完整响应或脚本文本。移除旧 `decode_audio` 中向 stderr 打印 response 键和步骤信息的调试代码。
4. **测试与回归**：
   - 覆盖 valid WAV、invalid RIFF、missing audio、text + audio、audio 在后续 step、多个 audio block 拦截、invalid base64，以及采样率/通道/空帧/截断 WAV 边界测试；
   - 现存 Interactions 与 voice test 模拟数据同步接入规范 WAV 结构；
   - 62 项 Gemini 专项测试全部通过，全量 suite 495 passed、1 skipped、5 deselected、10 subtests 全部通过。未调用真实 Gemini API。
   - 功能提交 `0992207bbd986ad4ff8eaccc1f76d15d35adb50d` 已验证，`last_verified_commit` 已同步。

---

## 2026-09-26 仓库未跟踪文件收口与测试资产补全

目标：处理仓库中残留的未跟踪文件，保持工作区整洁且不遗漏必要测试资产。
1. **测试资产补全**：将 `tests/fixtures/deepseek_analysis_0015_concepts_overflow.json` 纳入 Git 跟踪。该文件为 `test_2026_09_25_analysis_0015_concepts_overflow_and_conversational_retry` 必需的脱敏测试 fixture，同目录下的 `deepseek_analysis_0003_evidence_overflow.json` 已在历史提交中跟踪。
2. **会话转录隔离与清理**：在 `.gitignore` 中新增 `debugging-*.md` 规则；本地保留完整的原始排障流水 `debugging-bookcast-deepseek-schema-error--bf8123db.md`，删除存在截断的重复副本 `-2.md` 与 `-3.md`。
3. **验证与状态同步**：相关测试 1 passed，`scripts/validate_project.py` 通过，功能提交 `8fe39f474888d9d6fb31dfcbd7d31573d3ebd076` 已创建并验证。工作区当前无未跟踪文件。

---

## 2026-09-26 RC Stage 5：Cost Summary production integration

目标：让创建时配置快照、Attempt usage、成本摘要、CLI 与 Web 使用同一份按 Job 验证的成本视图。新 Job 在 manifest 保存 `output_id` 与不含凭证的 `cost_snapshot`，其中包含 Provider/model/pricing 和北京时区工作日峰谷策略。恢复时当前 Provider 选择可以变，但创建时计价快照不变；旧 Job 无完整快照时金额为 unavailable，不用今天的 `bookcast.toml` 补价。

`_Runner.update_llm_usage` 不再读取不存在的 `self.config`；终态 LLM/TTS Attempt 更新 usage 视图并原子写 `usage/cost_summary.json`。失败时只记录异常类型的 `cost_summary_diagnostic`，不阻断 Job。摘要按 `(provider, model)` 分组；缺字段的 Attempt 不计价，相关行标记 partial；TTS 保持 unavailable。`job_status` 只返回通过 Job ID、output ID、实际源文件 SHA、快照、当前选择及 journal 指纹校验的摘要；Web DTO 直接使用该结果。CLI `bookcast cost <output>` 持锁读取 Job 的 manifest/snapshot/usage，不接受 `--config` 历史重定价，并从 manifest 显示身份。没有新 Attempt 的恢复若使摘要失效，在完成时安全刷新。

离线测试覆盖 Pipeline Attempt → usage → cost_summary → job_status → Web DTO、历史配置与当前配置不一致、partial usage、同 Provider 多 model、跨 Job 复制/过期身份、无快照旧 Job、摘要失败不影响生成以及无新增 Attempt 恢复。默认 pytest 的禁网保护仍有效，未调用真实 Provider。唯一额外测试维护是把 Stage 3 已修改的 Gemini 缺 Key 断言从旧 `SystemExit` 改为 `ProviderError(AUTH)`；没有修改 Gemini 生产代码。完整默认 suite 为 483 passed、1 skipped、5 deselected、10 subtests（116.48 秒）；成本/恢复专项 34 passed，Web typecheck、compileall、项目校验和 diff check 通过。功能提交 `86275234810b2b704fa73b9ef7d8632e22196117` 上复测 12 passed，项目校验与 Web typecheck 通过；`STATE.last_verified_commit` 保存此已验证提交，交接快照自身不自引用。

本阶段未改 LLM prompts、Gemini retry/limiter、TTS chunking、价格表、音频或已完成真实 Job。下一步仍是用户本机有凭证环境的新独立 DeepSeek clean run；不能以旧失败 Job 或本阶段离线金额冒充真实账单。预先存在的四个未跟踪调试/fixture 文件未改、未提交。

---

## 2026-09-25 RC Stage 4：Job Resume / Active Error State 收口

目标：严格解耦 active_error 与 historical_attempt_errors。
1. **Active Error 契约**：
   - 只有 Job 有效状态为 `failed`、`failed_retryable`、`blocked`（`TaskState.FAILED_PERMANENT`、`TaskState.FAILED_RETRYABLE`、`BLOCKED`）时，`active_error` / `error` / `progress.error` 才允许存在。
   - 当任务成功完成（`completed` / `SUCCEEDED`）时，`active_error`、`error` 及 `progress.error` 严格返回 `null`（`None`）。
   - `_Runner.finish()` 在任务完成前清空 `self.last_error`，`_Runner.event()` 仅在失败状态中发射活跃错误。
2. **Historical Audit 不受污染**：
   - 所有的历史重试、限流（`rate_limit`）、超时（`timeout`）与配额超限（`quota_exhausted`）尝试完整保留在 `manifest.ai_calls`、`logs/events.jsonl` 与运行历史中，禁止删除历史审计。
3. **CLI / Web 行为**：
   - CLI：`show_progress` 在 `completed` 状态下不显示历史错误（显示为 `-`）；`status` 命令在 completed 状态下不输出 `错误：...`。
   - Web API：`GET /api/jobs/{id}` 与 `GET /api/jobs` 对 `SUCCEEDED` 任务返回 `error: null`、`active_error: null` 及 `progress.error: null`。
   - Web UI：仅对当前未完成的 active_error 渲染红色告警横幅；恢复成功的任务显示完成状态，不显示红色错误横幅。
4. **回归测试**：
   - `test_active_error_state_failed_permanent`（验证永久失败任务包含 active_error）
   - `test_active_error_state_failed_retryable`（验证重试失败任务包含 active_error）
   - `test_active_error_state_resume_success_clears_active_error_retaining_history`（验证多 chunk 任务中 chunk 1 成功、chunk 2 rate_limit 失败后 resume 成功：最终状态为 SUCCEEDED、active_error=null、job_finished event 的 error=null，且 ai_calls 中完整保留历史 rate_limit attempt）
   - `test_active_error_state_completed_job_active_error_is_null`（验证正常完成任务 active_error=null）
   - `test_active_error_state_multiple_failures_then_success`（验证多次失败后成功的任务 active_error=null，ai_calls 保留所有历史失败）
   - `tests/test_web.py::test_recovery_delegates_to_core_without_repeating_completed_chapters`（验证 Web API 接口在失败态返回 active_error，恢复后返回 active_error=null 且保留历史调用）

验证：
- 39 passed (`tests/test_job_recovery.py`, `tests/test_job_cli.py`, `tests/test_web.py`)；
- `npm --prefix web run typecheck` 0 errors；
- `python3 scripts/validate_project.py` 通过；
- Python syntax compileall 检查通过；
- `git diff --check` 通过；
- 未调用真实 API。

---

## 2026-09-25 RC Stage 3：Kokoro 长连续句与 Gemini 缺失 API Key 异常分类修复

目标：解决两个独立的小型 P0 问题：
1. **Kokoro 长连续句**：`split_text` 的默认 limit 从 200 修正为 80，`speech_units` 显式强制 `limit=80`，确保生成的每个 `SpeechUnit.text` 长度 <= 80，消除 Pydantic `ValidationError`；同时保留自然标点优先切分、无标点确定性 fallback、顺序一致性与字符不丢失。新增 79/80/81/100/200 字与标点长句单元测试，以及 `render_speech` 完整端到端离线回归。
2. **Gemini 缺失 API Key**：`GeminiTTSProvider._request` 中移除 `sys.exit`，在 `key` 缺失或为空字符串时稳定抛出 `ProviderError(ErrorKind.AUTH)`，杜绝 `UnboundLocalError`、`SystemExit` 或通用 `schema_error`，且错误信息不包含密钥明文内容。新增缺失、空串、有效 key 测试。

验证：
- `tests/test_tts.py` 与 `tests/test_tts_integration.py` 共 37 项全部 passed；
- `scripts/validate_project.py` 通过；
- `compileall` 与 `git diff --check` 通过；
- 未调用真实 TTS/LLM API。

---

## 2026-09-25 RC Stage 0：离线测试基础设施

目标：使核心 RC 测试在不调用真实 Gemini/DeepSeek/Kokoro API 的条件下快速、确定性运行。当前代码已在 Gemini Provider 注入 `clock`/`sleeper`，生产默认仍为真实时间，25 秒 RPM、10/20/40/60 秒退避及 `Retry-After` 数值未改。默认 pytest 的 socket 禁网保护覆盖主进程和 Python 子进程；意外连接立即以 AssertionError 失败。`unit`/离线 `integration` 仍是默认层，`live`/`large_model` 保持显式 opt-in。

Gemini 测试已改为 `/v1beta/interactions` 的 `input`、`generation_config`、`steps[].content[]` 样本；移除旧 `generateContent` 请求断言和 PCM 响应 fixture。`test_success_chunk_no_repeat` 用真实 `_Runner` 检查点验证：chunk 1 落盘、chunk 2 首次 403 失败，恢复后只有 chunk 2 增加一次 HTTP 请求，按 `tts_segment:0001:0001/0002` 校验状态。测试不接触真实 API。

验证：核心 58 passed（4.83 秒）；完整默认 suite 465 passed、1 failed、1 skipped、5 deselected、10 subtests passed（61.98 秒）。功能提交 `4f841063a320da78f4b20c94471f49809fbb75c3` 上核心 58 passed（4.29 秒），validator 和 compileall 通过。唯一失败 `tests/test_cost_calculator.py::test_cost_calculation` 仍断言旧 `summary['llm']['cost']` 字段，当前成本摘要按 `llm.providers` 分层；属于本阶段禁止修改的 cost 范围，未改实现或测试，不据此判断生产成本逻辑。`git diff --check` 通过。已存在的无跟踪调试文档及 DeepSeek fixture 未改、未提交。下一步单独处理成本测试契约，再按 RC 后续阶段审查生产语义；不要把本阶段测试通过当作 Gemini 真实服务验证。`STATE.last_verified_commit` 记录上述已验证功能提交，不自引用交接快照。

---

## 2026-09-25 too_short validation error recovery

分析 v4 fresh run 中 `analysis:0057:0001` 的失败，发现由于输入文本（chunk 0001）非常短（只有 900+ tokens），模型找不到 core_ideas 并合法地输出了空数组 `[]`。
但 `EvidenceAnalysis` schema 约束 `core_ideas: Field(min_length=1)`，导致 Pydantic 抛出 `ValidationError(reason='too_short')`。
由于原 `compatible.py` 的 schema 恢复逻辑漏掉了对 `too_short` 的处理，没有给模型下发 retry guidance（"Must contain at least 1 items"），直接变成了 `FAILED_PERMANENT`。

**Fix**: 在 `prepare_schema_retry` 增加了对 `reason == 'too_short'` 的显式捕获与 retry 提示生成（同 `too_long` 的统一处理层级），保持了原 Schema 的业务约束。并补充了对应的回归测试。


## 2026-09-25 ConsistencyReview 质量修复

用户报告 fresh 5-min v3 run 在 `consistency:0001` 产生 FAILED_PERMANENT `schema_error`。经查 `output/llm-cost-clean-5min-v3/b288c0f91d3e46f32eb95916/logs/events.jsonl`，错误是 `ValueError`，`validation_reason=finish_reason`，`finish_reason=length`。这是由于 DeepSeek 在 `consistency:0001` 生成 `ConsistencyReview` 时，陷入 CoT reasoning loop 耗尽了 12000 output tokens，触发了 length limit。
同时，用户期望修复 `ConsistencyReview` 关于 checks 数量不一致的 quality error。因为 Pydantic 不感知源 script turn indices，旧的验证会在 `content.py` 抛出 `ProviderError(ErrorKind.BUSINESS)`。

修改了 `src/bookcast/adapters/compatible.py`，让 `prepare_schema_retry` 捕获 `finish_reason == 'length'` 并添加 retry_guidance。修改了 `src/bookcast/content.py` 的 `validate_review`，对于 extra checks 执行确定性删除（语义对齐），对于 missing checks 抛出 `ProviderError(ErrorKind.SCHEMA)` 并携带 `validation_reason=missing_turns`。`prepare_schema_retry` 支持识别 `missing_turns` 并提供 guidance。
没有关闭 consistency validation，没有修改 Token/TTS 优化，完全复用 existing normalization。所有 72 个线下 tests 通过。已 push 修复。

# 给下一位 Coding Agent

更新时间：2026-09-25。真实 5 分钟独立 DeepSeek 任务 `output/llm-cost-clean-5min-5f94c8665518/b288c0f91d3e46f32eb95916`（job_id `69dbb0d591bd4c04a6a54a74140f8baf`）已在 `analysis:0003:0001` 永久失败，不能计作 clean-run 成本。只读事件显示两次 HTTP/JSON 正常，`finish_reason=stop`，Pydantic `EvidenceAnalysis.evidence` 列表两次 `too_long`（上限6）。原始响应正文未持久化，因此只能确认超限，无法声称具体列表长度或内容。第三单元 2693 字、64 证据候选、prompt 10156 字；前两章无异常输入，analysis prompt/schema 未被 Phase18 token 优化改变。

最小修复在 `src/bookcast/adapters/compatible.py`：DeepSeek 一次有界纠错后，可选列表仍超 maxItems 时确定性保留前 N 项，再做完整 Pydantic/证据归属校验；必需列表不归约，非可修复错误不 retry。`src/bookcast/pipeline.py` 事件增加 `task_id` 与 `schema_model`，不改变 manifest 旧格式。脱敏重建 fixture 与测试覆盖真实错误形态及不可修复错误。相关 217 passed，validator/compileall/diff check 通过；无真实 DeepSeek 调用。旧失败 job 不应作为 clean run 复用；用户本机应使用新独立输出根启动新的 5 分钟任务，旧已完成 20 分钟产物保持不变。

---

# 给下一位 Coding Agent

更新时间：2026-09-25。当前目标是用户明确要求的真实 DeepSeek 5 分钟双人播客 clean-run 成本验证，不是继续架构优化。**真实调用未启动**：当前 Codex 执行进程没有 `DEEPSEEK_API_KEY`，本地 Web 127.0.0.1:8765 未运行。不要向聊天输出密钥，不要拿旧 Job 冒充新 clean run。用户已收到安全注入凭证的异步请求。

零成本烟测通过（Mock 13 请求、真实 0），相关离线 244 passed / 1 skipped。新独立运行基线在 `output/llm-cost-clean-5min-5f94c8665518/baseline.json`，run_id `5f94c8665518409a9cdad81f205d0cee`；当前只有该基线文件，无 Core job_id、manifest、usage 或检查点。源使用旧已完成 Web Job 内的 EPUB 导入副本，SHA-256 为 `80b17c0c498c1cc1ae32dc806211c3e1300846f37617ca792189957709d3a0bc`；本地净化解析为 69 个 XHTML 单元、93 个分析 chunk。配置仍是 deepseek-flash / kokoro-multi-lang-v1_0、two_host、5 分钟，HEAD `6a9f9515774468d10bc3f8633dfcfa43a9f431ed`。旧 Job/音频未修改。

取得执行环境凭证后，先确认可用，使用 baseline.json 中 output_dir 作为 `bookcast generate` 的全新输出根，只运行一次，不 retry；出现 schema/auth/异常重复请求或超预期 fan-out 时立即停止。随后用新 Job `usage/llm_usage.json` 与 manifest 审计实际阶段用量、重复、repair 和 target_duration，不推算未发生的数据。旧完成任务一概不要运行。

---

# 给下一位 Coding Agent

更新时间：2026-09-25。先读 AGENTS.md 和核对 Git。Phase18 的 LLM 成本审计与有界预算已提交为 `04551c0d779d6c4b8f198d192dd5dafbbeb70005`；当前真实《狂人日记》完成 Job/音频未修改，未调用 DeepSeek 或 Kokoro。测试结果与决策见 [LLM_COST.md](LLM_COST.md) 和 D-023。

## Phase18 本次工作

- 已完成真实 Job manifest 只读审计：96 analysis、191 章综合、26 全书综合、24 dialogue、24 consistency 个成功唯一 LLM Step，共 361；含失败/修订的实际 LLM attempt 为 429。该 manifest 记录输入 1,135,571、输出 1,202,485 token（含 reasoning 915,704），不是整日 346 万 token 账单。
- 新编排保留所有净化后的源块分析与精确证据；章综合批量从4提到12，在全书综合前按时长/书序选候选章节。20分钟最多32候选、16段脚本及一致性复核；新任务结构投影约210～220个 LLM 请求。未入选单元不进入 dialogue/consistency，仍列入 omitted_chapters。
- chapter synthesis 关闭 reasoning、4096上限；book high/16384、dialogue/consistency low/12000；analysis 保留同配置的既有 16384 上限，以避免旧证据缓存大面积失效。质量门禁、来源过滤、targeted repair 和 TTS 代码未改。
- Attempt journal 派生 `usage/llm_usage.json`，只记 provider/model/stage、请求与服务端 token/缓存用量；无价目表时 estimated_cost=null。不保存正文或密钥。新脚本 `.venv/bin/python scripts/llm_cost_smoke.py` 显式 Mock、临时目录、0 真实 API。
- 离线相关 244 passed/1 skipped，成本 smoke、validator、compileall、diff check 通过。新真实长书的质量与账单对照尚无证据，不要声称已达 1/3 目标。下一步只在用户决定付费新任务时评估，不自动重制旧任务。提交以 `git log -1` 核对，STATE 的 last_verified_commit 遵循 D-006，不自引用交接快照。

---

# 给下一位 Coding Agent

更新时间：2026-09-25。先读 AGENTS.md 并核对 Git；以下为最新 EPUB 来源过滤工作。已验证功能提交 `a65f77dea69907fd05d5033350bfca264dc90c00`，未 push。未运行真实 DeepSeek，也未修改现存已完成 Web Job。

## EPUB 非正文/广告过滤与来源审计

当前真实 EPUB《狂人日记》已完成 DeepSeek+Kokoro 20 分钟双人播客，targeted repair/quality gate 均工作；本次没有覆盖这些逻辑。只读核对原 Job `3a591bfdaac14dbbb254ae8b9e138e85` 的源 EPUB：73 个 spine 项中一个空封面，其余原解析成 72 个 XHTML 文档对应的 Chapter，**不是 72 个语义章节**。原 `parse_epub` 按 spine 收集文本，仅跳过显式 nav，未识别普通链接目录、独立广告页和正文页的推广段落，因此前三个“章节”中包括目录与广告。

新 `source_sanitation.py` 在 Chapter/LLM 前用 EPUB 资源名/属性、链接目录结构及促销上下文规则过滤；独立 `source_filter.json` 记录保留/移除资源及段落的分类、规则和短预览。普通 URL、数字、微信讨论及真实序言默认保留。quality 末端新增 `source_contamination` 阻断并升至 `quality-v2` 缓存版本，targeted repair 判定/次数、TTS 和 Provider 未改。parse 指纹含过滤规则版本；EPUB by-ID 恢复不再绕过新指纹。

对真实源的只读解析得到 69 个保留 XHTML 单元、93 个分析 chunk（旧 72/96）；排除封面、普通目录、前后两页广告及正文中的 4 段推广/制作信息，保留章节中未发现已知推广字符串。未生成全书音频或更新真实 Job。小 EPUB Mock 回归覆盖正文与序言保留、广告在分析/综合/脚本前消失、审计记录、普通数字/URL保留、脚本污染阻断 TTS、解析版本变化恢复。功能提交上相关测试 `113 passed、1 skipped`，validator/compileall/diff check通过。当前已完成音频仍是旧内容；如需净化结果，应另起生成任务并按用户意愿承担费用，不能把旧音频视作自动修复。

---

# 给下一位 Coding Agent

更新时间：2026-09-25。先读 AGENTS.md 和项目文档并核对 Git。当前工作区已实现 Quality Gate 矛盾发言局部定向修复（Targeted Repair）机制，并添加了有界上限与回归测试验证。

## Quality Gate 矛盾发言局部定向修复（Targeted Repair）与有界验证

真实 EPUB《狂人日记》推进至 TTS 前（已完成 400+ 个前置步骤），在 `quality.json` 触发阻塞：`blocking issue: "逐段一致性复核发现与来源矛盾的陈述"`。排查发现全书 24 个 segment 中仅 `0003` 和 `0023` 这 2 个 segment 的个别 turn 出现了 `verdict: "contradicted"`。
1. 失败现场与架构根因：
   - 原代码中 `report = json.loads(r.path('evaluation/quality.json').read_text())` 后直接判断 `if report['blocking_issues']: raise BookCastError(...)` 并永久终止任务。
   - 缺乏局部修复重审机制：全书数百步骤已全部完成，仅因个别段落有 1~2 处矛盾即导致整个任务停滞，且不支持针对性改写受影响段落，存在“要么整本重跑、要么任务作废”的严重缺陷。
2. 最小定向修复设计：
   - 精准定位受影响段：从 `report['factual_consistency']['semantic_reviews']` 识别所有 `verdict == 'contradicted'` 的条目，精准提取 `segment` 和 `turn_index`，并抽取该段争议发言、原引用证据及判定原因，持久化至 `evaluation/repairs/{segment.id}.json`。
   - 局部重跑与提示注入：仅对受影响的 segment 增加 revision 并调用 `dialogue` 重新生成（payload 附带 `repair_issues` 与原证据引用上下文）；在 `INSTRUCTIONS['dialogue']` 中明确规定矛盾点必须严格忠于原书证据，待核验点（unverifiable）必须弱化推断或仅作合理转述，严禁虚构。
   - 局部重审：仅对重新生成的 segment 触发 `consistency` review（payload 注入 `revision`，使步骤缓存键严格绑定版本），更新内存中的 scripts 与 reviews 后重新局部评估 `quality.json`。未受影响的正常 segment 100% 缓存命中，完全不产生额外调用。
   - 有界防死循环止损：定义 `MAX_SEGMENT_REPAIRS = 2`，每个 segment 最多尝试 2 次定向修复。若 2 次修复后仍有矛盾，则停止修复并安全保留 `BookCastError` 永久报错，防止无限消耗 LLM token。
   - 恢复支持：在 `pipeline.py` 中放行被质量门禁阻塞的永久失败任务，允许通过 `bookcast resume`（或 `retry`）直接恢复并无缝进入局部定向修复流程。
3. 测试与验证：
   - `test_targeted_repair_of_contradicted_segment_and_tts_allowed`：验证矛盾段精准识别、定向修复重审、质量门禁通过、TTS 正确执行并生成音频、后续 resume 100% 缓存命中 0 新增调用。
   - `test_targeted_repair_bounded_limit_halts_on_persistent_contradiction`：验证持续矛盾段在达到 2 次修复上限后严格终止抛错，不多消耗调用。
   - 全量回归测试：`tests/test_content.py` 36 项测试全过，`scripts/validate_project.py` 和 `git diff --check` 全部通过。
4. 下一步：
   - 对当前《狂人日记》Web Job 执行 `bookcast resume` 即可触发对 segment `0003` 和 `0023` 的局部定向修复与重审，通过后自动进入 TTS 合成，无需从头重跑。

---



基于提交 `913eed7`、次级数组溢出截断及 Markdown 剥离修复，用户真实 retry 《狂人日记》推进至第 42 章完成（完成 235 步）后，在第 43 章首块 `analysis:0043:0001` 报 `schema_error`：
1. 失败排查现场与历史对比：
   - 历史错误规律：早期章节出现 `too_long`（数组超出上限），第 22 章出现 `json_invalid`（Markdown ```json 代码块包裹），第 30 章归约出现 `business_error`（claim_ids 外推）。
   - 本次失败：`stage: analysis:0043:0001`, `error_type: ValidationError`, `validation_field: core_ideas.0`, `validation_reason: model_type`。
   - 现场数据对比：第 42 章正文 1555 字，DeepSeek 输出标准 `EvidenceFinding` 对象列表；第 43 章原书为仅 7 个字的短章标题占位《奔　　月〔１〕》，DeepSeek 输出 `core_ideas: ["奔月〔１〕"]`（字符串列表而非对象列表），触发 Pydantic `model_type` 错误。
   - 架构根因：
     - DeepSeek 仅支持 `response_format={"type": "json_object"}`，保证 JSON 语法合法但不保证 Schema 合规。
     - 此前 `CompatibleLLMProvider.prepare_schema_retry` 存在严重架构缺陷，硬编码了 `if failure.validation_reason != 'too_long': return False`，人为排斥了除 `too_long` 之外的所有错误类型（`model_type`、`missing`、`string_type`、`list_type` 等），导致任何类型漂移均被视为永久错误终止。
2. 通用受控修复设计：
   - 通用安全本地归一化：
     - 若模型返回 `null` 且字段为可选列表（`not field.get('minItems')`），自动转换为 `[]`。
     - 若字符串列表字段收到单个字符串，自动提升为 `[str]`。
   - 通用有界纠错重试（Bounded Schema Repair）：
     - 彻底废除 `failure.validation_reason != 'too_long'` 的特判限制，允许所有可安全修复的 ValidationError 触发一次针对性纠错重试（由 ProviderChain 保证 strictly 1 retry，`correction == 0`）。
     - 通过 `_get_field_schema` 解析 JSON Schema（含 `$defs` 引用），精准提取出错路径的期望类型/结构（例如 `core_ideas.0` 需为包含 text 和 evidence_id 的对象）。
     - 将具体错误路径、期望规范及上一轮失败片段以结构化指令注入重试提示中，要求模型仅修复结构不改语义。
     - 严格保持最终 Pydantic 和领域模型校验不变，不放宽 schema，不无限重试，OpenAI 等其他 Provider 行为严格不受影响。
   - 添加回归测试：
     - `test_real_failure_shape_chapter42_model_type_is_repaired`：覆盖真实失败形态（字符串元素自动 repair 为对象）。
     - `test_multi_field_schema_drift_with_null_and_repair`：覆盖多字段 null 与类型漂移。
     - `test_repair_failure_is_permanent_and_no_infinite_retry`：覆盖纠错失败严格报 `ProviderError(ErrorKind.SCHEMA)` 并永久终止（最多 2 次调用）。
     - `test_openai_provider_unaffected_by_schema_drift_retry`：验证 OpenAI provider 不受 DeepSeek 纠错影响（1 次失败即终止）。
3. 真实 API 有界复验：
   - 任务 `3a591bfdaac14dbbb254ae8b9e138e85`（Core Job `a6b3e5d63dc64f82a0227ba979ab80c3`）自第 235 步恢复，前 235 步 100% 缓存命中（0 token）。
   - 第 43 章：`analysis:0043:0001` 一次性成功，章节综合成功（完成至 238 步）。
   - 第 44 章：`analysis:0044:0001` 一次性成功，章节综合成功（完成至 241 步）。
   - 第 45 章：首块 `analysis:0045:0001` 初次调用真实触发 schema 漂移并记录 `failed_retryable`，通用纠错机制即时触发并成功修复，紧接着章节综合 `00-0000`、`00-0001`、`01-0000` 及 `analysis:0045` 全部调用成功（完成至 246 步）。
   - 连续通过第 43、44、45 三章后按设计有界中断（interrupted，保持 FAILED_RETRYABLE），未浪费后续 token。前 246 步全部标记为 completed 并安全落盘。
   - 离线测试 154 passed / 1 deselected，项目校验与差异检查全过。
   - 下一步：用户可直接在 Web 界面点击 resume / 运行 `bookcast resume` 继续后续章节（第 46～72 章）及 TTS 合成。

---


## 真实 DeepSeek content synthesis 跨块 claim_ids 归约 business_error 修复与有界验证

基于提交 `913eed7` 及 Markdown 语法剥离修复，用户真实 retry 《狂人日记》推进至第 29 章全部完成、第 30 章首两块分析与首块综合均完成（完成 157 步）后，在 `synthesis/chapters/0030/00-0001`（对第 2 块主题进行归约综合）报 `business_error`：
1. 失败排查现场：
   - 报错位置：`src/bookcast/content.py:185` `ContentFlow.reduce` 的 `validate(value)`：`if any(not set(t.claim_ids).issubset(allowed) for t in value.themes): raise ProviderError(ErrorKind.BUSINESS)`。
   - `events.jsonl` 记录：`error: business_error`, `error_type: ProviderError`, `finish_reason: stop`。DeepSeek HTTP 200，输出 3620 tokens，返回的 JSON 结构完全符合 Pydantic `Synthesis` 结构模型。
   - 根因：在多块主题归约时，模型输出的 theme.claim_ids 偶发外推、伪造了非 allowed 集合的 claim ID 或包含了前后多余空格；原业务逻辑直接使用 `not set(t.claim_ids).issubset(allowed)` 进行严格断言，未先清洗收敛，且 `ProviderError(ErrorKind.BUSINESS)` 被 ProviderChain 视为不可重试的永久业务失败，直接终止任务。
2. 最小修复：
   - 在 `src/bookcast/content.py` 新增 `resolve_synthesis(value: Synthesis, allowed: set[str]) -> Synthesis`，在校验前将 theme.claim_ids 过滤收敛至 `allowed` 有效子集（保留前 8 项），不改变 Prompt 输入（确保前 157 步 Step 指纹不变，100% 缓存命中）。
   - 保留严格业务不变式：若某个 theme 包含的 claim_ids 在清洗后为空（即全部为外推/伪造 ID），则保留原样让后续 `validate` 严格抛出 `ProviderError(ErrorKind.BUSINESS)`。
   - 仅在 `reduce` 阶段通过 `self.call(..., transform=...)` 接入清洗，OpenAI 及其他 Provider 和非综合链路不受影响。
   - 添加回归测试 `test_synthesis_resolves_hallucinated_claim_ids_and_preserves_business_invariant`。
3. 真实 API 有界复验：
   - 任务 `3a591bfdaac14dbbb254ae8b9e138e85`（Core Job `a6b3e5d63dc64f82a0227ba979ab80c3`）执行恢复，前 157 步全部命中检查点缓存秒级跳过（0 token 消耗）。
   - 原失败 operation `synthesis/chapters/0030/00-0001` 真实调用 DeepSeek 成功（第 158 步完成），紧接着后续 operation `synthesis/chapters/0030/00-0002` 真实调用 DeepSeek 成功（第 159 步完成）。
   - 连续通过两步后按设计有界中断（interrupted，保持 FAILED_RETRYABLE），未浪费后续 token。前 159 步全部标记为 completed 并安全落盘。
   - 单元测试 150 passed / 1 deselected，项目校验与差异检查全过。
   - 下一步：用户可直接在 Web 界面点击 resume / 运行 `bookcast resume` 继续第 30 章剩余部分、第 31～72 章及 TTS 合成。

---

## 真实 DeepSeek Markdown 代码块包裹修复与第 22、23 章有界验证

基于提交 `913eed7` 及次级数组截断修复，用户真实 retry 《狂人日记》推进至第 21 章完成后，在第 22 章首块（`analysis:0022:0001`）失败：
1. 失败排查现场：
   - `events.jsonl` 记录：`stage: analysis:0022:0001`, `error_type: ValidationError`, `validation_field: $`, `validation_reason: json_invalid`, `finish_reason: stop`。
   - 根因：DeepSeek 在启用 `response_format: {"type": "json_object"}` 情况下，返回的内容被 markdown 代码块标记包裹（```` ```json\n{...}\n``` ````）。
   - Pydantic v2 `model_validate_json` 在根路径 `$` 报 `Invalid JSON: expected value at line 1 column 1 [type=json_invalid]`；此前适配器直接把 `choice['message']['content']` 送入校验，未做 markdown 语法包裹剥离。
2. 最小修复：
   - 在 `CompatibleLLMProvider` 中增加 `_strip_markdown_fence(text)`，安全剔除前后 ```` ```json ```` / ```` ``` ```` 包裹。
   - 仅对 DeepSeek（`urlsplit(self.spec.base_url).hostname == 'api.deepseek.com'`）生效，OpenAI 及其他兼容端点行为保持严格不变。
   - 保持严格域模型与 Pydantic 字段级校验，不吞咽错误，不放宽 schema。
   - 添加回归测试 `test_real_failure_shape_deepseek_markdown_fence_is_safely_stripped`。
3. 真实 API 有界复验：
   - 任务 `3a591bfdaac14dbbb254ae8b9e138e85`（Core Job `a6b3e5d63dc64f82a0227ba979ab80c3`）执行恢复，前 21 章全部命中检查点缓存秒级跳过（0 token 消耗）。
   - 第 22 章 `analysis:0022:0001` 一次性调用成功（Markdown 包裹成功剥离），章节综合 `00-0000`、`00-0001`、`01-0000` 及 `analysis:0022` 汇总全部完成。
   - 第 23 章 `analysis:0023:0001` 首轮触发数组超限 `failed_retryable`，次轮自动纠错成功，章节综合及 `analysis:0023` 全部完成。
   - 连续通过第 22、23 两章后按设计有界中断（interrupted，保持 FAILED_RETRYABLE），未浪费后续 token。
   - 离线测试 149 passed / 1 deselected，项目校验全过。
   - 下一步：用户可直接在 Web 界面点击 resume / 运行 `bookcast resume` 继续第 24～72 章及 TTS 合成。

---


# 给下一位 Coding Agent

更新时间：2026-09-25。先读 AGENTS.md 和项目文档并核对 Git。已验证功能提交：54257b4883b6d82831fa7c20bbbd777bf4c73714；未 push。

## 2026-09-25 Web 误认 Mock 音频与真实 TTS 恢复修复

用户以为20分钟《狂人日记》retry完成并得到48秒Mock：实际本地两份20分钟《狂人日记》Web Job均FAILED_PERMANENT，保存的settings/manifest配置都是deepseek→kokoro，0次TTS attempt和0个audio WAV。页面显示的Mock完成任务是另一份20分钟PDF《分析师荐股能力评定与跟踪》；Web在选中任务缺席历史列表时回退到首项，造成错认。全局Provider状态不等于任务保存配置。

修改`web/app/page.tsx`避免选中项缺席时展示别的任务音频，Web状态明确给出本任务LLM/TTS配置和“最近调用”。`pipeline.py`对TTS已删除Provider不复用缓存；`speech.py`禁止真实+Mock混链并在完成前校验每段audio_kind/活跃Attempt来源；`content.py`和legacy流程均调用；`jobs.py`对历史配置为真实TTS却留下Mock音频的completed Job只读标为可恢复，隐藏Web下载。没有修改现存用户Job。

离线六模块189 passed，隔离cwd补测1 passed，Web typecheck/build与聚焦Playwright 1 passed。原书第3章短文本用当前保存配置的Kokoro独立合成7.13秒speech WAV，临时文件已删除；没有DeepSeek key，未启动整本retry，原任务manifest SHA保持不变。旧Web服务须重启并刷新前端构建。已验证功能提交589479fcdca65356347b363f15d0aac997198f48，未push。下一步有key时显式retry原Job；无需删Mock音频，因为原Job根本没有Mock音频。

## 2026-09-25 Web 历史记录兼容性追查

`data/web/jobs` 六份submission均合法；三份成功任务当前WebService.status正常，另外三份是《狂人日记》失败任务，见本地被忽略的数据目录，非测试垃圾。旧Web进程不能解析上次补丁写入manifest v3 Attempt的error_type/validation_field/validation_reason（extra=forbid），页面误报损坏；当前代码的history可读全部六份。现将诊断字段从manifest序列化排除，保留events.jsonl日志；未修改任何任务数据。重启Web服务后刷新页面，失败任务应显示真实FAILED_PERMANENT。若需继续内容生成，显式retry，不删除目录。专项74 passed；已验证功能提交18372283b55680d294d7c64f07b70a78fb5d6495，未push。

## 当前目标和证据

用户要求优化最终 2~3 分钟的中文 A/B 试听播客，不改变 LLM 生成的 `script` 内容，且做到 0 次 LLM API 调用。旧版存在的明显问题是 80 字符的生硬分段边界，没有文本清洗（Markdown 和符号被一字一句念出），以及毫无差别的 0.18 秒停顿，极其破坏双人闲谈的沉浸感。

## 修改和验证

- `src/bookcast/speech.py`：新增 `normalize_tts_text` 剥离 Markdown 符号和括号内的辅助内容，放宽 `split_text` 的 limit 到 200，并实现了基于中文标点和避免截断英文/数字的智能标点回退。新增基于 `turn_index` 解析来制定不同停顿时间的动态切分机制。
- `src/bookcast/audio.py`：扩展了 `concat_wav` 接收 `pause_seconds: list[float]` 的能力，实现了从固化常数到细粒度拼缝停顿的转变。
- 采用更搭配双人聊天的 `zf_xiaoxiao` 与 `zm_yunyang` 代替原有的发音人配置，速度微调至 `1.05`。
- 测试 `test_tts.py` 全部回归通过。成功使用 `v5` 历史 `script` Artifact 零 API 成本生成了 Baseline 和 Optimized 版本的 2 分钟 A/B 对比音频，存放在 `output/tts-ab`，修改全部合并推送（`0607c83`）。

## 下一步

人工审查并对比听感。对于 TTS 后端无需再深入调参，下一步可能重点是对已有阶段（比如 `planning`、`synthesis` 或 `claims` 等环节）进行其他的架构精简或 prompt 优化，如果用户不主动指派可进行其他 Phase17 剩余阻塞排查，如 Qwen、Gutenberg 和版权依赖问题。
---

# 给下一位 Coding Agent

更新时间：2026-09-24T05:16:45Z。按 [AGENTS.md](../AGENTS.md) 阅读项目文档并核对实际代码与 Git。OCR 详情见 [OCR.md](OCR.md)，架构决定见 D-021；聊天记录不是项目状态。

## 当前目标与代码状态

Phase 17 可选本地 OCR 工程验收完成，V1 原生解析和后续 Content/LLM/TTS/Export 保持原链路。`bookcast inspect-document 文件.pdf` 在隔离进程分类 text/image/mixed/blank；显式 `bookcast generate 文件.pdf --ocr auto`（也支持 EPUB）才在 macOS 使用本地 Apple Vision。未启用 OCR 的扫描 PDF 不会假装完整。BookCast 仍为 0.1.0 / pre-1.0，项目 LICENSE、PyMuPDF 许可路径及 Kokoro espeak-ng-data 通知继续阻止可再分发 V1 包；OCR 不阻塞既有主流程。

## 刚完成的工作与关键文件

- [document_extraction.py](../src/bookcast/document_extraction.py)：中立 OCR 契约、隔离解析/检测及安全错误；[apple_vision_ocr.py](../src/bookcast/adapters/apple_vision_ocr.py)：本机 Vision 中英识别，不传 AI 凭证。
- [pdf_extraction.py](../src/bookcast/pdf_extraction.py)：先分类，再定向识别扫描区域；限制源大小、页数、像素、区域、文本及进程时间。[parsers.py](../src/bookcast/parsers.py)：仅 OCR EPUB 包内图片，不访问文档 URL。
- [models.py](../src/bookcast/models.py) 保留源 SHA、页/资源、区域、置信度；[pipeline.py](../src/bookcast/pipeline.py) 将 OCR 配置及适配器版本纳入 parse 指纹；[cli.py](../src/bookcast/cli.py) 增加显式入口。[test_ocr.py](../tests/test_ocr.py) 覆盖安全反例、恢复和真实 Vision。
- README、PRODUCT、ARCHITECTURE、ROADMAP、DECISIONS、OCR、STATE 和 WORKLOG 已同步。没有安装 Tesseract、下载模型、调用 DeepSeek/Gemini 或重跑已有真实音频。

## 已运行测试及结果

- 默认完整离线 `pytest -q`：410 passed、1 skipped、5 deselected、10 subtests、7 个既有依赖 warning。OCR 模块默认 12 passed、真实系统 1 skipped。
- 显式 `BOOKCAST_RUN_OCR_LIVE=1 .venv/bin/pytest -q tests/test_ocr.py -k real_chinese`：1 passed；自制中文/英文扫描图、90° PDF 与隔离子进程均实际识别。Swift 编译缓存需要本机用户缓存写入权限，无云 API。
- `python3 scripts/validate_project.py`、`compileall`、`git diff --check` 通过；功能提交上已复验项目校验、编译和 `git diff HEAD^ HEAD --check`。完成 parse 后恢复测试确认 Chapter 产物 mtime 未变、无再次 OCR。

## 未解决问题与下一步

1. OCR 仅 macOS/Swift/Apple Vision 显式可用。复杂多栏、表格、脚注、低质扫描、矢量描边字及非 macOS 均未验收；后续应用有权使用的样本人工核对原页，不得将技术成功写成普适准确率。
2. 当前 OCR 恢复粒度是整份文档 parse Step；提交前强杀可能重做识别，提交后 SHA 有效则直接复用。逐页持久缓存需另行设计并沿用 Job/Artifact 状态机。
3. 处理发布许可/资产通知、Phase16 Medium/Low backlog、Qwen 可选依赖公告和公共 DNS 下全新 Gutenberg 获取复验。

## Git 与不要重复做的事情

本阶段接手时 `main`/`origin/main` 同为 `da4ca8e465dfa5037f6c451b2348d689aa5d1638`；Phase16 旧交接的“尚未推送”已过期。最新已验证功能提交为 `3ff4eb360e4fcf9ecfffd7cafa280be4246a769c`（`feat: add opt-in local OCR extraction for PDFs and EPUBs`）；交接快照自身的 HEAD 必须用 `git log -1` 查询，STATE 不自引用。Phase17 未主动 push，远端 CI 不可冒充本地验证。不要重复生成已有 DeepSeek/Gemini/Kokoro/Qwen 任务，不要下载大型模型，不要为了 OCR 改写内容或语音 Provider。
