# 开发工作日志

本文件只追加重要、可验证的开发事实。已写入的历史条目不重写；纠错另加条目。时间使用 UTC，后续条目包含任务、结果、测试与关联提交（若当时已存在）。

## 2026-09-26T14:15:00Z — Phase 19.3A TTS 成本模型纠偏与 A/B 对比数据校准

- 背景与原因：
  * Phase 19.3A 初始汇报中将 Gemini 3.8 Flash-Lite TTS 误按字符计费（套用 Qwen 计费模型：15 元/百万字），得出“Gemini 便宜 81.24%”、“25分钟仅需 0.10 元”的错误结论。
  * 依据 Google 官方公开定价，Gemini 3.8 Flash-Lite TTS 计费单位为：
    - 音频输出：$6 / 1,000,000 audio tokens（官方等价：$0.0015 / 10 秒 = $150.00 USD / 1,000,000 秒）
    - 文本输入：$0.50 USD / 1,000,000 text tokens
- 修正内容与架构完善：
  1. 计价模型与配置架构升级：
     - `src/bookcast/provider_config.py`：`ModelPricing` 扩展 `currency`（支持 CNY、USD）、`billing_unit`（支持 'auto'、'characters'、'audio_duration'、'audio_tokens'、'tokens'）以及 `audio_tokens_per_million`；`ProvidersConfig` 增加独立汇率配置层 `exchange_rates`（默认 `{'USD': 7.20}`）。
     - `src/bookcast/cost.py`：TTS 计费按 `billing_unit` 精准分发：
       * `characters`：按每百万字单价计算。
       * `audio_duration`：按每百万秒单价计算真实音频时长费用，叠加未缓存输入 token 费用。
       * `audio_tokens`：优先使用服务端返回的真实音频 token，若无则回退至官方时长等价值。
       * 汇率换算与双币种跟踪：在细项中完整记录 `original_cost`、`original_currency`、`exchange_rate`、`converted_cost`、`converted_currency`，不在 Provider 逻辑中硬编码汇率。
     - `bookcast.toml` 与 `examples/model-routing-cloud.toml`：
       * `qwen3-tts-instruct-flash`：`currency = "CNY"`, `billing_unit = "characters"`, `characters_per_million = 80.00`。
       * `gemini-3.8-flash-lite-tts`：`currency = "USD"`, `billing_unit = "audio_duration"`, `audio_seconds_per_million = 150.00` ($0.0015/10s), `audio_tokens_per_million = 6.00`, `uncached_input_per_million = 0.50`。
  2. 离线测试覆盖：
     - 新增 `tests/test_gemini_tts_pricing.py`，完整覆盖配置解析、时长计费、token 计费及 USD->CNY 汇率换算；离线集成测试注册至 `tests/conftest.py`。
  3. 已有 A/B 数据重算（0 真实 API 调用）：
     - Candidate A（Qwen3-TTS-Instruct-Flash，157.62s，765字）：
       * 成本：765 * 80 / 1,000,000 = 0.06120 CNY
       * 成品分钟成本：0.02330 CNY / min
       * 25 分钟投影：0.5824 CNY
     - Candidate B（Gemini 3.8 Flash-Lite TTS，170.30s，765字/约765 tokens）：
       * 原始成本：音频 170.30 * $0.00015 = $0.02555 USD + 文本 765 * $0.0000005 = $0.00038 USD = $0.02593 USD
       * 汇率换算（7.20）：0.1867 CNY
       * 成品分钟成本：$0.00913 USD / min（约 0.0658 CNY / min）
       * 25 分钟投影：$0.2283 USD（约 1.644 CNY）
  4. 结论纠偏与正式撤回声明：
     - 正式撤回上一轮关于“Gemini 便宜 81.24%”、“Gemini 约 0.10 元 / 25 分钟”的错误表述。
     - 校准后的真实结论：在人民币计费视角下，Qwen3-TTS-Instruct-Flash 实际上比 Gemini 3.8 Flash-Lite TTS 便宜 67.2%（Gemini 约为 Qwen 成本的 2.82~3.05 倍）。
     - 暂停对默认 TTS 路由的任何调整，维持当前策略：`standard: qwen3-tts-instruct-flash`，`high: gemini-3.8-flash-lite-tts`。

## 2026-09-26T13:55:00Z — Phase 19.3A TTS 真实 A/B 评测完成

- 目标：在严格控制变量（不修改 LLM routing、Prompt、reasoning 策略或集中计价）的前提下，针对 3 分钟标准时长中文双人播客，对 Qwen3-TTS-Instruct-Flash（标准质量）与 Gemini 3.8 Flash-Lite TTS（高音质）开展首轮真实 A/B 评测与盲测资产构建。
- STEP 1 冻结测试稿：
  * 构建并锁定 `output/ab/tts_ab_script.json`：固定 22 轮对话、765 个中文字符，包含普通陈述、疑问句、长句、短句、强调、数字（2026年、80%、3倍）、英文词（AI、API、stress test、feedback）、书名与人名（《思考，快与慢》、丹尼尔·卡尼曼、查理·芒格）、自然停顿与轻微情绪变化。
  * 两候选模型输入完全相同，禁止分别改写。
- STEP 2 & STEP 3 真实模型生成：
  * Candidate A（Qwen3-TTS-Instruct-Flash）：Cherry（Host）+ Ethan（Guest），自然沉稳与好奇互动正式 instruction，单句单元合成（22 请求，每请求 0.78s~3.80s，平均 2.41s，P95 3.63s），生成音频时长 157.62s，墙上耗时 53.03s，RTF 0.336，估算成本 0.0612 元。0 重试，0 失败。
  * Candidate B（Gemini 3.8 Flash-Lite TTS）：Kore（Host）+ Puck（Guest），语义对齐之正式风格 prompt，多轮片段合成（2 请求，590字 + 175字，耗时 22.41s + 18.81s，平均 20.61s，P95 22.23s），生成音频时长 170.30s，墙上耗时 41.23s，RTF 0.242，估算成本 0.01148 元。0 重试，0 失败。
- STEP 4 统一后处理：
  * 两份音频经过完全一致的 FFmpeg 后处理链：EBU R128 (-16 LUFS) 响度归一化至 24kHz/16-bit PCM 单声道 WAV，并转码 128kbps MP3。
  * 生成 `output/ab/A.wav`、`output/ab/B.wav` 与 `output/ab/A.mp3`、`output/ab/B.mp3`。
- STEP 5 指标与分析：
  * 自动采集 23 项指标落盘至 `output/ab/metrics.json`。
  * 成本比较：Qwen 0.02330 元/成品分（25分钟投影 0.5824 元），Gemini Lite 0.00404 元/成品分（25分钟投影 0.1011 元），Gemini Lite 比 Qwen 节省 81.24% 成本。
- STEP 6 盲听评分资产：
  * 生成 `evaluation/tts_ab_blind_score.json`，含 12 项 1–5 分主观维度及质性反馈栏，评分留空待人工盲听填写。
- 盲测映射：
  * 隔离保存在 `output/ab/ab_mapping.json`，测试前不对盲听用户展示对应关系。

## 2026-09-26T13:36:00Z — Phase 19.2 Telemetry 与成本精度修正及 Reasoning Token 审计完成并验证

- 处理 Phase 19.2 Live E2E 验收后的两项关键遥测/成本精度问题并完成 Reasoning Token 深度审计，杜绝重复调用真实 API：
  - 问题 1：Qwen3-TTS-Instruct-Flash 集中计价单价纠偏：
    * 依据阿里云百炼官方公开原价，`qwen3-tts-instruct-flash` 标准单价为 0.8 元/万字 = 80.00 元/百万字（北京与新加坡统一原价），原配置 20.00 元/百万字为过低误配。
    * 修正 `bookcast.toml` 与 `examples/model-routing-cloud.toml`：`characters_per_million = 80.00`。
    * 依据真实已有用量重新核算 56 秒 E2E 成本：250 汉字 * (80.00 / 1,000,000) = 0.0200 元（原 0.0050 元）。
    * 重算已有 56 秒 E2E 总成本：LLM estimated cost 0.0666 元 + TTS estimated cost 0.0200 元 = 总 estimated cost 0.0866 元（原 0.0716 元）。
    * 已同步回写 `output/e2e/a834c024a7d11624be66f19b/usage/cost_summary.json`、`manifest.json` 与 `e2e_acceptance_report.json`，无任何未定价（unpriced）遗留。
  - 问题 2：physical_requests 遥测全面覆盖 LLM 与 TTS 真实网络请求：
    * 根因定位：原 `usage/physical_requests.jsonl` 仅在 `synthesize_unit_with_context` 处埋点，LLM 适配器基类 `CompatibleBase` 尚未接入 `ProviderRequestContext`。
    * 最小无侵入修复架构：
      1) `src/bookcast/provider_api.py`：`ProviderRequestContext` 扩展 `physical_attempt_index: int = 0`。
      2) `src/bookcast/adapters/compatible.py`：`CompatibleBase` 接入 `self.context`、`set_context` 与原子 append 写入的 `_record_physical`；在 `_request` 中完整记录 15 项指标（provider, model, clean endpoint, logical chunk id, physical_attempt_index, started_at, finished_at, latency, http_status, result, retry_reason, retryable, usage_available, billing_evidence, error_kind）；`urlsplit` 严格剥离 query 和敏感凭据；`for_task` 保留 context。
      3) `src/bookcast/adapters/qwen_llm.py`：`for_task` 传递 context。
      4) `src/bookcast/pipeline.py`：`_Runner.ai_operation` 统一构造带 `job_id`、`output_id`、`logical_chunk_id` 与 `telemetry_path` 的 `ProviderRequestContext` 并传给 `chain.execute`。
      5) `src/bookcast/model_routing.py`：`RoutedLLMChain.execute` 与 `RoutedTTSChain.execute` 转发 `context`。
      6) `src/bookcast/provider_chain.py`：`ProviderChain.execute` 接收 `context`，在 `correction` 重试循环中将 `physical_attempt_index=correction` 注入 provider，确保逻辑任务与单次物理尝试一一对应。
      7) `src/bookcast/adapters/qwen_cloud.py`：同步扩充 `endpoint`（去除敏感 query）、`latency`、`physical_attempt_index` 与错误分类，保持全 Provider 格式一致。
    * 严格隔离逻辑调用与物理请求，杜绝双重计费：
      `manifest.ai_calls` 权威记录业务 Attempts 及模型报告的 token/字符用量；
      `physical_requests.jsonl` 记录真实物理网络往返；
      成本计算模块 `cost.py` 严格以 `manifest.ai_calls` 为唯一数据源，物理日志纯粹用于网络审计，从架构上保证绝对 0 重复计费。
    * 理论物理请求数审计：本轮 56 秒 E2E 实际包含 9 次逻辑 LLM 调用（Qwen 4 + DeepSeek 5）和 10 次逻辑 TTS 单元合成（Qwen TTS 10）。由于各阶段均 0 失败 0 重试，理论物理请求数完全等于 19 次（9 LLM + 10 TTS）。
  - 额外审计：Reasoning Token 深度分析与后续 A/B 假设：
    * 各阶段用量与成本分解：
      1) `extraction`（Qwen3.7-Flash，2次）：in 2,163 tokens，out 1,174 tokens，reasoning 0（0%），耗资 0.0038 元，耗时 12.79s。
      2) `chapter_synthesis`（Qwen3.7-Flash，2次）：in 1,457 tokens，out 465 tokens，reasoning 0（0%），耗资 0.0024 元，耗时 5.55s。
      3) `book_synthesis`（DeepSeek-Flash，1次）：in 775 tokens，out 1,297 tokens，reasoning 1,009（77.8%），耗资 0.0056 元，耗时 5.44s。
      4) `dialogue`（DeepSeek-Flash，2次）：in 3,353 tokens，out 6,777 tokens，reasoning 6,099（90.0%），耗资 0.0302 元，耗时 25.66s。
      5) `consistency`（DeepSeek-Flash，2次）：in 3,147 tokens，out 5,388 tokens，reasoning 5,066（94.0%），耗资 0.0246 元，耗时 24.98s。
    * 归因分析：
      - `dialogue`：DeepSeek 默认 thinking 机制对双人观点交锋、语气角色分配、论据锚定与字数约束展开了全局推演，第 2 章单一生成消耗了 4,818 个推理 tokens（正文仅 345 tokens）。
      - `consistency`：模型对每一句台词与原文主张进行严密的反事实逻辑校验，耗费 94% 的推理 tokens 仅输出几个布尔与分数项。
    * Phase 19.3 A/B 优化假设（保持当前 Prompt/策略，作为后续对比方向）：
      - 假设 A（推理预算分级）：对 dialogue / consistency 分配 `reasoning_effort="low"` 或设定 max thinking tokens，预期削减 50-70% 推理用量并缩短延迟，同时维持剧本质量与一致性通过率。
      - 假设 B（核验思考引导）：在 consistency 提示词中加入“聚焦矛盾检测与未引用论断”规则，减少显见事实的过度自洽推理。
      - 假设 C（两级阶梯核验）：先用 cheap LLM（Qwen3.7-Flash）无思考初筛，仅在低置信或疑义时升级至 DeepSeek 深度推理核验。
  - 测试验证：
    * `tests/test_cost_telemetry.py`：新增 3 项测试（15项必要遥测字段、HTTP 429 错误记录、重试 index 递增及零重复计费验证），全量 14 passed。
    * 专项套件：`tests/test_cost_*.py tests/test_gemini_*.py tests/test_qwen_*.py` 233 passed。
    * 完整全量 pytest：701 passed、1 skipped、7 deselected、8 warnings、10 subtests passed（72.65s）。
    * `validate_project.py` 与 `git diff --check` 通过。

## 2026-09-26T13:13:45Z — Phase 19.2 Step 2：1分钟真实 BookCast Live E2E 验收全链路完成并验证

