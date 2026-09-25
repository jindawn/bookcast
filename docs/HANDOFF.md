
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