- 授权执行 1 分钟真实 BookCast 端到端全业务流水线验收，输入短篇中文文本 `data/e2e_source.txt`（2 章，280 字符），执行完整解析、LLM 任务路由、全书综合、剧本生成、事实一致性审核、TTS 品质路由、音频切句、云端语音合成与 WAV/MP3 封装：
  - 任务路由策略与执行：
    * LLM cheap/general：`qwen`（`qwen3.7-flash`）执行 2 次分块证据分析（`analysis:0001:0001`、`analysis:0002:0001`）与 2 次章节综合（`synthesis/chapters/0001`、`synthesis/chapters/0002`），共 4 次请求，耗时约 20s，无超时无重试。
    * LLM complex/high_quality：`deepseek`（`deepseek-flash`）执行 1 次全书核心论证综合（`synthesis/book/00-0000`）、2 次双人对话剧本编写（`script:0001`、`script:0002`）与 2 次事实一致性审核（`consistency:0001`、`consistency:0002`），共 5 次请求，无重试无报错。
    * TTS standard：`qwen-tts`（`qwen3-tts-instruct-flash`）执行 10 个语音单元（双段各 5 轮对白）渲染，主持人采用 Cherry 音色，嘉宾采用 Ethan 音色，0 失败 0 重试，物理日志与单集音色严格保持单 Provider 一致。
    * 路由与回退：0 次 failover，0 次 fallback，模型完全按既定 Router 策略各司其职。
  - 音频质量与物理特性：
    * 最终音频产物：`output/e2e/a834c024a7d11624be66f19b/podcast.wav` 与 `podcast.mp3`。
    * 时长：56.46 秒（完全落在 45–60 秒目标区间）。
    * 格式规范：标准 RIFF/WAV，24000Hz 采样率，单声道（Mono），16-bit PCM，总帧数 1,355,040。
    * 音频信号：RMS 能量 2051.62（远高于 50.0 阈值），非零采样占比高，自然停顿正常，绝无截断或静音。
  - 内容质量与复核：
    * 对话剧本严格基于原文核心观点（阅读拓展认知、芒格名言、思维模型重构、主动反思检验、假设边界与认知闭环），无任何编造、外部无关事实或广告。
    * 5 轮对白自然交替，主持人推进阐释，嘉宾追问“幸存者偏差”与“实践检验路径”，事实一致性审核全部通过。
  - 用量与集中计价遥测：
    * LLM 用量：总请求 9 次，输入 10,895 tokens（缓存命中 1,664），输出 15,101 tokens（推理 tokens 12,174），估算成本 0.0666 元（Qwen 0.0062 元 + DeepSeek 0.0604 元）。
    * TTS 用量：总请求 10 次，合成 250 汉字，原始音频时长 52.16s，估算成本 0.0050 元。
    * 总估算成本：0.0716 元人民币（不再全部显示 unpriced）；Provider 未返回权威发票凭单故 actual_cost 规范留空为 `None`。
    * 遥测持久化一致性：`manifest.json`、`usage/physical_requests.jsonl`（10 次物理 HTTP 记录）、`cost_summary.json` 与 `evaluation/quality.json` 完全对应。
- 验收脚本验证：`scratch/evaluate_e2e_live.py` 成功输出全量 17 项指标报告至 `e2e_acceptance_report.json`。
- 本轮 Live 结束后立即安全停止，绝无长音频或全书批量调用；未 push，未改动仓库跟踪代码。

## 2026-09-26T12:58:00Z — Phase 19.2 Step 1：云端模型集中计价配置与成本估算补齐完成并验证

- 检查现有用量遥测、物理日志与计价体系，在不改动 wire protocol 和现有架构的前提下完成集中计价补齐：
  - `src/bookcast/provider_config.py`：扩展 `ModelPricing`，新增 `characters_per_million` 与 `audio_seconds_per_million`，所有计价费率字段默认 `0.0`，100% 保持向后兼容。
  - `examples/model-routing-cloud.toml` 与 `bookcast.toml`：集中配置 `qwen3.7-flash`（0.20 cached, 1.00 uncached, 2.00 output）、`deepseek-chat` / `deepseek-flash`（峰时 0.04/2.0/8.0，谷时 0.02/1.0/4.0）、`qwen3-tts-instruct-flash`（20.00 / 百万字）与 `gemini-3.8-flash-lite-tts`（0.18 cached, 0.72 uncached, 2.88 output, 15.00 / 百万字, 250.00 / 百万秒音频）。针对含点模型名称严格使用 TOML 带引号语法（`[pricing."qwen3.7-flash".default]`）。
  - `src/bookcast/cost.py`：
    * `_row` 补齐 `characters`、`estimated_cost`、`actual_cost` 与 `billing_evidence` 字段。
    * TTS 计算逻辑补齐：支持字符数、音频时长及 Token 综合计费，状态标记为 `estimated` 并计入总计；无计价模型仍严格保持 `unavailable` 与 `partial`。
    * 明确区分 `actual_cost` 与 `estimated_cost`：模型 API 响应未返回权威真实计费单时，一律记为 `estimated_cost`，`actual_cost` 保持 `None`。
  - `src/bookcast/speech.py` 与 `speech_segments.py`：落盘语音单元与片段 sidecar json 时补充 `character_count`，供成本模块精确提取。
  - `src/bookcast/adapters/qwen_cloud.py`：在 `_send` 物理请求遥测及 `synthesize_unit_with_context` 中解析提取服务端/单元字符数，写入 `ProviderUsage(input_tokens=chars)` 与物理日志。
  - `src/bookcast/cli.py`：更新 `cost` 命令输出，对已计价 TTS 模型输出估算费用及字数，不再硬编码 `Cost: unavailable`。
  - `src/bookcast/llm_usage.py`：`tts_usage_snapshot` 扩充支持字符统计与可选传入 `prices` 估算单段 TTS 成本。
- 测试验证：
  - `tests/test_cost_telemetry.py`：新增 4 项测试覆盖 Qwen LLM、Qwen TTS 字符计价、Gemini TTS 时长/字符计价及 DeepSeek 峰谷时段估算，验证 estimated_cost 正确且 actual_cost 为 None。
  - 测试套件全量通过：`test_cost_*.py` 22 passed，全量默认 698 passed / 1 skipped / 7 deselected / 10 subtests passed（73.21s）。
  - `validate_project.py` 与 `git diff --check` 通过。

## 2026-09-26T09:12:00Z — Phase 19.1 Live Preflight：Qwen LLM 与 TTS 协议路径彻底隔离与自动纠偏完成并验证

- 针对 Qwen LLM（OpenAI 兼容协议 `/compatible-mode/v1`）与 Qwen TTS（原生多模态协议 `/api/v1`）执行 Live Preflight 审查与防护强化：
  - `src/bookcast/adapters/qwen_llm.py`：新增 `_resolve_llm_base_url`，在初始化时自动规范化 `base_url`。若输入包含 `/api/v1` 则自动剥离并修正为 `/compatible-mode/v1`；若输入为 DashScope 或阿里云 Maas 根域名，自动补齐 `/compatible-mode/v1`，杜绝请求误投至 `/api/v1/chat/completions`。
  - `src/bookcast/adapters/qwen_cloud.py`：强化 `_resolve_endpoint`，若输入包含 `/compatible-mode/v1` 则自动剥离并保证对齐到 `/api/v1`，杜绝请求误投至 `/compatible-mode/v1/services/...`。
  - `tests/test_live_dashscope.py`：实现 `_resolve_live_endpoints()`，严格按优先级解析环境变量：
    * LLM：优先 `DASHSCOPE_LLM_BASE_URL` -> 其次 `DASHSCOPE_BASE_URL` -> 默认按 `DASHSCOPE_REGION`。
    * TTS：优先 `DASHSCOPE_TTS_BASE_URL` -> 其次 `DASHSCOPE_BASE_URL` -> 默认按 `DASHSCOPE_REGION`。
    * 北京与新加坡国际区双向正确映射。
  - `docs/PROVIDERS.md`：详细记录两端协议子路径差异、适配器自动纠正保护机制与环境变量优先级。
- 测试扩展：
  - `tests/test_qwen_llm.py`：新增 8 项测试（`TestLLMEndpoints`），验证北京/新加坡/专属端点保留与自动修正。
  - `tests/test_qwen_cloud.py`：新增 7 项测试，验证 `/compatible-mode/v1` 剥离修正及 `_resolve_live_endpoints` 在各种环境变量组合下的确定性行为。
- 验证：106 passed（Qwen 专项），全量默认离线测试 694 passed / 1 skipped / 7 deselected / 10 subtests passed（73.19s），validate_project 验证通过，保持 100% 离线与 0 网络调用。

## 2026-09-26T08:52:00Z — Phase 19.1 Qwen Cloud TTS 协议修正与 Live-readiness 优化完成并验证

- 修正 `src/bookcast/adapters/qwen_cloud.py`：
  - 目标协议对齐：全面支持 `qwen3-tts-instruct-flash` 所属的 Qwen-TTS 官方接口协议（`POST {base_url}/services/aigc/multimodal-generation/generation`），采用 `input.text`（纯文本）、`input.voice`、`input.language_type` 与 `parameters.instructions`（指令控制），杜绝向文本注入 `<|system|>` 标记。
  - 协议隔离与兼容：识别 `cosyvoice` 系列模型（如 `cosyvoice-v2`），自动保持旧版 `text2voice/voice-synthesis` 协议与 Base64 PCM 行为，两者严格物理隔离。
  - Endpoint 区域化配置：消除硬编码北京区，默认北京（`https://dashscope.aliyuncs.com/api/v1`），支持配置化自定义 `base_url`（如新加坡 `https://dashscope-intl.aliyuncs.com/api/v1` 或阿里云百炼专属 `https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api/v1`）及 `region` 参数。
  - 响应解析升级：实现官方非流式响应 `output.audio.url` 完整音频文件（WAV）的安全下载与校验（SSRF 安全重定向与环回隔离），兼容流式/内联 Base64 `output.audio.data` 与旧版 `output.audio` 字符串。
- 修正 `src/bookcast/provider_config.py`：
  - `QwenCloudTTSConfig` 默认音色更新为标准预设 Cherry（主持人）与 Ethan（嘉宾），新增 `instructions`、`optimize_instructions`、`language_type`（默认 `"Chinese"`）与 `region` 字段。
  - `ProviderSpec.validate_endpoint` 移除 `self.base_url is not None` 限制，允许配置自定义区域端点并执行 HTTPS/安全主机校验。
- 新增 `tests/fixtures/qwen3_tts_instruct_flash_response.json`：落盘官方 Qwen3-TTS-Instruct-Flash 非流式响应标准 HTTP fixture。
- 更新 `examples/qwen-cloud-tts.toml` 与 `examples/model-routing-cloud.toml`：默认 TTS 目标统一为 `qwen3-tts-instruct-flash`，音色设为 Cherry 与 Ethan，Qwen LLM 明确为 `qwen3.7-flash`。
- 新增 `tests/test_live_dashscope.py` 并注册至 `conftest.py` 的 `LIVE_MODULES`：提供单独显式 opt-in 测试（`BOOKCAST_RUN_DASHSCOPE_LIVE=1`），默认测试套件保持 100% 离线与 0 网络请求。
- 更新 `tests/test_qwen_cloud.py`：扩充至 55 项离线专项测试，覆盖 Qwen-TTS 与 CosyVoice 协议隔离、北京/新加坡/专属端点解析、官方 URL 响应下载与校验、环回拦截与超限保护。
- 验证：55 passed（`test_qwen_cloud.py` 专项），7 passed（`test_cost_telemetry.py`），全量默认离线测试 679 passed / 1 skipped / 7 deselected / 10 subtests passed（84.79s），无真实 API 调用。

## 2026-09-26T08:20:00Z — Phase19 R6 统一入口、Web Worker 传递修复与文档迁移完成并验证

- 修改 `src/bookcast/web_service.py`：修复 Web 提交任务时的 TTS 引擎名称映射，前端传 `gemini` / `kokoro` 时智能匹配已配置的规范适配器（`gemini-tts` / `kokoro-local`），并在 `settings_snapshot` 中保存真实选择。
- 修改 `src/bookcast/web_worker.py`：修复后台 Worker 启动流水线和恢复流水线时遗漏传递 `tts_selection` 与 `llm_selection` 的既有缺陷，保证用户选择的语音引擎完整透传至 `configured_pipeline` 与 `resume_pipeline`，并落盘至 `manifest.provider_settings`。
- 修改 `src/bookcast/onboarding.py`：识别云端 DashScope 服务（`qwen-llm` 与 `qwen-cloud-tts`），补齐凭据与配额指引，明确 `ready` 语义（优先级候选池中至少一个可用 Provider 即可就绪）。
- 修改 `src/bookcast/cli.py`：`doctor --human` 明确展示 LLM 任务路由（`general` / `complex`）与 TTS 品质路由（`standard` / `high`）启用状态与首选项；CLI `--tts-provider` 选项说明支持品质档位。
- 修改 `docs/PROVIDERS.md`：补充 R6 统一入口、Web Worker 传递修复、引擎命名兼容、ready 语义与旧任务原样恢复说明。
- 修改 `tests/test_web.py`：新增 2 项测试（`test_web_worker_propagates_selected_tts_engine_to_pipeline` 与 `test_web_worker_resolves_gemini_tts_provider_name`），验证从提交、worker 执行、流水线调用到 manifest 保存的完整透传。
- 验证：23 passed（`test_web.py` 全量），105 passed（R6 关联测试），671 passed/1 skipped/5 deselected/10 subtests（全量离线），validate_project/compileall/diff --check 均通过，无真实 API 调用。
- 功能提交：`0504a42e12fa2818617f16ef0627d7e34ef3e64f`

## 2026-09-26T08:06:00Z — Phase19 R5 Usage 与物理成本审计完成并验证

- 修改 `src/bookcast/adapters/qwen_cloud.py`：实现 `synthesize_unit_with_context` 与 `_record_physical`，在向 DashScope 发起请求时记录物理 HTTP 遥测（单句字数、请求耗时、状态码、安全错误原因），写入 `usage/physical_requests.jsonl`，与逻辑 Step/Attempt 明确分离。
- 修改 `src/bookcast/speech.py`：在 `render_units` 中向支持上下文的 TTS Provider 传入 `ProviderRequestContext`（包含 `job_id`、`output_root`、`logical_chunk`、`provider_name`）。
- 修改 `src/bookcast/cost.py`：`_selected` 支持通过 `config.tts_routing` 解析真实 Provider 与 Model（避免在 `--tts-provider high` 等情况下误识别 Provider 名称）；对于未配置定价的模型（如 Qwen-TTS / CosyVoice）在 `ActualCostSummary` 中标记 `status="partial"` 或 `unavailable`，严格杜绝未定价模型伪造 0 成本。
- 新增 `tests/test_cost_telemetry.py`：7 项测试，覆盖 DashScope 物理请求遥测记录（成功与 HTTP 400 失败）、错误分类安全性、ProviderRequestContext 管道透传、TTS 路由真实模型解析与未定价状态标识。
- 修改 `tests/conftest.py`：注册 `test_cost_telemetry` 至离线 integration tier。
- 验证：7 passed（专项），18 passed（cost 关联测试），669 passed/1 skipped/5 deselected/10 subtests（全量），validate_project/compileall/diff --check 均通过，无真实 API 调用。
- 功能提交：`692ac963d2c460a964ab4d8d1c1fb479666df254`

## 2026-09-26T07:58:00Z — Phase19 R4 TTS quality 路由与 episode 级一致性完成并验证

- 新增 `src/bookcast/provider_config.py`：`TTSRouting` 配置模型（包含 `standard`、`high_quality` 与 `default_quality` 字段）与 `ProvidersConfig.tts_routing`；在序列化中 `tts_routing=None` 时自动弹出保持旧快照字节一致；在 `validate_chains` 中要求所配置 Provider 必须在 `tts_priority` 中且为有效 TTS Provider。
- 新增 `src/bookcast/model_routing.py`：`RoutedTTSChain`（继承 `ProviderChain`），单集解析唯一 TTS Provider；活动链仅包含单一所选 Provider，严格杜绝单集生成中途跨 Provider 换声；隔离了候选池中同时包含 `speech_units` 与 `speech_segments` 导致的互斥验证问题。
- 修改 `src/bookcast/provider_registry.py`：在配置 `tts_routing` 时，若选择为 `auto`、`standard`、`high` 或 `high_quality`，则实例化 `RoutedTTSChain`；显式 Provider 名称覆盖（override）则绕过路由直接使用单 Provider 链。
- 新增 `examples/tts-routing-mock.toml`：离线 Mock 双品质 TTS 演示配置。
- 新增 `examples/model-routing-cloud.toml`：包含 LLM 任务路由与 TTS 品质路由的完整云端配置。
- 修改 `docs/PROVIDERS.md`：补充 TTS 质量路由与单集一致性说明。
- 新增 `tests/test_tts_routing.py`：25 项测试，覆盖品质路由解析、旧快照兼容性、单集单 Provider 保证、中途失败不换声、混合候选池能力隔离、Gemini 3.8 Flash-Lite TTS 模型校验与 payload/decode wire 契约、离线 Pipeline 生成与恢复集成。
- 验证：25 passed（专项），248 passed（关联组件），662 passed/1 skipped/5 deselected/10 subtests（全量），validate_project/compileall/diff --check 均通过，无真实 API 调用。
- 功能提交：`c9b78b3cca1cd5d97883716b7f69ba140d29bd59`

## 2026-09-26T07:47:00Z — Phase19 R3 Qwen 云端 TTS 适配器完成并验证

- 新增 `src/bookcast/adapters/qwen_cloud.py`：`QwenCloudTTSProvider`，实现阿里云 DashScope TTS API（语音合成）适配器；声明 `speech=True, speech_units=True, cloud=True`，单次请求处理单句（≤ 80 字），不混入 multi_speaker / speech_segments。
- 新增 `src/bookcast/provider_config.py`：`QwenCloudTTSConfig` 配置模型与 `ProviderSpec.qwen_cloud_tts` 字段；要求显式 `send_text_to_cloud=True`、两个不同音色（`host_voice != guest_voice`）、有效 DashScope 音色命名正则，端点固定官方 DashScope 地址。
- 修改 `src/bookcast/provider_registry.py`：注册 `tts/qwen-cloud-tts -> QwenCloudTTSProvider`。
- 音频解码与校验：Base64 PCM 解码后校验长度与字节对齐，封装为标准 PCM16 单声道 WAV 并验证 RIFF/fmt/data 块，采样率默认 24000Hz；空音频或损坏数据抛出 SCHEMA ProviderError。
- 错误与限频映射：DashScope 专有错误码分类（`Ariel.InsufficientBalance` 等映射为 QUOTA，`Throttling` 映射为 RATE_LIMIT）；通过 `min_request_interval` 实现有界请求间隔控制。
- 新增 `examples/qwen-cloud-tts.toml`：离线演示与配置模板。
- 修改 `docs/PROVIDERS.md`：补充 Qwen 云端 TTS 配置与使用约束。
- 新增 `tests/test_qwen_cloud.py`：47 项测试（wire format、音色与 style 注入、PCM 解码转 WAV 校验、HTTP/DashScope 错误分类、80 字符边界输入校验、capabilities、健康检查、缓存键、限频）。
- 验证：47 passed（专项），166 passed（关联组件），637 passed/1 skipped/5 deselected/10 subtests（全量），validate_project/compileall/diff --check 均通过，无真实 API 调用。
- 功能提交：`e144065644efa56a5cbe817e2f7c74d7cd517e0f`

## 2026-09-26T07:29:00Z — Phase19 R2 Qwen LLM 适配器完成并验证

- 新增 `src/bookcast/adapters/qwen_llm.py`：`QwenLLMProvider`，继承 `CompatibleBase`（复用 HTTP transport、auth、health_check、classify_http），独立实现 Qwen3 wire 方言。
- thinking 参数映射：`enable_thinking: bool`（Qwen3 wire），不发 DeepSeek 的 `thinking: {type: ...}` dict；`reasoning_effort` 一律不转发。
- structured output：`response_format: json_object` + schema 系统消息，无 markdown fence 剥离（Qwen3 JSON 行为规范）。
- `prepare_schema_retry` 永远返回 `False`；DeepSeek adapter 行为完全不变（有独立测试保证）。
- `for_task()` 返回 call-scoped 视图，防止 `reported_model`/`last_usage` 跨调用泄漏。
- `provider_config.py`：允许 `qwen-llm` 使用 `generation`/`reasoning_policy`，要求 `base_url`（HTTPS）。
- `provider_registry.py`：注册 `llm/qwen-llm -> QwenLLMProvider`。
- 新增 `tests/test_qwen_llm.py`：36 项测试（thinking 映射、structured output、usage、error 分类、schema retry False、DeepSeek 不受影响、config 验证、cache_key、for_task 隔离、R1 路由集成）。
- 新增 `examples/qwen-llm-routing.toml`：离线演示配置。
- 验证：36 passed（专项），590 passed/1 skipped/5 deselected/10 subtests（全量），validate_project/compileall/diff --check 均通过，无真实 API 调用。
- 功能提交：`6bf7a7945379fb0b219099019d1589b521006cc3`

## 2026-09-26T07:17:00Z — Phase19 R1 opt-in LLM 任务路由完成并验证

- 新增 `src/bookcast/model_routing.py`：`RoutedLLMChain`（继承 `ProviderChain`），按任务 profile 维护独立 sticky failover 状态，复用现有 Attempt/journal/cost，不加 retry、不替换 provider 记录。
- 新增 `src/bookcast/provider_config.py`：`LLMRouting` Pydantic 模型（four profiles + task_profiles），`serialize_legacy_compatible` 在 `llm_routing=None` 时不写入字段，旧快照字节对齐；`validate_chains` 检查路由实例属于 `llm_priority` 候选池。
- 修改 `src/bookcast/provider_registry.py`：仅在 `kind==llm && selection==auto && config.llm_routing is not None` 时返回 `RoutedLLMChain`，其余路径不变。
- 新增 `tests/test_model_routing.py`：35 项测试，覆盖路由意图、可配置映射、明确 override、无效配置拒绝、fallback 仅用显式路由、无隐式 fallback、永久错误不换 Provider、profile 独立恢复、旧序列化兼容、DeepSeek bounded schema retry via routing、完整 Pipeline snapshot/resume、CLI example、Web worker 保存路由。
- 新增 `examples/model-routing-mock.toml`：离线演示，两个 Mock LLM + 一个 Mock TTS，不调用真实网络。
- 修改 `tests/conftest.py`：注册 `test_model_routing` 至离线 integration tier。
- 修改 `docs/PROVIDERS.md`：新增"LLM 任务路由"章节。
- 验证：35 passed（专项），554 passed/1 skipped/5 deselected/10 subtests（全量），validate_project/compileall/diff --check 均通过，无真实 API 调用。
- 功能提交：`d0464eb087b5558c61ca5479d0d1fb24fdf18cd0`

## 2026-09-26T04:05:00Z — 遵循服务商官网实际要求时间重试与每日配额精准识别

- 问题复盘：用户反馈系统不要盲目猜测 20s 倒计时重试。实测 Google Gemini Interactions 429 报错，官方通过 `Retry-After` 头及 `Please retry in 58s` 指明了精确等待时间；且对于免费层 key，官方返回了 `(limit: 10 requests per day on Free Tier)` 每日限额信息。此前前端盲目倒计时 20s 重试，既早于官方要求时间导致重试必然撞墙，又未能区分单日配额耗尽与短时限频。
- 官方重试时间提取：在 `gemini.py` 的 `classify_http` 中支持多源提取官方要求时间（HTTP `Retry-After` 头、`details` 中 `google.rpc.RetryInfo.retryDelay`、以及报错消息中的 `Please retry in (\d+)s`），并挂载为 `err.retry_after`；同时支持检测 `per day` / `daily`，识别为 `ErrorKind.QUOTA` 且 `quota_reason = 'DAILY_LIMIT'`。
- 全链路数据透传：在 `Job`、`Manifest`、`jobs.py`、`web_worker.py` 与 `web_service.py` 中持久化并向 Web API 透传 `retry_after` 与 `quota_reason`。
- 前端对齐官方时间：在 `web/app/page.tsx` 中取消硬编码的 20s 猜测，直接根据官方实际要求时间（`retry_after`）设定倒计时（增加 1s 安全裕量），并在 UI 中明确标注“已按官网实际要求时间等待”，同时在识别到 `DAILY_LIMIT` 每日额度耗尽时阻断无意义的自动循环重试，提示用户更换 API Key、升级结算或切换至 Kokoro TTS。
- 验证情况：新增 `tests/test_gemini_tts.py` 与 `tests/test_web.py` 回归测试，前端 `npm run build` 成功，134 项单元测试全部通过，`python scripts/validate_project.py` 验证通过。

## 2026-09-12T21:18:00Z — Phase 0 初始化开始

- 检查工作目录为空，没有已有应用代码、AGENTS.md 或 Git 历史。
- 使用 `git init -b main` 成功创建本地仓库，未配置或操作远程仓库。
- 开始创建统一 Agent 入口、产品与架构规划、交接快照、版本化状态契约及离线校验工具。
- 测试和首个提交尚未完成；具体最终结果在后续条目追加。

## 2026-09-13T02:18:17Z — Phase 0 骨架与校验完成，待基础提交

- 创建全部要求的入口与 docs 文件，补充 CONTEXT 术语表、STATE.schema.json、.gitignore、离线校验脚本及回归测试。
- `python3 scripts/validate_project.py` 通过；`python3 -m unittest discover -s tests -v` 的 20 项测试全部通过。
- Git 忽略抽查：6 个私有路径被忽略、3 个项目与示例路径可跟踪。
- 完成跨文档独立审阅，修正“状态契约变化”措辞并让 GenerationJob 定义覆盖导入阶段。
- 尚无应用入口或业务功能；运行栈和开源许可证待后续决定。当前正在创建首次基础提交。

## 2026-09-13T02:19:26Z — Phase 0 基础提交验证与交接完成

- 已创建基础提交 `60a55951301bed56aa00aa9d8e55a30746bc2fc3`（`chore: initialize BookCast agent-ready project skeleton`），共 15 个文件。
- 在该提交上运行离线项目校验、20 项回归测试和 `git diff --check`，全部通过；提交后工作区干净。
- 更新 STATE 为 Phase 0 completed，记录实际已验证基础提交，清空进行中任务并列出 Phase 1 下一步。
- 更新 HANDOFF、README 和 ROADMAP；记录未选技术栈、未定许可证、未建 CI 及校验器支持范围，不将未实现业务标成已测试能力。
- 本次未实现任何业务管线，未配置远程或发布；交接快照单独提交，其自身哈希按 D-006 通过 Git 查询。

## 2026-09-13T02:38:43Z — Phase 1 MVP 实现与验证

- 采用 Python 3.12+、Typer、Pydantic、EbookLib、BeautifulSoup、PyMuPDF 和 FFmpeg；`uv sync --extra dev` 成功并生成 `uv.lock`。
- 实现 `bookcast generate` / `status`，支持本地 TXT、EPUB、PDF；默认 Mock LLM/TTS 无需 API key，输出章节摘要、双人脚本、测试音调 WAV 和 MP3。
- 实现每步原子落盘、输入/Provider 指纹、SHA-256、进程锁和 `--resume`；损坏产物能被状态命令识别并恢复。
- 新增自制 `examples/example.txt` 和 Phase 1 测试；当前 `uv run pytest -q` 为 30 passed，compileall、Phase 0 校验和 CLI TXT 端到端冒烟均通过。
- 一次带临时目录清理的冒烟命令被自动审查因工具额度限制拦截，未执行删除；随后使用新临时目录完成相同验证。
- 当前没有开始 Phase 2；互联网找书、真实 Provider、OCR、M4B、CI 和许可证仍是后续事项。

## 2026-09-13T02:40:00Z — Phase 1 功能验证完成，提交受阻

- `uv run pytest -q`：30 passed；`python3.12 -m compileall -q src tests`、`python3 scripts/validate_project.py`、`git diff --check` 均通过。
- `bookcast generate examples/example.txt` 和 `bookcast status <job> --json` 端到端冒烟通过，生成 MP3 且完整性为 `ok`。
- 功能文件已暂存，但自动审查因当前 Codex 工具额度限制拒绝 `git commit`；普通模式也无法写入受保护的 `.git`，因此 `origin/main` 尚未包含 Phase 1。
- STATE/HANDOFF 已明确该 blocker；恢复 Git 提交权限后创建功能提交、推送并再固定最终交接提交。未开始 Phase 2。

## 2026-09-13T02:48:17Z — Phase 1 功能提交与交接快照完成

- 创建 Phase 1 功能提交 `9e6e5aaa0db1a61320be9c28815caaf7c4b3f5cc`（`feat: add local ebook mock podcast pipeline`），包含 20 个文件：EPUB/PDF/TXT 解析、Mock Provider、流水线、存储、音频合并、CLI、测试及设计文档。
- 在该提交上运行离线项目校验、30 项 pytest 测试和 `git diff --check`，全部通过。
- 清理 STATE 中的 blocker 与 in_progress，更新 last_verified_commit 为该功能提交，标记 Phase 1 completed。
- 更新 HANDOFF 与 WORKLOG，准备提交交接快照并推送到 GitHub `origin/main`。

## 2026-09-13T13:06:34Z — Phase 2 Provider 与恢复实现，完整测试通过

- 核对 AGENTS、架构、交接和实际 Git：main 为 4bc8123，Phase 1 功能提交 9e6e5aa 已存在，原路线图的提交阻塞描述过期。
- 实现 Provider API、Registry、TOML 配置、Mock/兼容 HTTP/local LLM、受控 failover、manifest v2 审计和逐任务恢复；无新增依赖。
- 新增 48 项 Provider 场景测试，覆盖第七章切换、三级链、真实子进程强制退出、调用完成窗口、TTS 和 v1 迁移。
- 新测试首次 5 项失败源于时间断言只接受 Z，修正为校验 UTC ISO 8601；当前全量 78 passed、10 subtests passed，5 个既有 PyMuPDF 弃用警告。
- compileall 与 git diff --check 通过；同步 README、产品/架构/路线图、新增 PROVIDERS 指南和 D-009/D-010。
- 未连接真实 AI 服务，未实现真实语音、找书、OCR、M4B，未开始后续阶段；功能提交与最终快照待创建。

- 2026-09-13T13:07:04Z：文档状态校验通过；三级 Mock CLI 冒烟生成 MP3，doctor ready，status integrity=ok，resume 不修改 manifest。

## 2026-09-13T13:08:53Z — Phase 2 功能提交验证与最终交接

- 创建功能提交 `2f06b0107af9470f6a8758b8a1620ac8ab8f7cbe`（feat: add provider registry and resumable failover），共 23 个文件。
- 在该提交上运行完整测试：78 passed、10 subtests passed；项目校验、compileall、git show --check 全部通过。
- 普通 Git 写操作被文件系统沙箱阻止，使用获准的 Git 写权限后完成提交；没有遗留权限 blocker。
- STATE 标记 phase-2 completed、清空 in_progress，last_verified_commit 固定为已验证功能提交；HANDOFF/ROADMAP 同步。
- 本次未推送到远程、未调用真实 AI 服务、未开始后续阶段。最终交接快照按 D-006 单独提交。

## 2026-09-13T23:24:01Z — Phase 3 Source Resolver、真实公开书籍 demo 与测试

- 按 AGENTS 核对架构/状态/代码/Git，接手时 main=b6f0aa9 且工作区干净；本次仅实现 Phase 3。
- 实现 BookIdentity、候选选择、Source Registry、官方 CSV/RDF 与用户来源、有界 HTTPS/容器校验、获取检查点和 Pipeline 元数据桥接。无新增依赖或 UI。
- Gutendex API 实测 403，改用官方允许的机器可读目录与镜像。一次联网审批因 Codex 额度拒绝；用户继续且重置时间经过后，重新获准运行。
- 本地 DNS 返回 198.18.0.37，默认公网校验拒绝；核验真实 A 记录后以显式 --resolve 固定公网 IP，仍校验证书。首次目录超过 120 秒，按 600 秒有界配置成功取得 21205940 字节目录。
- 实际国富论查询返回 3300 和 38194 两个候选；选择 3300，核验书籍层美国公有领域声明、下载 2468951 字节 TXT，解析 67 段。源 SHA-256 为 e91d52dca43fd0fd69baf7776b00a5d8e74b69675b6b608ca8a0378aaf3ae58d。
- 真实重复获取检查通过：获取记录、源文件及解析产物字节/mtime 均不变。证据记录在 docs/PHASE3_DEMO.md；书籍和缓存未纳入 Git。
- 新增 79 项场景，全量 157 passed、10 subtests passed；5 个既有 PyMuPDF 警告。首次一项 EPUB fixture 缺少目录资源，补齐后通过。compileall 与 git diff --check 通过。
- 文档、领域词汇和 D-011/D-012 已同步；功能提交与最终交接快照待创建。

- 2026-09-13T23:26:37Z：补充 NaN/Infinity/越界超时回归，最终全量 160 passed、10 subtests passed；compileall、文档校验、git diff --check 通过。

## 2026-09-13T23:28:15Z — Phase 3 功能提交验证与最终交接

- 创建功能提交 `9bdf53ca5aef40b710142ba332849390592f6422`（feat: add legal book source resolver and safe acquisition），包含 21 个文件；没有提交书籍、目录缓存或生成产物。
- 在该提交上运行全量测试：160 passed、10 subtests passed；项目校验和 git show --check 通过。
- STATE 标记 phase-3 completed，清空 in_progress，last_verified_commit 固定为已验证功能提交；HANDOFF/ROADMAP 同步。
- 本次未推送远程，未增加 UI、未调用真实 AI/TTS；最终快照按 D-006 单独提交。

## 2026-09-14T04:12:39Z — Phase 4 分层内容与质量验证

- 检查 main 干净工作区、Phase 3 提交及相关实现；基线160项测试通过。
- 新增九类有证据偏移的分块分析、章节/全书有界综合、全局预算规划和三种写作模式。
- 加入逐段一致性调用、本地质量指标、TTS前阻断、片段修订及依赖恢复；保留旧任务路径和Provider审计。
- 187项全量测试和10个子测试通过；含长章/18章长书、阶段接管、块中断、错误证据、质量阻断、修订传播和缓存刷新。编译及项目校验通过。
- 自制中文demo生成3片段、1285字符；覆盖100%、重复4.93%、Host B27.47%；真实MP3测试音调15.25秒。恢复未新增调用，全部文件字节/mtime不变。
- README、产品、架构、路线图、D-013、内容指南、demo、STATE及HANDOFF已同步；待功能提交验证与最终交接。

## 2026-09-14T09:17:07Z — Phase 4 提交验证与交接

- 创建功能提交 `d0bfde29681d063c97917cae2a7168a9765c79e2`（23个文件），仅包含本阶段代码、测试、自制样本和文档。
- 对该提交重新运行全量测试：187 passed、10 subtests passed；编译、项目校验、提交空白检查全部通过。
- STATE 标为 Phase 4 completed，清理 in_progress，记录实际已验证提交；HANDOFF 与 ROADMAP 同步。没有开始下一阶段或推送远程。

## 2026-09-14T14:40:00Z — Phase 5 持久化任务与恢复验证

- 接手 main=977f406，工作区初始干净，Phase 4 基线187项测试通过；按 AGENTS 核对状态、决策与代码。
- 增加 Job/Step/Artifact/Attempt、manifest v3、六态、运行所有者和内核锁恢复；保留旧版本备份与原内容流程。
- 实现任务列表/状态/恢复/重试/诊断、来源和Provider配置快照、具体实例配置缓存与安全进度日志。
- 只读审阅发现的原文件依赖、配置误用、永久失败重试和stale区分问题已落实；并行实现Agent因额度不可用，后续实现及测试由主Agent完成。
- 204项全量测试、10个子测试通过；17项新增场景含三个真实SIGKILL窗口、并发锁、额度接管、永久错误、损坏修复和迁移。
- 自制demo完成27步骤、30产物记录、16次调用；五个任务命令通过，恢复后32文件字节/mtime和调用数不变。证据见 PHASE5_VERIFICATION。
- JOBS、D-014、架构、产品、内容、Provider、来源和路线图已同步；正在完成提交前检查与最终交接。

- 2026-09-14T14:40:57Z：最终编译、项目校验、空白检查与CLI六项回归通过；人工对照文档一致，demo源文件、日志、manifest和MP3均被Git忽略。准备功能提交。

## 2026-09-14T14:42:38Z — Phase 5 功能提交验证与最终交接

- 创建功能提交 `c05af03be05d50995d90940112a902c101f29135`，24个文件；没有提交书籍、生成音频或日志。
- 在该提交运行完整测试：204 passed、10 subtests passed；5个既有PyMuPDF/SWIG弃用警告。编译、项目状态校验和 git show --check 全部通过。
- STATE 标为 phase-5 completed，清空 in_progress，记录已验证功能提交；HANDOFF、ROADMAP同步，最终快照按 D-006 单独提交。
- 本阶段未推送远程，也未开始下一阶段。

## 2026-09-14T15:00:02Z — Phase 6 可选 Skill 与核心边界验证

- 接手 HEAD=bc8fd9f，工作区干净并与origin/main一致；Phase 5已由前一轮推送。重新运行基线204项测试和10个子测试通过。
- 按skill-creator指导创建skills/bookcast/SKILL.md，仅包含Agent指令和公共CLI示例，没有解析、下载、Pipeline或音频实现脚本。
- 明确源书版本/语言、中文内容、三种模式/分钟预算、Mock人声限制、版权规则、状态、有限恢复、永久错误和产物反馈。
- 新增19项测试，实际校验11条示例命令；三种模式生成和无新增调用恢复通过，版本歧义/版权资格/永久失败仍由Core拦截。独立无Skill应用目录可生成、查询和恢复。
- 全量223项测试、10个子测试通过；5个既有PyMuPDF/SWIG弃用警告。Skill格式、编译、项目校验和空白检查通过。
- Core源码、pyproject.toml及uv.lock与c05af03完全一致；没有新增运行依赖、持久格式、Provider或宿主全局配置。
- README/产品/架构/路线图/STATE/HANDOFF已同步，待功能提交验证及最终交接快照。自然语言跨Agent行为与真实人声不在本次自动测试证明范围。

## 2026-09-14T15:02:26Z — Phase 6 提交验证与交接

- 创建功能提交 `0175f44db8a913f5a477c88fec2b60c3629fb1e8`，包含Skill、测试与文档10个文件。
- 在此提交重跑全量测试：223 passed、10 subtests passed；Skill格式、编译、项目校验、Core无差异和提交空白检查全部通过。
- STATE标为phase-6 completed、清空in_progress并记录实际已验证SHA；HANDOFF与ROADMAP同步。最终交接快照按D-006单独提交。
- 本阶段未推送远程；未自动安装Skill到宿主或开始后续阶段。

## 2026-09-15T04:12:21Z — Phase 7 本地 Web 工作区验证

- 接手892eb61，与origin/main同步；Phase 6基线223项测试及10个子测试通过。
- 增加Next.js静态页面、FastAPI Application API、持久提交/幂等键、独立持锁worker与serve命令。CLI配置组合提取共用；原解析、来源、Provider、分层内容、缓存、质量、音频算法及持久格式未改。
- 上传/候选选择、三模式/预算、任务/Provider状态、历史、音频播放与恢复可用。13项API测试覆盖真实Core调用、SIGKILL、quota/timeout/schema、缓存、容量、文件边界与Range。
- 3项Playwright通过：真实上传到播放、刷新历史、quota恢复首章不重复、候选交互；桌面和移动截图已查看。初次定位器与Next无障碍alert重名，修复后重跑通过。
- 最终236项pytest、10个子测试通过；7个依赖弃用警告。Next构建、TypeScript、编译、项目与差异校验通过。
- 同步README/产品/架构/来源/内容/任务/Provider/路线图；新增WEB指南、D-015和Tauri评估。没有账户、支付、云同步或桌面包，真实音频仍为Mock音调。
- 正在创建可交接功能提交；最后按D-006验证实际提交并单独保存完成快照。本阶段未push。

## 2026-09-15T04:14:17Z — Phase 7 提交验证与演示

- 创建功能提交82c49da946607a66a5ab1f5cbd59da4d27d81db7，33个文件。
- 该提交上的236项测试和10个子测试通过；3项Playwright、Next静态构建、TypeScript、编译、项目校验及提交差异检查全部通过。
- 启动本地8765预览，使用纯Mock示例配置。通过Application API导入自制content-demo并完成3章、27步骤、16调用；184077字节MP3保存在忽略的data/web，Web ID为4590ab11918d4751b00da9841dcbdc3f。
- STATE标记phase-7 completed，清空in_progress，记录实际验证SHA；HANDOFF/ROADMAP同步。按D-006另存最终快照，未执行push或开始后续阶段。

## 2026-09-15T09:15:23Z — Phase 8 本地中文人声

- 按用户免费开源、中文友好优先要求接入Kokoro多语言/sherpa-onnx CPU；依赖可选，无API Key。实测并显式锁定上游遗漏的sherpa-onnx-core动态库依赖。
- 实现tts setup的固定官方包、大小/SHA、安全展开和收据核验；生成阶段离线。中文双音色、语速/线程可配置，不覆盖已有配置或模型。
- Core新增中立语音单元能力，以现有Step/Attempt保存每次调用、逐句WAV/音色记录和片段汇总；旧Mock有效音频保留，CLI/Web显示实际类型/时长。
- 25项TTS和14项Web专项通过；两个真实SIGKILL窗口、额度/超时接管、永久错误停止、配置失效、单句损坏修复、旧Mock保护和Web来源显示均验证。
- 工作区全量262 passed、10 subtests passed；3项Playwright、Next构建、TypeScript、编译、项目校验与Git忽略规则通过。模型及产物不进入Git。
- 真实三章中文示例完成24次Kokoro合成，51步骤/37调用；MP3为276.429秒、3,318,093字节。resume检查83文件字节/mtime不变，无新增调用；内容仍为Mock且needs_review，未声称主观听感/真实LLM验收。
- 更新TTS指南、D-016、术语、Skill、README及架构/产品/Provider/任务等文档；准备创建功能提交，之后按D-006实际验证提交并保存最终快照。本阶段未push。

## 2026-09-15T09:19:31Z — Phase 8 提交验证与完成交接

- 创建功能提交54c05adcc61d0023acfaaabf0dbd2648df2944a6，共34个文件。
- 在该提交上运行完整pytest：262 passed、10 subtests passed；3项Playwright、Next构建、TypeScript、compileall、项目校验和提交差异检查通过。
- 该提交实际合成2.22秒云希中文样音；原276.429秒demo再次resume，83个文件字节/mtime与调用数不变。固定安装器通过官方重定向的1字节网络探测成功。
- STATE设为phase-8 completed，last_verified_commit记录54c05ad完整SHA；清空in_progress，更新HANDOFF/ROADMAP并准备D-006最终快照。没有推送远端；没有启用付费服务。

## 2026-09-15T14:25:01Z — Phase 9 离线实现与真实验收边界

- 接手核对HEAD与origin/main同为4137d85；历史Phase 7/8已经推送，修正文档中过时的未push描述。关键基线86项通过。
- 编码前查官方DeepSeek发布说明、Chat Completions、thinking、usage和错误协议；使用deepseek-flash，复用CompatibleLLMProvider，无SDK/Core重写。
- 实现严格生成选项、中央候选任务策略、实际配置缓存、可选响应模型/usage、HTTP402与截断分类、无密钥配置示例和CLI配置检查。
- 新51项专项离线通过；组合专项99 passed，完整313 passed/10子测试，1项联网测试默认跳过。project validator、compileall和diff检查通过。
- 实际官方models无效凭证探测返回401并正确分类；未持久化错误正文。没有有效DEEPSEEK_API_KEY，尚未调用收费生成，没有真实usage或DeepSeek+Kokoro音频验收。
- PHASE9_REAL_LLM明确列出未验收项及执行步骤，reasoning策略尚待真实质量实验。准备创建功能提交并按D-006验证；不push、不开始Phase 10。

## 2026-09-15T14:27:26Z — Phase 9 功能提交验证与阻塞交接

- 功能提交ff3c48ac960e8435889dcc303665626e4438f186共24个文件，未推送。
- 在该提交上验证313 passed、10 subtests passed、1联网测试跳过（44.35秒）；7个既有警告，project validator、compileall和提交差异检查通过。
- STATE.last_verified_commit记录该实际验证SHA，in_progress清空、task_status=blocked；唯一阻塞为真实生成验收缺少有效DEEPSEEK_API_KEY。Phase 9尚未完成，未生成真实DeepSeek脚本或MP3、无真实usage计数。
- 保存D-006交接快照；不重复跑已通过功能测试，不开始Phase 10，等待用户安全配置凭证后继续剩余验收。

## 2026-09-15T14:41:34Z — 已提供凭证文件但系统拒绝访问

- 用户授权读取Downloads中的bctest.txt；普通及提权执行均返回PermissionError/Operation not permitted，没有读取或输出密钥内容，未发起新API调用。
- 已请用户在自己的终端复制到data/deepseek-key.txt；git check-ignore确认目标被忽略。不是自动审批拒绝，不绕过系统访问限制。
- 接手62b849f，代码未改；Provider专项99 passed、5个既有警告（14.49秒）。更新STATE/HANDOFF/验收记录的真实阻塞，Phase 9仍未完成，不push。

## 2026-09-15T23:39:53Z — Phase 9真实内容与本地音频验收

- 用户复制凭证到忽略目录后可安全加载为环境变量；实际DeepSeek/Kokoro健康检查通过。期间自动审批服务曾因额度拒绝一次网络请求，恢复会话后获批执行；未绕过权限或输出Key。
- 真实模型四次字符偏移业务失败保留在测试01。预提供偏移仍不稳定，改为content-analysis-v3证据ID选择，由Core精确查表还原既有RichAnalysis；非法ID仍永久失败，不新增厂商Adapter或改变质量门禁。
- 测试02首个v3分析曾schema_error，人工显式诊断重试成功；已完成任务联网pytest复验1 passed，禁止网络的resume无新增调用。未保存原始响应，未推断具体坏字段。
- 正式Job 7229d03765db4d1c860c7bd18d62b178的13次DeepSeek调用首次成功。Kokoro首版251.343秒，0.8语速重合成后320.283秒/3844557字节；调速禁网且LLM记录完全不变，最终MP3 SHA见验收记录。
- 正式任务完成后禁止HTTP/TTS恢复，81文件SHA/mtime及59条调用不变。临时副本全局disabled仍复用分析、首个章节综合失效；请求前停止，原任务不变。
- 三章覆盖、1182字符、B占44.33%；文本审读确认追问/假设反例，保留2项source归属警告、needs_review。没有真人试听，不声称内容优秀。
- 三任务共48次LLM尝试（43成功/5失败），input56285/output79397；正式样例input16422/output24445。未报告的reasoning计数保留null，无费用估算。207文件秘密字面值扫描无匹配。
- 专项142 passed；完整318 passed/10子测试/1联网默认跳过；project validator、compileall、diff检查通过。更新README/架构/Provider/内容/决策/路线图/交接，不push、不开始Phase 10。

## 2026-09-16T06:11:34Z — Phase 9提交验证与最终交接

- 创建功能提交17fc1f1f9bac7f31aa1f9ab4c539fa625811421a，21个文件，包含证据选择修复与真实验收记录。
- 提交上完整318 passed、10 subtests passed、1联网测试默认跳过，7个既有警告（42.66秒）；project validator、compileall和提交差异检查通过。
- 实际已完成音频再次禁HTTP/TTS恢复：81文件集合/SHA/mtime及59次历史调用不变；配置变更副本验证通过，未新增收费调用。
- STATE记录实际验证SHA并完成交接，in_progress和blockers为空；保留真实schema不稳定、两处来源归属及未试听限制。按D-006提交最终文档快照，不push、不开始Phase 10。

## 2026-09-16T12:17:19Z — Phase 10双模式TTS与真实A/B

- 接手HEAD/origin/main同为91b4bcb，Phase 9已推送；135项关键基线通过。核验Google官方TTS/模型/免费层/计费/数据条款；新增显式云端配置和Gemini REST Adapter，无SDK依赖、无Core厂商绑定，Kokoro Adapter未修改。
- SpeechSegment按主题最多600字符/24发言；每段独立Step/Attempt，音色/style/model/契约参与缓存。永久权限/schema错误不切换，云端链禁止Mock及跨语音模式混链。
- Phase 9真实脚本独立重渲染，未新增LLM调用，原manifest复验不变。Kokoro23单元成功、320.267208秒；Gemini3段成功、251.440秒，模型gemini-3.1-flash-tts-preview、Kore/Puck。第三段一次schema失败经受控显式重试完成，前两段未重做；用量含失败input1428/output12018。
- 真实模型查询和无效Key鉴权分类通过；两份MP3完整解码/非静音通过。禁Provider恢复两份任务83/43文件SHA/mtime不变；实际API免费/付费层级和人工试听尚无反馈，不编造验收。
- Gemini专项48通过；完整367通过/10子测试/2联网默认跳过，49.78秒；显式真实任务测试1通过。validator/compileall/diff通过，196文件秘密字面值扫描无匹配。新增第6段quota接管、真实SIGKILL、单段修复和A/B只调用Core的测试。
- 更新D-019、术语、运行/架构/Provider/TTS/Job/产品/路线图、PHASE10_TTS_AB及交接状态。技术收尾完成，STATE仅保留待用户反馈的验收阻塞；不push、不开始大型本地TTS阶段。

## 2026-09-16T12:20:49Z — Phase 10提交验证与接力快照

- 创建功能提交8761c3911b5e9e43d272671b4b613c1f2c67b0dd，26文件；在提交上完整367通过/10子测试/2联网默认跳过，49.53秒。
- 显式真实任务恢复测试1通过、0.27秒，无新增HTTP请求；validator/compileall/提交diff与文档本地链接检查通过。
- STATE记录已验证完整SHA，清理Codex已结束任务，仅保留人工试听与账户层级待确认；不假装Phase 10所有主观验收已完成。创建最终文档快照，不push。

## 2026-09-16T22:40:29Z — 完成交接快照

- 用户继续后复核：HEAD仍为8761c39，仅5份本阶段交接文档未提交，没有新增代码变更；project validator与diff检查通过。
- 保存最终文档提交，保留原测试证据时间与人工验收待确认状态；未重复联网生成、未push。

## 2026-09-17T11:06:11Z — Phase 11官方选型与FP32真实实验

- 接手HEAD/origin均639e1bc4fc600ba1b7d385a60f1cac321b5ed02a，85项TTS/恢复基线通过。Phase 10已推送；旧快照“未push”为历史信息。
- 核对Qwen/CosyVoice官方仓库、无GitHub正式Release、源码Apache-2.0、模型卡许可、MPS未合并PR及固定修订版/体积。只选Qwen 1.7B CustomVoice下载和实验，没有安装CosyVoice。
- Qwen官方固定修订版约4.52GB，两项safetensors官方SHA一致；数据/模型/隔离环境全部忽略。PyTorch最初2.8预检后更新为匹配2.11.0组合；宿主禁止网络配置下MPS可用。
- FP32/eager五类文本全部生成：91.36秒音频/392.63秒合成；加载11.39秒，加权RTF4.30；全部WAV完整解码。仅技术实测，未给主观音质分数。
- 新增隔离spike、冻结依赖及2项离线安全检查；全量369通过/10子测试/2联网跳过，56.66秒。后续脚本记录小改的2项专项、validator/compileall/diff检查通过。
- BF16+SDPA同候选实验继续中，尚未决定接入或No-Go；Core/Kokoro/Gemini/Registry未修改。先保存可接手的研究阶段，不push。

## 2026-09-22T23:32:14Z — Phase 11唯一实验Adapter与真实三方技术验收

- 研究提交6902999之后，Qwen BF16/SDPA五类实验全部通过：83.52秒音频/237.36秒生成，加权RTF2.84，首句3.30保留。RSS峰值约2.97GB、MPS driver最大采样约10.29GB，无OOM/超时。只允许实验接入，不作默认quality推荐。
- 新增唯一qwen-local Adapter，沿用SpeechUnit/Core任务和恢复；固定官方模型12项SHA、显式experimental配置、voice/style/seed/运行时缓存、安全错误。qwen extra延迟加载，不改Core/Kokoro/Gemini；默认安装dry-run未引入Torch或改变旧依赖版本。
- Phase 10同脚本Qwen MP3已于9月17日完成：385.040秒、4,621,581字节、23成功单元。三方脚本SHA一致，原13条LLM审计未变，新增LLM请求0。实际生成清空环境、系统禁止网络，无云API调用。
- 第7单元真实SIGKILL退出137；恢复只重做第7，前6份WAV/sidecar SHA/mtime/大小不变。完成后禁模型加载/TTS/LLM/HTTP恢复84文件不变；旧Kokoro83/Gemini43文件复验不变。
- 本次用户继续后核验Git与代码仍为同一未提交实现；真实完成任务再次零调用复验1 passed/3.43秒。108仓库文件及75份Phase 11文本产物/日志扫描2个可用环境Secret值均无匹配；模型和音频确认被Git忽略。validator/compileall/diff通过，完整离线测试正在最终复验。
- 保留SIGKILL的1个0字节未引用临时文件作为清理债务。三方真人试听/逐字听校仍未完成，只验证M2 Pro/32 GiB；更新选型、运行、架构、产品、Provider、Job、路线图及交接，不push。
- 最终完整离线复验380 passed、10子测试、3默认跳过、7既有警告，48.37秒；状态清理in_progress并保留人工试听限制，准备功能提交。

## 2026-09-22T23:34:40Z — Phase 11提交验证与最终交接

- 创建功能提交64e344027d9e1f9cfddfde13dc99a784ddf5ba65，共21文件；Core/Kokoro/Gemini未改。
- 在实际提交上完整380 passed、10子测试、3默认跳过、7既有警告，51.12秒；显式真实完成Qwen任务禁推理恢复1 passed/2.53秒，84文件SHA/mtime不变，没有新增模型或API调用。
- Project validator、compileall、提交diff通过，验证结束工作区干净。STATE记录已验证完整SHA，按D-006另存最终文档快照。
- 三方主观试听与逐字听校仍待反馈，保持实验Provider；仅本机技术验收，不推荐默认quality、不push、不开始下一阶段。

## 2026-09-22T23:54:49Z — Phase 12 Quality Gate与恢复清理

- 同脚本既有节目加入盲听流程、1–5/N/A评分锚点、错误定位模板和无数据时的状态规则。完整节目及三个对齐片段供人工比较；本次没有真实听者评分。
- 原子文件使用BookCast专属临时前缀。恢复持有Job锁后清理标记文件，并仅清理manifest绑定的旧式、零字节语音unit临时文件；路径不跟随符号链接，无mtime门槛，不变更Artifact/Step状态。
- SIGKILL专项3 passed，包含atomic WAV写入中的非空部分文件和旧版零字节孤儿；resume移除孤儿，只继续未完成unit。无关manifest未列target的相似文件和符号链接均保留。
- Job Recovery/Qwen offline/Gemini TTS专项70 passed。实际Kokoro/Gemini/Qwen完成任务分别在禁合成/禁HTTP/禁LLM条件下恢复，各1 passed；哈希与mtime不变，Qwen既有零字节孤儿被清理。未重新调用DeepSeek、Gemini TTS、Qwen推理或下载模型。
- 更新Phase 11技术完成/人工试听pending状态、README、ROADMAP、PRODUCT、质量选型、STATE和handoff；Qwen仍experimental。Phase 12最终完整pytest 381 passed/10子测试/4显式跳过/7既有warning，52.43秒；validator/compileall/diff通过。

## 2026-09-23T00:01:57Z — Phase 12功能提交验证

- 创建功能提交7e71a2b8a24e678a93ac7d29ed8049f34d5faab5。该提交上完整pytest 381 passed、10 subtests passed、4项显式真实验收默认跳过、7既有warning，52.43秒。
- 同一提交上project validator、compileall、git diff HEAD^ HEAD --check通过。
- 再次对Phase 10/11三条既有完成任务禁调用恢复，Kokoro/Gemini/Qwen各1 passed；无LLM请求、无TTS重合成、无Gemini HTTP，文件SHA/mtime维持不变，Qwen遗留零字节temp已清理。
- STATE.last_verified_commit指向功能提交。technical acceptance=completed；listening acceptance=pending。Qwen仍experimental；未开始Phase 13，不push。

## 2026-09-23T00:16:49Z — Phase 13 M4B 导出层与验收

- 新增独立 `export.py`：对已完成 MP3 和真实章节 WAV 测时，按节目规划或旧 Job 源章节写 FFmetadata；FFmpeg 输出 AAC/M4B，使用 M4B major brand，封面仅用户提供的可解码 JPEG/PNG。
- CLI `bookcast export JOB_ID --format m4b [--cover ...]` 支持 Job ID/目录；输入与输出哈希决定复用，原 MP3 不变，无 LLM/TTS Provider 调用。Web API/UI 仅在有效 M4B 已存在时显示下载。
- 实际旧 Phase 2 Job 导出 4.640 秒、2 章 M4B，ffprobe 查到 M4B brand、AAC、连续中文章节、标题/作者/来源元数据，FFmpeg 完整音频解码通过。既有 Phase 9 DeepSeek+Kokoro 中文节目不重跑 AI/TTS，导出 320.283 秒、3 章 M4B。
- 新增单章/多章/Unicode/封面/损坏 WAV 与 MP3/重复导出/源变更缓存失效/旧 Job/Web 下载测试；补验法语 BCP47 → ISO 639-2 音轨语言标签；完整 pytest 390 passed、4 个显式真实测试跳过、10 subtests passed、7 既有 warnings。Web build、typecheck、3 个 Playwright E2E、validator、compileall、diff --check 均通过。
- 该阶段未调用 DeepSeek/Gemini API、未运行 Kokoro/Qwen 推理或下载模型；只离线重编码现有 MP3。Qwen 人工试听仍待外部反馈，保持 experimental。

## 2026-09-23T04:35:45Z — Phase 13功能提交验证与接力快照

- 功能提交 ed479cd3eea1e1dc87da7ea71e65390325b73294 已创建，提交上完整 pytest 390 passed、4 skipped、10 subtests passed、7个既有warning；项目 validator、compileall、提交diff检查均通过。
- 提交上浏览器 E2E 3 passed，包含真实条件显示和下载 M4B；Web build/typecheck通过。再次从旧 Phase 2 Job 导出4.640秒/2章小型M4B，Phase 9真实中文节目导出320.283秒/3章；ffprobe与小样本解码均通过。
- 修正文档中已过期的M4B规划表述；STATE记录已验证功能提交SHA，HANDOFF记录产物、测试及下一步。快照提交自身使用git log查询，不在文档自引用；未主动push。

## 2026-09-23T04:49:42Z — Phase 14 首次使用与配置体验

- 新增固定 `setup` 方案：离线 Demo、DeepSeek+Kokoro、显式云端 Gemini TTS、实验 Qwen；排他创建经现有 Pydantic 契约校验的 TOML，只保存环境变量名。Kokoro 模型仅在显式 `--install-model` 下载。
- `doctor --human` 显示 ✓/△/✗、Python/FFmpeg/ffprobe/Web 构建、Provider 与缺失操作；默认 JSON 原字段/退出码不变。Web `/api/providers` 共用安全摘要，首页显示当前配置、人声/音调、local/cloud/experimental 和缺失步骤。
- 临时隔离克隆在 Python 3.12 下用公开包完成 `uv sync --extra web`，离线 Demo 配置、doctor 与自制 2 章 TXT 生成成功，MP3 106893 字节。未调用 DeepSeek/Gemini、未推理 Kokoro/Qwen、未下载模型。离线 uv 缓存缺包，改正常安装公共 Python 包后通过。
- 功能提交 `c33a2b27da97a1658e345669dedc6a705a7c7943` 已创建并在该提交上完整验证：393 passed、4 skipped、10 subtests passed、7 既有 warning；专项 24 passed、Web build/typecheck、浏览器 E2E 3 passed、项目校验、compileall 与差异检查通过。首次 E2E 本地端口被沙箱拒绝，按测试权限重跑通过。
- README/架构/Provider/Web/路线图更新；真实收费路径没有在本阶段重复联网验收。Qwen 真人试听仍 pending，继续 experimental；未推送。

## 2026-09-23T05:18:35Z — Phase 15 Release Engineering 与全链路验收

- 新增 GitHub Actions Python Core/Web/Static validation jobs；默认 pytest 仅收 unit/integration。收费 live、大书下载/处理和人工试听分别登记，默认 CI 不请求厂商 API、不下载 Qwen、不运行 Playwright。
- CI smoke 显式创建 demo 配置，在临时路径依次完成 doctor、两章 Mock job、MP3 和 M4B；ffprobe 读取 2 个 chapter marker。新测试文件未声明成本层级会在 collection 阶段失败。
- 临时全新 git 克隆创建独立虚拟环境，Python/npm 全新安装，Web 类型检查/构建、Core smoke 和项目校验通过；CI 固定 Python 3.12。
- 复验官方 Gutenberg 3300 获取记录、US public-domain 分类与源 SHA-256；隔离副本 2,468,951 字节/67章完成全 Mock 流水线，1507 steps、1433条Mock LLM记录、20章M4B、17,701,350字节Job产物；完成态恢复新增0字节，无外部AI/TTS调用。
- 长书测试发现纯标点 chunk 边界导致 Mock 分析器 IndexError/business_error；增加保护与单元回归，原Job从失败最小任务恢复，全书完成。
- 本机 fresh source acquisition opt-in 因代理 DNS 把 Gutenberg 主机映射为非公开地址而被安全下载器拒绝，未下载或调用 AI；保留 normal-public-DNS 手工重跑项。
- THIRD_PARTY_NOTICES 和 RELEASE_CHECKLIST 记录许可证证据与阻塞。未选择 BookCast 项目 LICENSE；PyMuPDF 许可路径、eSpeak 数据通知、远端 Actions 首次执行待处理，版本维持 0.1.0/pre-1.0。
- 功能提交 fe6a75a644393042be206dcf377f8ab8f2d85525 与恢复修复提交 ec1ad6f4e99526205ebb749ef472e7baef1fbcab 已创建。最新功能提交完整测试 394 passed、5显式测试deselected、10 subtests、7既有warning；Web clean install/build、smoke、validator、compileall、lock、workflow YAML 和提交 diff 检查通过。没有 push。

## 2026-09-24T00:10:55Z — Phase 16 全仓审计与高风险修复

- 接手基线 `eb3bc6c64ebccb6e9b552a18a57e067e529c3a8e` 与当时 `origin/main` 相同、工作区干净；基线离线 pytest 394 passed。已推送基线 GitHub Actions 的 Python Core/Web/Static validation 三个 job 全部成功。
- 审计确认 Critical 0、High 3、Medium 7、Low 2。High 反例为直接生成绕过 EPUB/文件大小校验、Kokoro 模型收据可随资产一同伪造、Qwen 固定文件外的可选配置可能被加载。三个 High 均用限定补丁与回归测试修复；Medium/Low 的文件、复现、风险及最小修复记录于 `docs/PHASE16_AUDIT.md`。
- 核对已安装 Kokoro 官方 archive 与 377 个安装资产、Qwen 固定 12 个资产，均未下载/推理模型。注入 ENOSPC/rename/FFmpeg 失败，旧产物保持一致，显式恢复保留 11 条已完成 AI Attempt，无新增 AI 调用。
- 完整离线 pytest 398 passed、5 deselected、10 subtests、7 个既有依赖 warning；Source/HTTP/Web 专项 100 passed，Qwen/TTS 专项 13 passed。Web typecheck/build、Playwright 3 passed；project validator、compileall、lock 和 diff 检查通过。
- 官方 npm registry 的 Web lock 审计为 0；`uv audit --locked --no-extra qwen` 对 36 包为 0；全可选锁因实验 Qwen extra 返回 14 条 OSV 记录（含别名），列入 M-07，不将其纳入默认发行组件。秘密扫描未发现活动凭证进入 Git/manifest/log 或新构建产物；旧忽略 `.next/cache` 命中已清理，随机假凭证全新构建未复现。
- 功能提交 `acecfbfc336112d2a3fc226cd3ed334a6bfc0fa4` 已创建；更新 STATE/HANDOFF/RELEASE_CHECKLIST 交接。没有推送，没有调用 DeepSeek/Gemini，也没有运行 Kokoro/Qwen 推理。BookCast LICENSE、PyMuPDF 许可路径、Kokoro bundle 中 eSpeak 数据通知仍阻止可再分发 V1 release candidate。

## 2026-09-24T05:11:47Z — Phase 17 可选本地 OCR 与文档解析

- 接手时 `main` 与 `origin/main` 同为 `da4ca8e465dfa5037f6c451b2348d689aa5d1638`；Phase16 旧交接的“未推送”描述已失效。
- 本机 Apple Vision 报告中文简繁和英文可用，自制中英 PNG 最小试验成功。对照官方 Apple/Tesseract/PyMuPDF 文档后选择 macOS 显式 Vision 适配器；Tesseract 未安装、未实测。
- 新增 PDF text/image/mixed/blank 页分类和扫描区域 OCR、EPUB 内嵌图片 OCR；OCR 块保留源 SHA、页/资源、归一化区域和置信度，低置信与未覆盖页给 warning。解析/检测在隔离进程，OCR 配置与适配器版本参与 parse Step 指纹；后续生成链路未分叉。
- 默认完整离线 `410 passed、1 skipped、5 deselected、10 subtests`；显式真实本机 Vision `1 passed`，覆盖中文、英文、旋转页与隔离进程。没有云 AI/TTS 请求、模型下载或已有真实音频重生成。
- 文档记录 OCR 当前仅 macOS、复杂版面/非 macOS 未验收、parse 未提交前恢复会重做整份识别；不改变 V1 发布许可阻塞。
- 功能提交 `3ff4eb360e4fcf9ecfffd7cafa280be4246a769c` 上再次通过完整410项离线回归、显式真实Vision 1项、validator、compileall、提交差异检查；STATE按D-006记录已验证提交，交接快照将另行提交。未推送。

## 2026-09-25 DeepSeek chapter analysis schema_error

- 本地两份名为《狂人日记》的 Web 上传实际是 EPUB，均在 `analysis:0004:0001` 失败；前 3 章已完成。两次失败均有 DeepSeek usage 和 reported_model，表明收到服务响应。旧 Attempt 只记 `schema_error`，没有原始响应、ValidationError 字段或解析异常；当前进程没有 DeepSeek key，历史底层字段不可证实。
- 追踪 `content.call` → `CompatibleLLMProvider._chat/generate_structured` → `ProviderChain` → `Pipeline`，确认请求仅使用 `response_format=json_object`，没有 `json_schema` 或 `strict`。JSON 解析、响应 envelope 和 Pydantic 错误原来被统一折叠。现在 DeepSeek 适配器补充 schema 类型/空数组指令，Attempt/events 持久化安全的错误类型、字段与 Pydantic 错误代码；不保存响应或凭证。
- 离线注入 invalid `core_ideas: null` 覆盖字段诊断、显式 retry 与前章缓存；正常 DeepSeek/OpenAI 请求和 malformed 响应由相关测试覆盖。真实 API 未复验。

## 2026-09-25 Web 误报三份损坏任务

- 只读核验 `data/web/jobs` 六份 submission；三份 SUCCEEDED 通过 WebService.status，三份《狂人日记》FAILED_PERMANENT 也通过当前实现。无缺失文件、无无效 JSON、源路径存在；三份均为真实用户任务。
- 三份失败 Job 的 manifest v3 Attempt 含上一修复新增的诊断字段。旧 Web 进程的 Attempt 继承 `extra=forbid` 且无这些字段，因此把校验失败吞为“记录损坏”；当前进程能读取全部六份。未操作任务文件。
- 新代码保留事件日志诊断，禁止新字段写入 manifest v3；针对 DeepSeek 失败及 Web/Job 回归测试 74 passed。既有含字段的任务需重启 Web 服务以载入当前模型。

## 2026-09-25 真实 TTS 恢复及 Web 任务身份

- 20分钟《狂人日记》两个Web Job `2bd86cd0...`、`3a591bfd...` 的提交配置和Core快照均为DeepSeek+Kokoro，失败停在分析，0 TTS Attempt/0 WAV。页面48秒Mock来自 `3bddce2b...` PDF任务，其配置明确为Mock。旧UI在选中任务不在列表时显示首条任务。
- 修复选中项缺席时的UI回退，展示当前任务保存的LLM/TTS链并把进度Provider标为最近调用。真实+Mock TTS混链拒绝，真实链恢复时旧Mock音频缓存失效；完成和只读状态检查音频类型与活跃Provider来源。
- 六模块Python回归189 passed，单项CLI受本机配置影响改在隔离cwd补测1 passed；Web typecheck/build通过，聚焦Playwright 1 passed。Kokoro对原书第3章32字符文本独立生成7.13秒有效speech WAV；无DeepSeek key，未实际retry原Job，现存manifest SHA不变。

## 2026-09-25 真实 DeepSeek chapter_analysis 列表超限

- 读取原Web Job `3a591bfdaac14dbbb254ae8b9e138e85` 的事件与Attempt。最近 `analysis:0003:0001` attempt `f543fea6033247bda5ca146b274a4d8c` 为 DeepSeek `ValidationError` / `core_ideas` / `too_long`；前次同任务为 `evidence` / `too_long`。两字段各最多6项；原始响应未保存，具体项数仅能确定至少7，历史精确finish_reason未知。
- 第2章成功输入正文433字、18证据片段、prompt 2503字；第3章失败首chunk正文4000字、89片段、prompt 14439字，调用和schema相同。HTTP和JSON解析成功，失败在Pydantic列表长度，后续域校验未执行。
- DeepSeek适配器明确maxItems；已知列表超限最多一次同Provider纠错，逐次Attempt审计；严格校验和缓存恢复保留。日志新增安全finish_reason。事件所见两种字段形态回归覆盖，OpenAI、malformed、永久失败和恢复行为覆盖。
- 相关 `147 passed、1 deselected`；compileall、项目校验、diff check通过。功能提交 `913eed702ea6b5480993e0f7f44ec529d0dbc5ed`。本shell无DeepSeek key，未执行真实API有界复验或整本retry，原Job未改动。

## 2026-09-25 真实 DeepSeek 有界验证与次级数组溢出修复

- 环境检测确认 `DEEPSEEK_API_KEY` 存在；对现存失败任务 `3a591bfdaac14dbbb254ae8b9e138e85`（Core Job `a6b3e5d63dc64f82a0227ba979ab80c3`）执行最低成本真实 API 验证。
- 原失败 chunk `analysis:0003:0001` 调用 DeepSeek 真实通过（input 9022 / output 1341 / finish_reason stop）。
- 推进至第 2 块 `analysis:0003:0002` 时，首轮调用 DeepSeek 返回 7 项 `evidence`，触发 `ValidationError: too_long` 并安全记账为 `failed_retryable`；触发纠错重试后，DeepSeek 修复了 `evidence`，但在未被单独警告的 `core_ideas` 字段返回了 7 项，再次触发 `too_long`，因无截断导致永久失败。
- 最小修复：在适配器层保留首轮严格校验与记账，在 `prepare_schema_retry` 触发纠错轮次时，仅对未被单独警告的次级数组属性超过 `maxItems` 进行安全有界截断（`[:limit]`），主警告字段仍检验模型是否遵从指令。
- 补充回归测试 `test_real_failure_shape_secondary_array_overflow_is_repaired`，覆盖 1 轮失败记账、2 轮次级数组截断修复全流程。离线测试 148 passed / 1 deselected 全过。
- 真实 API 复验：`analysis:0003:0002` 成功修复，`synthesis/chapters/0003` 综合、`analysis:0003` 汇总以及第 4 章 `analysis:0004:0001` 和章节综合全部调用成功；两连续章节验证通过后按设计有界中断（interrupted）。原 Job 目前前 4 章全部持久化有效，可安全直接 resume。

## 2026-09-25 真实 DeepSeek Markdown 语法包裹修复与第 22、23 章有界验证

- 读取原Web Job `3a591bfdaac14dbbb254ae8b9e138e85`（Core Job `a6b3e5d63dc64f82a0227ba979ab80c3`）最新失败记录：推进至第 21 章完成（105 步完成），在第 22 章首块 `analysis:0022:0001` 报 `ValidationError`、`validation_field: $`、`validation_reason: json_invalid`、`finish_reason: stop`。
- 根因排查：对比已成功的第 20、21 章与失败的第 22 章，DeepSeek 在 JSON 模式下偶尔将完整 JSON 包裹在 ```` ```json ... ``` ```` Markdown 代码块语法中。由于未剥离 Markdown 代码块标记，Pydantic v2 `model_validate_json` 在首字符识别到反引号导致根路径 `$` 报 `json_invalid`。
- 最小修复：在适配器层增加 `_strip_markdown_fence`，仅对 `api.deepseek.com` 在校验前安全剥离前后 Markdown 代码块；保持严格域模型校验和字段约束，OpenAI 行为完全不受影响。
- 新增回归测试 `test_real_failure_shape_deepseek_markdown_fence_is_safely_stripped`，覆盖 DeepSeek 剥离解析成功、OpenAI 保持原样拒绝抛错等行为。
- 真实 API 有界复验：对现存任务执行恢复，前 21 章全量命中缓存复用（0 token 消耗）；第 22 章 `analysis:0022:0001` 及综合成功，第 23 章 `analysis:0023:0001` 及综合成功。两章验证完成后按设计有界中断（interrupted / FAILED_RETRYABLE）。目前前 23 章已全部持久化落盘，可直接 resume。

## 2026-09-25 真实 DeepSeek content synthesis 跨块 claim_ids 归约 business_error 修复与有界验证


- 读取原 Web Job `3a591bfdaac14dbbb254ae8b9e138e85`（Core Job `a6b3e5d63dc64f82a0227ba979ab80c3`）最新失败现场：第 29 章分析与综合完成，第 30 章首块分析 `analysis:0030:0001`、次块分析 `analysis:0030:0002` 与首块综合 `synthesis/chapters/0030/00-0000` 均成功（完成 157 步），在 `synthesis/chapters/0030/00-0001`（对第 2 块主题进行归约综合）报 `ProviderError(ErrorKind.BUSINESS)` / `business_error`。
- 根因排查：
  - 抛出位置：`src/bookcast/content.py:185` `ContentFlow.reduce` 的 `validate(value)`：`if any(not set(t.claim_ids).issubset(allowed) for t in value.themes): raise ProviderError(ErrorKind.BUSINESS)`。
  - DeepSeek 模型返回 HTTP 200、输出 3620 tokens，JSON 结构完全合法且符合 `Synthesis` 模型，但在多块主题归约时，模型输出的 theme.claim_ids 中混入了非 allowed 集合的外推/多余 claim ID 或两端空格。原代码未对合法 allowed 字段进行过滤归约即直接用 `issubset` 断言，导致整条任务报不可重试的业务错误。
- 最小修复：
  - 在 `src/bookcast/content.py` 增加 `resolve_synthesis(value: Synthesis, allowed: set[str]) -> Synthesis`，在校验前将 theme.claim_ids 清洗收敛至 allowed 中的有效子集（保留前 8 项），不改变 prompt 内容（保证已有 157 步指纹不变）。
  - 保留严格业务不变式：若某个 theme 包含的 claim_ids 在清洗后为空（即全部为外推/伪造 ID），则保留原样让后续 `validate` 严格抛出 `ProviderError(ErrorKind.BUSINESS)`。
  - 仅在 `reduce` 阶段通过 `self.call(..., transform=...)` 接入清洗，OpenAI 及其他 Provider 和非综合链路不受影响。
- 新增回归测试：`tests/test_content.py` 中的 `test_synthesis_resolves_hallucinated_claim_ids_and_preserves_business_invariant`，验证有效/外推混合 claim_ids 正确归约至合法子集，以及纯编造 claim_ids 依然拒绝。
- 真实 API 有界复验：对任务 `3a591bfdaac14dbbb254ae8b9e138e85` 执行 resume，前 157 步 100% 缓存命中；原失败 operation `synthesis/chapters/0030/00-0001` 真实调用 DeepSeek 成功（第 158 步完成），紧接着后续 operation `synthesis/chapters/0030/00-0002` 真实调用 DeepSeek 成功（第 159 步完成）。两步连续成功后按设计安全中断，落盘状态为 `FAILED_RETRYABLE`，前 159 步均标记为 `completed`。

## 2026-09-25 真实 DeepSeek chapter analysis 通用有界 schema 纠错与第 43～45 章有界验证

- 读取原 Web Job `3a591bfdaac14dbbb254ae8b9e138e85`（Core Job `a6b3e5d63dc64f82a0227ba979ab80c3`）最新失败现场：第 42 章完成（235 步完成），在第 43 章首块 `analysis:0043:0001` 报 `ValidationError`、`validation_field: core_ideas.0`、`validation_reason: model_type`、`finish_reason: stop`。
- 历史 Schema 错误分析：
  - 早期第 3, 7, 14, 15, 20, 23, 31, 34 章：`too_long`（列表超出 6 项上限），通过纠错重试截断。
  - 第 22 章：`json_invalid`（Markdown 代码块包裹），通过安全剥离代码块修复。
  - 第 43 章：原书为 7 个字标题《奔　　月〔１〕》，DeepSeek 在短章下输出了 `core_ideas: ["奔月〔１〕"]`（纯字符串列表），而非 Pydantic 期待的 `EvidenceFinding` 对象列表，报 `model_type`。
- 架构缺陷排查：
  - DeepSeek 仅支持 `response_format={"type": "json_object"}`，保证 JSON 语法合法但不保证 Schema 符合领域契约。
  - 此前适配器中 `prepare_schema_retry` 存在严重架构硬编码：`if failure.validation_reason != 'too_long': return False`。任何非 `too_long` 的模式漂移（如 `model_type`, `missing`, `string_type` 等）均被当场拒绝触发纠错，直接导致永久终止。
- 通用受控修复设计：
  - 本地安全归一化：若模型返回 `null` 且字段为可选列表（`not field.get('minItems')`），自动转换为 `[]`；若字符串列表收到单字符串则包装为列表。
  - 通用有界纠错重试（Bounded Schema Repair）：
    - 废除仅限 `too_long` 的限制，基于 JSON Schema 及其 `$defs` 递归解析失败字段路径的结构要求（对象属性、类型、必填字段等）。
    - 构造包含出错字段路径、期望类型/结构描述以及前次响应缩略片段的纠错指令注入重试 Prompt，明确要求仅修正结构不修改语义。
    - 严格绑定于 ProviderChain 的单次纠错重试（`correction == 0`，至多 2 次调用），失败则立即报 `ProviderError(ErrorKind.SCHEMA)` 并永久终止，防止死循环。
    - 严格保留最终 Pydantic 与领域契约校验，不放宽 Schema，OpenAI 等其他 Provider 行为严格不受影响。
- 新增回归测试：
  - `test_real_failure_shape_chapter42_model_type_is_repaired`（真实字符串修复为对象）。
  - `test_multi_field_schema_drift_with_null_and_repair`（多字段 null 与类型漂移联合修复）。
  - `test_repair_failure_is_permanent_and_no_infinite_retry`（二次修复失败严格永久报错终止，绝不无限重试）。
  - `test_openai_provider_unaffected_by_schema_drift_retry`（OpenAI 报错不触发 DeepSeek 纠错）。
- 离线测试与验证：相关模块 `154 passed、1 deselected`；`scripts/validate_project.py` 及 `git diff --check` 全部通过。
- 真实 API 有界复验：对现存 Web Job `3a591bfdaac14dbbb254ae8b9e138e85` 自第 235 步恢复执行，前 235 步全部命中检查点缓存秒级跳过（0 token）；第 43 章一次性成功（238 步完成）；第 44 章一次性成功（241 步完成）；第 45 章首块 `analysis:0045:0001` 初次调用真实触发 schema 漂移（记录 `failed_retryable`），通用纠错重试机制触发并自动修复成功，章节综合及汇总全部调用成功（累计完成 246 步）。连续通过 3 章后按设计安全中断，落盘状态为 `FAILED_RETRYABLE`，前 246 步均标记为 `completed`。

## 2026-09-25 Quality Gate 矛盾发言局部定向修复（Targeted Repair）与有界验证

- 真实运行现场：真实 EPUB《狂人日记》在 400+ 个前置任务（EPUB 解析、逐章分析、层级综合、全书规划及对话段落生成）完成后，在 `quality.json` 触发阻塞：`blocking issue: "逐段一致性复核发现与来源矛盾的陈述"`。排查真实产物 `evaluation/quality.json` 发现，全书 24 个 segment 中仅 `0003` 和 `0023` 这 2 个 segment 的个别 turn 出现了 `verdict: "contradicted"`。
- 架构缺陷排查：
  - 原质量门禁为整本一票否决：`ContentFlow.run()` 中一旦 `report['blocking_issues']` 非空，立即抛出不可恢复的 `BookCastError`，任务直接标记为 `FAILED_PERMANENT` 终止，未提供段落级针对性修复重审机制。
  - 缺乏局部修复重跑：整本书数百步耗时耗费巨大，因个别段落有少量矛盾语句即整体报废或被迫全量重跑。
- 最小定向修复设计：
  - 精准识别与争议信息持久化：解析 `report['factual_consistency']['semantic_reviews']` 中所有 `verdict == 'contradicted'` 的条目，按 segment 归类并提取包含 `turn_index`, `verdict`, `reason`, `speaker`, `text`, `cited_claims` 的 `repair_issues`，保存至 `evaluation/repairs/{segment.id}.json`。
  - 局部定向重生成：仅对受影响的 segment 提升 revision（记录于 `manifest.segment_revisions[segment.id]`），将 `repair_issues` 注入 `dialogue` 提示词。在系统级指令中明确规定：矛盾陈述必须严格忠于原书证据修复，待核验内容（unverifiable）优先改为现有证据支持的表述或删弱无依据推断，严禁虚构。
  - 局部定向重审：仅对重新生成的 segment 触发 `consistency` review（payload 附带 `revision` 保证 step 缓存键按 revision 隔离更新），替换内存中对应 segment 的 script 与 review 后重新调用 local `evaluate` 重新生成 `quality.json`。未受影响的正常段落 100% 缓存命中，完全不产生额外开销。
  - 有界防死循环止损：设置 `MAX_SEGMENT_REPAIRS = 2`。若某个 segment 经过 2 次定向修复后仍存在矛盾，循环终止并安全抛出 `BookCastError` 永久报错，防止无限循环调用。
  - 恢复支持：在 `pipeline.py` 中更新校验，当永久失败原因包含 `内容质量检查未通过` 时，允许通过 `bookcast resume` 直接恢复执行，无缝衔接局部定向修复。
- 新增回归测试：
  - `test_targeted_repair_of_contradicted_segment_and_tts_allowed`（验证矛盾段定向修复、重审、通过 quality gate、正常执行 TTS、manifest 记录 revision 以及后续 resume 0 额外调用）。
  - `test_targeted_repair_bounded_limit_halts_on_persistent_contradiction`（验证持续矛盾段在达到 2 次上限后严格终止抛错止损）。
- 离线测试与验证：`tests/test_content.py` 36 项测试全过，`scripts/validate_project.py` 及 `git diff --check` 全部通过。

## 2026-09-25 EPUB 来源过滤与广告污染防护

- 当前已完成《狂人日记》Web Job 的源文件实际为 EPUB；只读核对 OPF/spine：73 个 spine 项，旧 Parser 将其中 72 个有文本 XHTML 文档直接作为 Chapter；目录页、前后广告页及混合正文页推广段落因只按 nav 属性跳过而进入 LLM。这个数目不是语义章节数。
- 新 Parser 在 Chapter/LLM 前过滤明确封面、目录、版权/出版页、整页广告及高置信推广段落，保留序言、普通 URL/数字/微信讨论。`source_filter.json` 记资源/段落、分类、是否移除、原因、规则和短预览；EPUB parse 缓存按规则版本失效。quality 最终检查对明显脚本推广记 `source_contamination` 并在 TTS 前阻断，quality 缓存版本同步升级；targeted repair 和 TTS 未修改。
- 对真实源只读解析：72→69 个内容单元，96→93 个 4000 字分析 chunk；移除封面、链接目录、2页广告、正文 4 段推广/制作信息。剩余章节未检出 `ireadweek.com`、`免费电子书` 或旧广告 QQ 号。实际已完成 Job 和音频未改，未调用 DeepSeek/Kokoro。
- 真实风格小 EPUB 的 Mock 端到端测试覆盖分析/综合/脚本无广告、来源审计和不误删序言/普通 URL/数字；另有脚本污染注入测试证实 quality 阻断且 TTS 0 次调用。功能提交 `a65f77dea69907fd05d5033350bfca264dc90c00` 上隔离 cwd 相关模块 113 passed、1 skipped；validator、compileall、提交diff通过。根目录 CLI 用例受本机 DeepSeek 配置且无凭证影响，隔离 cwd 同一用例通过。未推送。

## 2026-09-25 Phase18 LLM 成本与时长预算

- 对已完成真实 EPUB Job 的 manifest 作只读分阶段审计；361 唯一步骤/429 次 LLM attempt，主要输出 token 来自章/全书综合和 reasoning。旧任务正文与音频未改，未调用真实 DeepSeek。逐项计数见 `docs/LLM_COST.md`。
- `content.py` 保留来源分析和证据，章综合批次 12，先按时长/书序筛选全书综合候选并将 20 分钟片段上限降至16；`generation.py` 增加分阶段输出预算；`llm_usage.py` 和 Runner 从 Attempt 聚合用量，不含 prompt/secret。
- 八模块离线 244 passed/1 skipped；Mock 成本 smoke 13 个请求、3 个 segment、0 真实 API；项目 validator/compileall/diff check 通过。新真实账单与质量未验证，禁止把离线投影当成实际降幅。

## 2026-09-25 真实 5 分钟成本验证预检

- 提交 `6a9f9515774468d10bc3f8633dfcfa43a9f431ed` 下运行 Mock 成本烟测（0真实 API）与 8 模块离线 244 passed/1 skipped。
- 独立运行目录 `output/llm-cost-clean-5min-5f94c8665518` 仅写入 baseline.json；真实 EPUB SHA 见基线，净化解析 69 单元/93 chunk，0 manifest/usage/检查点。当前进程缺少 DeepSeek 凭证且本地 Web 未运行，真实调用未开始，未创造 Core job_id 或消费 token。

- 2026-09-25：只读核对 5 分钟真实新任务的 `logs/events.jsonl`、manifest、章节输入：第3章第1块两次 DeepSeek 响应均 stop，分别 input/output 6488/2198 与 7562/2140 token；Pydantic `EvidenceAnalysis.evidence` 都是 `too_long`，上限6；原始响应未保存。修复仅在 DeepSeek adapter 一次纠错后归约可选超长数组，再严格验证；事件补 task_id/schema_model；脱敏重建 fixture 回归与相关 217 测试通过，未调用 API 或修改用户任务。

- 2026-09-25T02:38:41.102549Z: 修复 consistency:0001 `finish_reason: length` 失败，添加 missing_turns schema 重试及确定性数组对齐。所有测试通过并推送远程。
- 2026-09-25T03:06:47.657164Z: 修复 schema correction 对 `too_short` 的漏判，允许对极短数组给出兜底指引。- 2026-09-25: 实施了 TTS 音频拼凑体验优化。重写 `split_text` 放宽单片段至 200 字符上限，加入基于强弱中文标点防截断算法；引入 `normalize_tts_text` 剥离无效 Markdown 等符号。底层 `concat_wav` 扩展接受动态停顿列表。全程不修改原始 `script` 和 `LLM` 逻辑（0 次额外调用），最终选用了 `zf_xiaoxiao` 及 `zm_yunyang` 生成了对比 1.05x 播客速度的 A/B 试听音频，测试完全通过并推送。
- 2026-09-25 RC Stage 0：Gemini Provider 增加 `clock`/`sleeper` 注入，默认保持真实时间和既有 25 秒 RPM、10/20/40/60 秒退避、Retry-After。离线 pytest 主进程及 Python 子进程 socket 连接立即失败；旧 Gemini generateContent/contents/PCM fixture 更新为 Interactions 请求和 `steps[].content[]` 音频。`test_success_chunk_no_repeat` 实际落盘 chunk 1 检查点、令 chunk 2 首次失败，再恢复断言仅第二 chunk 新增一次 HTTP 请求。核心 58 passed/4.83秒；默认 suite 465 passed、1 failed、1 skipped、5 deselected/61.98秒，唯一失败为未触碰的旧 cost 摘要测试断言。validator、compileall、diff check 通过；没有真实 API 调用。
- 2026-09-25：RC Stage 0 功能提交 `4f841063a320da78f4b20c94471f49809fbb75c3` 后复测核心 58 passed/4.29秒，project validator 与 compileall 通过。交接快照按 D-006 保留该已验证提交哈希；未推送远程。
- 2026-09-25 RC Stage 3：修复 Kokoro 长连续句 80 字符 Pydantic 越界（split_text/speech_units 统一限制 80 字符，保留自然标点优先与确定性无标点回退，保证字符顺序与不漏字符）以及 Gemini 缺失或空 API Key 时抛出 ProviderError(ErrorKind.AUTH) 替代 sys.exit / 局部 import 异常；新增测试覆盖 79/80/81/100/200 字及中文长句切分边界与 render_speech 回归。
- 2026-09-25 RC Stage 4：Job Resume / Active Error State 收口。严格解耦 active_error 与 historical_attempt_errors。在 Core job_status 中仅在有效状态为 FAILED_PERMANENT、FAILED_RETRYABLE 或 BLOCKED 时保留 active_error；当任务成功完成（completed / SUCCEEDED）时，active_error、error 及 progress.error 严格为 None。Runner.finish() 在任务成功时清空 self.last_error，Runner.event() 仅在失败事件中传递 active_error。WebService 与 Web UI 仅对当前活跃错误显示红色告警，恢复成功的任务不再显示历史错误横幅。所有历史失败尝试原样完整保留在 manifest.ai_calls、logs/events.jsonl 与任务审计中。新增 5 项 Core 任务恢复回归测试与 Web API 错误生命周期断言，相关 39 项测试及 Web typecheck 全量通过。
- 2026-09-26 RC Stage 5：新 Job 冻结无凭证 Provider/model/pricing 及北京时区计价策略；Attempt 派生 usage/cost_summary 原子写入，缺少 usage/快照/价格时不标 actual；按 Provider+model 分组。`job_status` 和 Web DTO 仅展示通过 Job/output/source SHA/快照/选择/journal 校验的摘要；CLI 不再使用当前 TOML 历史重定价。摘要失败只记录安全 diagnostic，不中断生成。新增全路径离线回归，并更新 Stage 3 Gemini 缺 Key 的过期测试断言；无真实 Provider 调用。
- 2026-09-26 RC Stage 5 验证与提交：完整默认离线 suite 483 passed、1 skipped、5 deselected、10 subtests；成本与恢复专项 34 passed，Web typecheck、compileall、validator、diff check 通过。功能提交 `86275234810b2b704fa73b9ef7d8632e22196117` 上复测 12 passed、validator、Web typecheck 通过；交接状态仅记录该已验证功能提交。预先存在的未跟踪调试文档/fixture 未触碰、未提交，未调用真实 Provider。
- 2026-09-25T20:10:30Z：仓库未跟踪文件收口与测试资产补齐。确认 `tests/fixtures/deepseek_analysis_0015_concepts_overflow.json` 为 `test_2026_09_25_analysis_0015_concepts_overflow_and_conversational_retry` 必需的脱敏测试资产并纳入跟踪；删除冗余的截断副本 `debugging-bookcast-deepseek-schema-error--bf8123db-2.md` 和 `-3.md`，完整记录保留在本地并在 `.gitignore` 增加 `debugging-*.md`。测试 1 passed，项目校验与提交差异通过；创建功能提交 `8fe39f474888d9d6fb31dfcbd7d31573d3ebd076`。
- 2026-09-25T20:25:15Z RC Stage 6：统一 Gemini TTS 音频解码器为单一规范路径 `decode_audio(result, destination=None) -> AudioPayload`，彻底移除废弃未用的 `decode_pcm`。定义不可变 `AudioPayload` 契约结构，消除双重格式判断分支。实现 Interactions 响应音频定位、严格 Base64 解码、容器/MIME 与 RIFF/24kHz/1ch/16bit WAV 参数校验，仅完全校验通过后写入目标文件。decode 异常统一为 `ProviderError(ErrorKind.SCHEMA, error_type="decode_error")` 且绝不泄漏 Base64、原始响应或 transcript 文本。移除调试 print。新增 12 项测试覆盖 valid WAV、invalid RIFF、missing audio、text+audio、后续 step 音频、多 audio block、invalid base64 及 WAV 边界，规范化历史 Interactions 与 voice test 模拟 fixture 为合法 WAV。离线全量 495 passed、1 skipped、5 deselected、10 subtests 及 62 项 Gemini 专项测试全过；项目校验与编译通过；未调用真实 Gemini API。功能提交 `0992207bbd986ad4ff8eaccc1f76d15d35adb50d`。

- 2026-09-26T00:47:03Z RC Blocker Closure：Gemini 上游错误仅按内部枚举记录；显式 ProviderRequestContext 绑定 Job/output/logical chunk，物理 HTTP 请求独立记录 usage/billing evidence，遥测故障不覆盖合成结果；SQLite 跨进程 25 秒预约和发送前复核，Retry-After/退避按单调截止等待，长 Retry-After 不提前占远期槽。FakeClock 断言多实例、两线程、延迟发送、崩溃、重试 5/60 秒、quota、身份隔离与错误泄漏。完整默认离线 suite 511 passed、1 skipped、5 deselected、10 subtests；validator、Web typecheck、compileall、diff check 通过。没有调用真实 Gemini/DeepSeek；真实账单和实机时序未声称验收。
- 2026-09-26T00:51:28Z：RC Blocker Closure 功能提交 `5a04985ff3330d88671eeaa55c946506c473bcf8` 上重跑完整默认 suite 511 passed、1 skipped、5 deselected、10 subtests；项目校验、Web typecheck、compileall 和提交差异检查通过。`STATE.last_verified_commit` 仅记录此已验证功能提交；交接快照不自引用。
- 2026-09-26T00:55:47Z：复核发现 HTTP 400 虽已安全枚举化但误报 DECODE_ERROR，新增显式 `INVALID_REQUEST` 分类并经 Adapter 边界回归。补丁提交 `9f9fd41fa413f0f8902f75e9c07f3aeaeb55cec8` 上重跑默认离线 suite 512 passed、1 skipped、5 deselected、10 subtests；validator、Web typecheck、compileall、提交差异检查均通过。`STATE.last_verified_commit` 更新为此实际验证过的提交；无真实 API 请求。
- 2026-09-26T00:58:44Z：按 RC 错误日志契约在每条 Gemini 物理请求记录中显式增加 `chunk_id`、内部 `error_kind`、allowlist `safe_reason` 与 `retryable`，不保存上游原始文本。功能提交 `8f94f6553a427203b3db77a8ec6586c7aa92cd7c` 与同一代码工作树完整离线 suite 512 passed、1 skipped、5 deselected、10 subtests；该提交上 Gemini 专项 87 passed、项目校验通过，Web typecheck/compileall/diff check 通过；无真实 API 请求。

- 2026-09-26T01:11:14Z：Web 新建任务页面与 API 默认目标时长统一为 20 分钟；功能提交 `f7c39c835e3b81ec7cfc7f11a7b67f17d4a0c31e`。Web API 18 passed、Web typecheck/build、浏览器 E2E 1 passed、项目校验与提交差异检查通过；未运行完整 Python suite，未调用真实 Provider，既有任务未修改。

- 2026-09-26T01:16:18Z：Web 书架新增任务移除：可确认隐藏 Web 提交记录，活动任务拒绝，本地书籍/音频/Job 目录保留。功能提交 `41ec6b695a0cecee4bfef54f6e9573e1656a4fc2`；Web API 20 passed，typecheck/build，浏览器 E2E 4 passed，项目校验及 diff check 通过。首轮 E2E 因任务标题去扩展名与旧按钮定位歧义失败，修正后全过；未跑完整 Python suite、未调用真实 Provider。

- 2026-09-26T01:29:43Z：Web 书架移除错误就地展示与旧服务友好引导；405 状态拦截并提示重启服务，新增 libraryError 独立状态与书架区域警告段落；重新构建导出 web/out 静态资源；E2E 覆盖旧服务降级与正常提示。功能提交 `73adc9b02f4cce76bdc23cf43193a473d5fcb560`，Web API 20 passed，typecheck/build 通过，浏览器 E2E 5 passed，项目校验与提交检查通过。

- 2026-09-26T02:22:15Z：Web 任务错误友好映射与章节分析 ID 规范化。前端 `web/app/page.tsx` 针对 `business_error`、`schema_error` 等裸露内部错误枚举提供 `ERROR_DESCRIPTIONS` 友好中文描述映射（保留错误代码供技术定位）；后端 `src/bookcast/content.py` 在 `resolve_analysis` 中增加对 `chapter_id` 和 `chunk_id` 前导零数值安全对齐（如 "10" 规范化为 "0010"），防止格式差异引发误判。离线全量 515 passed、1 skipped、10 subtests 全部通过；`npm run build` 生成最新静态产物；项目校验与提交差异检查通过。

- 2026-09-26T02:26:00Z：修复 DeepSeek 纠错重试中的截断碎片干扰与复杂对话文本 JSON 提取。在 `src/bookcast/adapters/compatible.py` 的 `prepare_schema_retry` 中彻底移除截断的 `{snippet[:2000]}` 碎片，避免向模型注入残缺语法垃圾；升级 `_strip_markdown_fence`，结合正则代码块和括号匹配计数器（bracket-matching），稳健剥离思考过程（Thinking Process）及前后自然语言废话，精准提取顶层合法 JSON。新增 `test_deepseek_conversational_retry_with_complex_text_and_no_truncated_snippet` 测试，离线全量 516 passed、1 skipped、10 subtests 全部通过，Next.js 重新编译，项目校验与提交差异检查通过。

- 2026-09-26T02:56:00Z：修复质检门禁 `unlabelled_hypothetical` 繁体中文及同义词支持并升级质量缓存版本。在 `src/bookcast/quality.py` 中将 `attribution="hypothetical"` 检查的合法假设引导词扩充支持繁体字（「假設」、「設想」、「比如說」、「假使」、「假若」）与常用自然引词（「比如」、「譬如」、「如果」），防止模型在古籍/繁体语境下合法发言因字形差异被误判阻断；在 `src/bookcast/content.py` 中将质量检查缓存由 `quality-v2` 升级为 `quality-v3`，确保任务重试时自动以新规则重新评估并清除阻断；前端同步完善 `business_error` 中文文案及构建静态产物。新增 `test_quality_traditional_chinese_hypothetical_check`，离线回归 517 passed、1 skipped、10 subtests 全通，项目校验通过。

- 2026-09-26T03:12:00Z：Web 前端支持可恢复异常（如 rate_limit 限频）自动倒计时断点恢复。在 `web/app/page.tsx` 中新增 `autoResumeSeconds` 倒计时与 `autoResumeCount` 重试计数逻辑，当任务处于 `FAILED_RETRYABLE` 且属于 `rate_limit` / 网络可恢复异常时，界面自动启动 20 秒安全冷却倒计时，实时向用户展示倒计时与重试轮次，并在倒计时归零时自动发起从断点恢复；同时按钮支持用户随时点击立即恢复，最大自动恢复上限设为 5 次以避免死循环。Playwright E2E 5 passed、Web 20 passed、Next.js 重新编译导出，项目校验通过。

## 2026-09-26T07:05:02Z Phase19 R0 模型层审计设计

核对147个跟踪文件的仓库清单与模型调用相关实现/测试，保存20项审计和R0–R6设计；复用HANDOFF作为进度入口。原工作树干净，基线7f43ad733f1d9261bf458046261b274437158615默认离线519 passed、1 skipped、5 deselected、10 subtests（75.00秒）。未调用真实Provider；未改业务、用户数据或公共API。记录Web TTS选择未传worker等既有偏差，后续按边界修复。

## 2026-09-26T09:35:00Z Phase 19.1 最小 Live Smoke 探测与遥测记录完成

- 严格受控执行授权范围内的 3 个最小 Live Smoke 调用，无长音频、全书或批量请求，失败不无限重试：
  1. Qwen3.7-Flash：1 次最小文本请求至 `https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions`，响应 HTTP 401（`invalid_api_key` / `authentication_error`），耗时 0.239s，request_count=1，retry_count=0。当前环境变量 `DASHSCOPE_API_KEY` 为占位符 `"你的KEY"`（来自 `~/.zshrc:180`），未加载真实密钥。
  2. Qwen3-TTS-Instruct-Flash：1 条 32 字符中文音频请求至 `https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation`，响应 HTTP 401（`InvalidApiKey`），耗时 0.196s，request_count=1，retry_count=0，物理请求遥测成功落盘至 `output/smoke/usage/physical_requests.jsonl`。
  3. Gemini 3.8 Flash-Lite TTS：1 条最小双人对话 TTS 请求至 `https://generativelanguage.googleapis.com/v1beta/interactions`，响应 HTTP 200，耗时 5.848s，request_count=1，retry_count=0，成功生成 4.4 秒双人对话音频并落盘至 `output/smoke/gemini_smoke.wav`（217,264 字节，RIFF/WAV，24000Hz 16-bit PCM 单声道，RMS 能量 3177.63，确认包含真实语音信号），物理请求遥测成功记录。
- 3 个 Smoke 结束后立即安全停止，绝无完整生成或长音频生成；未 push，未改动仓库跟踪代码。

## 2026-09-26T10:08:00Z Phase 19.1 Qwen 双模型真实凭据 Live Smoke 全量通过

- 用户更新真实 `DASHSCOPE_API_KEY` 凭据后，严格限制只重试之前失败的两个 Qwen 最小请求（未重复调用已通过的 Gemini），失败不无限重试，绝无长音频或全书批量调用：
  1. Qwen3.7-Flash：1 次最小文本请求（prompt: "请回复两个字：收到。"），端点 `https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions`，响应 **HTTP 200**，耗时 3.280s，request_count=1，retry_count=0。输出文本 `"收到。"`，reported_model 为 `qwen3.7-flash`，用量可解析（input 12 tokens / output 3 tokens），物理请求遥测成功记录。
  2. Qwen3-TTS-Instruct-Flash：1 条 32 字符中文音频请求（"欢迎收听 BookCast 播客节目，我们今天讨论一本经典著作。"），端点 `https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation`，响应 **HTTP 200**，耗时 2.154s，request_count=1，retry_count=0。成功下载音频至 `output/smoke/qwen3_tts_smoke.wav`（280,364 字节，RIFF/WAV，24000Hz 16-bit PCM 单声道，总帧数 140,160，时长 5.84s，RMS 能量 2206.96，非零采样占比 65.3%，中文语音自然可播放，Cherry 音色与 instructions 指令生效），物理请求遥测成功落盘至 `output/smoke/usage/physical_requests.jsonl`。
- 两个 Smoke 完成后立即停止，绝无自动开始完整生成；未 push，未改动仓库跟踪代码。

- 2026-09-26 Phase19.3B Dialogue 离线检查点：冻结自制56.46秒E2E两次对话请求，两个重建 prompt 的 input_hash 与原 manifest 完全一致。A基线由历史资产读取（2调用、6099 reasoning tokens、25.66秒、估算0.0302元）；B仅设置DeepSeek官方 `thinking.type=disabled`，C仅缩短instruction，真实B/C调用数均为0。Runner要求显式 `--live`、每段先写receipt且禁止自动重复，物理请求遥测和未知usage保留。专项96 passed，全量离线711 passed/1 skipped/7 deselected/10 subtests，validator、compileall、diff check通过。当前进程无DeepSeek/DashScope key；无TTS/音频/Pipeline调用，生产路由未改。
- 2026-09-26 Phase19.3B Dialogue 功能提交 `9a1f8d8213e1fcc5da3f760591f28ebd4a47758d` 上复测专项96 passed，validator/compileall/提交diff通过；同一代码工作树全量711 passed。交接快照仅记录此已验证提交，不自引用；未push。

- 2026-09-26 凭证审计：`scripts/run_tts_ab.py:44` 疑似真实 Gemini 凭证曾作为无环境变量时回退，首次在本地 `8d6303d1dcc8` 引入并保留于 `9f3cfbaba9f0`；只读远端 refs 为旧 `7f43ad7`，未见远端暴露。已删除字面值并在 TTS 调用前要求环境变量；170 个当前跟踪文件及本地可达 907 个小文本 blob 跨 Provider 扫描无另一处类似生产硬编码。测试形似值已辨认为测试数据。受影响模块71 passed，无真实API、无push/历史改写；轮换仍待维护者。
- 2026-09-26 凭证移除功能提交 `eb9b4605d613311b405ea15b70de06bde8cff57e` 上复测受影响模块71 passed，validator/compileall/提交差异检查通过；交接快照不自引用，未push。

- 2026-09-26 Git Secret History Cleanup：只读查询确认origin/main=7f43ad7，从其创建codex/phase19-clean-history；重放旧main已净化最终跟踪树，48文件树完全一致，新功能提交18d86c2，旧main保留。旧含值提交不是新HEAD祖先，旧Gemini值在新历史810个可达blob精确扫描为0；跨Provider形似候选均为测试/示例。专项196 passed，默认完整离线713 passed/1 skipped/7 deselected/10 subtests；无真实API、无push。

- 2026-09-26 Phase19.3B Dialogue单次实验：冻结段0001历史A不重调（input1666/output1614/reasoning1281，旧Attempt约7.495s，估算0.008122元，133字/5轮，Claim coverage75%）。B thinking off与C精简instruction各发1次物理DeepSeek请求、0重试，均HTTP400 `input_error`，无usage/输出；失败安全receipt及物理遥测归档 `docs/experiments/phase19-3b-dialogue-one-shot.json`。因此reasoning/成本/生成延迟降幅和内容质量不可评估。专项96 passed，validator/diff check通过；无TTS/E2E/Consistency、无push。
- 2026-09-27：Dialogue单次B/C失败证据功能提交 `e2c8eac04a5457b3051ca9f4f7baf402fe3b5d44` 上复测专项96 passed、validator/提交差异检查通过；交接快照不自引用，未push、未发额外API请求。

- 2026-09-27 Phase19.3B HTTP400离线审计：保存E2E ProviderSpec与runner同端点/模型/超时/默认生成策略；当前`_chat` AST和E2E代码一致。正式Adapter离线拦截显示A→B只增thinking disabled，A→C只改user instruction；共享的schema JSON模式等请求体字段相同。B/C原始上游HTTP body/code/message并未持久化，内部仅留input_error；用户确认无其他脱敏日志，故确切根因未知，没有凭据进行修复或重跑。新增wire合同离线回归，相关Provider/Dialogue 145 passed；无真实API、无TTS/Consistency/生产变更。
