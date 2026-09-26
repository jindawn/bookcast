# 模型调用层可替换性重构

日期：2026-09-26。设计基线：`7f43ad7`。本文件记录稳定设计；最新进度、已验证提交和精确接手步骤统一在 [HANDOFF.md](HANDOFF.md)，机器状态在 [STATE.json](STATE.json)。不新增第二份 agent-handoff。用户授权先审计、设计，再实施首个兼容最小阶段；不授权 push、改数据库、删 Provider、修改公共 API、切换现有默认或大量收费测试。

## A. 当前架构与审计索引

审计范围为跟踪文件清单、架构/决策/产品/交接、全部模型调用入口和相关测试；未读取用户书籍、凭证或修改用户 Job。不是一次新的全产品安全认证。现有 RC 能力由基线和阶段后的默认离线 suite 验证。

```text
CLI / Web API → composition → Registry → ProviderChain
                                  ↓            ↓
书籍 → Parser → ContentFlow / Pipeline → Protocol → Adapter → API/本地模型
                         ↓                 ↓
                    manifest v3       Attempt + physical telemetry
                         ↓                 ↓
               SpeechUnit / SpeechSegment → WAV → MP3 / 独立 M4B
```

| 审计项 | 实际位置与现状 |
| --- | --- |
| 1 LLM Provider | `provider_registry.py` 注册 mock、openai-compatible、local；后两者共用 `adapters/compatible.py`。DeepSeek 是配置实例，不是单独 type；没有 Qwen LLM 专用 adapter。 |
| 2 TTS Provider | mock、kokoro-local、qwen-local、gemini-tts。Qwen 本地实验模型不是目标 Qwen 云 API。 |
| 3 模型名 | `ProviderSpec.model`、examples、onboarding 配置模板；本地 Kokoro/Qwen 固定模型由校验器保护。Gemini onboarding 为 3.8 Flash，旧 example 仍为 3.1 preview。 |
| 4 优先级 | `ProvidersConfig.llm_priority/tts_priority`，Registry 构建链；不是任务路由。 |
| 5 fallback | `ProviderChain.execute/restore`：瞬时错误有界换供应商、运行内禁用失败实例，成功者持续工作；恢复时依据 journal 恢复选择。永久错误不换模型。 |
| 6 retry | DeepSeek 在兼容 adapter 准备 schema repair、Chain 最多一次纠错；无通用 LLM HTTP backoff。Gemini adapter 自身最多四次 retry，10/20/40/60 秒加 jitter，尊重 Retry-After。不能在 Router 再包一层重试。 |
| 7 rate limit | Gemini adapter 的 `GeminiRequestLimiter` 用本机共享 SQLite 原子预约、发送复核，最低 25 秒。LLM 和其它 TTS 无同等共享限流。不能误称全局都有 RPM 控制。 |
| 8 cost | `cost.py` 冻结创建时 config/pricing 快照，按 `(provider, model)` 归集；缺 usage 或费率为 partial/unavailable；TTS 成本尚 unavailable。现有 actual 标签是按 token/费率计算，不是供应商结算账单。 |
| 9 usage | `generation.ProviderUsage`、`models.AIAttempt`、`llm_usage.py`、音频 sidecar；Gemini physical_requests 独立记录 HTTP 次数/时间/计费证据。逻辑 Attempt 数不等于物理重试数。 |
| 10 脱敏 | `provider_api.classify_error/ProviderError`、兼容 HTTP 分类/schema_failure、Gemini safe_failure/sanitizer。load_config 不输出 Pydantic 原始配置值。 |
| 11 Web 选择 | `Submission.tts_engine` 仅 auto/gemini/kokoro，WebService 存 settings_snapshot；worker 调 configured_pipeline。LLM 只能服务端 TOML。页面与 onboarding 仍包含厂商名称。 |
| 12 CLI 选择 | generate/resume/retry 的 `--provider`、`--tts-provider` 是配置实例名称；auto 使用 priority；config providers 不联网，doctor 会检查远程模型。 |
| 13 Pipeline | `_Runner.ai_operation` → Chain.execute，传 invoke/persist/observe；ContentFlow 通过 generate_structured，Speech 通过 units/segments capability；Core 不导入厂商 SDK。 |
| 14 已有接口 | Provider、LLMProvider、TaskLLMProvider、TTSProvider、UnitTTSProvider、SegmentTTSProvider、ProviderRequestContext；不另造第二套接口。 |
| 15 重复逻辑 | 多入口重复读取 priority/选择展示；Compatible 中三处按 DeepSeek 主机名分支；Gemini 限流/遥测没有跨厂商复用契约；音频解码已统一，不应重写。 |
| 16 DeepSeek 任务 | 所有 extraction、chapter_synthesis、book_synthesis、dialogue、consistency，取决于选中链；`generation.task_type/POLICY` 只选 reasoning 参数，不选模型。Planner 是本地算法。 |
| 17 Gemini 任务 | 云端多 speaker TTS，仅使用 SpeechSegment。已有 Interactions/style/voice/24k WAV decoder；本仓库无 Gemini LLM。 |
| 18 旧 TTS | Kokoro 仍支持且有本地验收；Qwen local 仍 experimental；Mock 用于测试，不允许真实 TTS 回退 Mock。全部保留。 |
| 19 schema | provider_config.py 的严格 Pydantic + TOML schema_version=1；generation.py 参数契约；运行 manifest v3；开发 STATE v1 是另一契约。 |
| 20 测试 | test_providers、test_generation、test_content、test_job_recovery/cli；test_tts、test_qwen_tts、test_gemini_tts/interactions/retry/safety_accounting、test_tts_*；test_cost_*、test_web/onboarding/skill。tests/conftest.py 默认主/子进程禁网，Gemini 虚拟时钟；live 单独显式启用。 |

## B. 耦合与文档偏差

1. 现有抽象足够，欠缺的是需求到配置实例的 Router；重写接口或 pipeline 会增加风险。generation 的 thinking/request_fields 带 DeepSeek 方言，不能直接当成 Qwen 的通用参数。
2. TTS Core 用 80 字 SpeechUnit 或 600 字/24 turn SpeechSegment；原有链不能混用这两类能力，也不能把 Qwen unit 与 Gemini segment 放进同一 fallback 链。历史 HANDOFF 曾称 200 字，实际代码是 80，以代码为准。
3. `web_service.py` 保存 `tts_selection`，但 `web_worker.py` 新任务调用 `configured_pipeline(settings, output)` 未传保存的 selections。显式 UI TTS 选择可能失效；恢复路径又会读取保存快照。这是既有缺陷，须用离线集成复现后单独修复，不在首个 LLM 阶段顺手改变用户行为。
4. `cost._selected` 和 UI 使用 priority 给出配置摘要；路由后这只能代表配置候选池，不是每次调用的实际来源。实际归属继续看 Attempt。最终入口阶段需明确多路由展示，修改公共 DTO 前须说明并取得授权。
5. 架构文档“没有数据库”仅适用于 Job 持久化；D-024 已引入 Gemini limiter SQLite。D-019 的旧 generateContent 已被 D-024 Interactions 局部替代。
6. 旧 TTS 在同能力链内可逐片段 fallback，不具备 episode 单供应商承诺；必须保持旧配置语义，同时让新质量路由只选择一个供应商。

## C. 推荐架构与取舍

推荐增量方案：`业务 task → 中立 profile → Router → 现有 Chain → 现有 Protocol → Adapter`。Router 只选择候选；Chain 继续拥有 Attempt、纠错与 fallback；Adapter 继续拥有协议、音色、物理请求及厂商限流。复用现有 manifest/usage/cost，不引入 SDK 隐藏重试、模型目录重组或第二条 pipeline。

替代方案：仅按 URL/model 改配置虽最小，但不能按任务路由，也不能隔离 TTS 差异；全面新建 LLM/TTS 框架可以统一外形，但会重写 RC 持久化、引入两套状态。本方案避免这两类问题。

### LLM 路由（首个实现阶段）

- TOML schema_version=1 增加可选 `llm_routing`；不配置时连序列化都不增加空字段，旧缓存/快照保持兼容。
- general/cheap/complex/high_quality 四个 profile 各给一个有序实例列表：首项是首选，其余是**显式 fallback**。四个 profile 均需声明，空链/重复/未知/错误 kind 拒绝。
- 路由实例必须属于 llm_priority 候选池；该池仍可被旧 CLI/Web 状态列出。绝不隐式外发到未选端点。
- 中央 task_profiles 默认 extraction→cheap、chapter_synthesis→general、book_synthesis→complex、dialogue/consistency→high_quality、other→general；允许按现有 TaskType 配置覆盖，不修改业务代码，不猜文本复杂度。
- 每个 profile 自己的 Chain 保持 sticky failover/恢复，互不污染。例如 complex 成功不能让下一次 cheap 自动跟到 DeepSeek。
- 明确 `--provider NAME` 优先于路由，仅单实例，继续允许显式选择不在 auto priority 内的已配置 LLM。
- Router 不加 retry、不吞异常、不替换持久化的真实 provider/model；缓存保持 D-014：路由改变只影响未完成工作，同名模型/有效生成配置改变才失效。整本改模型另建 Job。
- 默认映射到哪个厂商只存在显式新配置中；框架不包含 Qwen/DeepSeek 字符串。首阶段用 mock 验证路由，不宣称目标云模型已接通。

计划配置形状（需配套 providers，示意本身不是可运行配置）：

```toml
llm_priority = ["qwen", "deepseek"]
[llm_routing]
general = ["qwen"]
cheap = ["qwen"]
complex = ["deepseek"]
high_quality = ["deepseek"]
# 如显式允许 general 故障外发到 DeepSeek，可设 general = ["qwen", "deepseek"]。
[llm_routing.task_profiles]
extraction = "cheap"
chapter_synthesis = "general"
book_synthesis = "complex"
dialogue = "high_quality"
consistency = "high_quality"
other = "general"
```

### Adapter / capability

保留 ProviderSpec 的 model/base_url/api_key_env/timeout 与 Registry 扩展点。Qwen LLM 可复用兼容 HTTP transport，但 thinking/structured 模式要独立 adapter 映射；不得盲发 DeepSeek 的 request_fields。DeepSeek JSON 容错留在 adapter，后续可抽为私有方言对象而非散到 Router。

仅声明确实使用并验证的 capability。已有 structured/text/speech/unit/segment/multi_speaker/cloud/local 足够首阶段。新增 style/voice/max_chars/languages/rate 时必须有 adapter 契约测试；context/tool/streaming 暂不使用，不填猜测能力。不支持所请求的 style/multi-speaker 明确 INPUT 错误；仅可对显式允许的非关键特性降级并记原因。

### TTS 质量与一致性（后续）

standard→Qwen 云 Instruct，high→Gemini Flash-Lite 是新 opt-in 配置的目标，不是旧默认迁移。一个 Job/episode 在开始语音前解析一次，获得单一 TTS Provider；标准与高质量二选一。保留单位与片段路径，Qwen 使用逐句边界，Gemini 使用逐段边界，角色/style 由 adapter 映射。

新质量路由默认禁止跨 Provider fallback。先复用各自有界 retry，失败保留明确可恢复状态。自动整集 fallback 延后：必须在不覆盖已完成音频的独立语音 generation 中重做全部语音并成功后原子发布，同时记录 degradation/原费用；这涉及用户可见行为，须先说明，不能默默从中间换声。首版不承诺该功能。旧 tts_priority 链保留原义，不擅自把所有旧 Job 升级成新策略。

### Usage / retry / rate limits

复用 AIAttempt、ProviderUsage、音频 sidecar、cost_snapshot 和 physical_requests。ProviderResult 如确需增加，仅为它们的调用结果视图，不再平行持久化一份费用真相。记录实际 provider/model、服务端 reported_model、logical/physical 请求及 retry、输入字数/token、输出 token/时长、延迟和 fallback 顺序；未知价格/实际账单留空，不用 0 表示免费。

LLM 泛用 transient retry/速率配置作为独立阶段：必须保证每次可计费尝试可审计、有界、永久错误不重试。Gemini 先不改已验收 limiter、退避、decoder、error sanitization；以后参数化也保留最低间隔与虚拟时钟用例，不在路由外层重试整个有内部 retry 的 adapter。

## D. 文件与实施阶段

| 阶段 | 范围、文件 | 完成标准 |
| --- | --- | --- |
| R0 审计设计 | 本文、HANDOFF、STATE、WORKLOG、DECISIONS、ARCHITECTURE、ROADMAP | A–G 落盘、基线离线证据、小提交 |
| R1 首个最小阶段 | provider_config.py、新 model_routing.py、provider_registry.py；test_model_routing.py、conftest、PROVIDERS、离线 example | opt-in LLM routing 完整可运行；旧默认/快照/接口不变；路由/override/fallback/恢复/成本验证 |
| R2 LLM 方言接入 | adapters/compatible.py、新 adapters/qwen_llm.py、registry/config、test_generation 或专属测试、新显式云 example | Qwen wire fixture、DeepSeek 原有纠错/usage 保持、无需真实 key 通过；目标模型名仅在配置 |
| R3 Qwen 云 TTS | 新 adapters/qwen_cloud.py、config/registry、provider_api capability（必要时）、专项测试 | text/speaker/style 映射，安全音频获取/解码、有界 retry/physical usage、80 字兼容；不修改 qwen-local |
| R4 TTS quality | model_routing/config、composition/speech 必要的集级选择、离线 tests | standard/high/override；单集单 provider；禁止中途换声；Gemini Lite 用模型配置并验证 wire 契约 |
| R5 usage 与策略 | generation/llm_usage/cost、adapter telemetry；test_cost_* | logical 与 physical 分开，snapshot/partial/未知成本、routing history、延迟；旧历史不伪造 |
| R6 入口和迁移 | CLI/onboarding/WebService/worker、必要的 UI 与 docs/tests | 先复现并修复保存选择未传 worker；公共 API 或默认切换前按用户门槛说明；旧 Job 原样恢复 |

每阶段可独立回滚代码；不回滚/删除用户生成数据，不改数据库或 manifest schema。R1 是本轮设计后的首个可交接实现范围，R2–R6 是后续计划，不能标成已完成。

## E. 风险与验证门槛

- 缓存：新字段为空的旧配置序列化必须一致；新路由恢复不重做已完成 Attempt；同名模型变更仍按原规则失效。
- sticky 状态：不同 profile 独立恢复，临时错误不能把所有任务强度混成同一模型。
- 外发：fallback 必须显式列举；认证/schema/business 不换模型；Mock 音调不能掩盖真实失败。
- 模型版本：API 别名会漂移，配置名不是版本冻结保证；保留服务端模型 ID，不硬填版本。
- 协议：Gemini 文档可能继续变化。此次检索中的 speech_config 示例与本仓库对象结构存在差异，必须在 R4 用官方 REST schema 核验；不能仅因模型名可配置就宣称 Lite 已验收。
- 计费：已有 LLM actual 标签不代表结算账单，TTS 和物理 retry 成本仍缺口；不能汇总成确定总价。
- Web：现有 TTS override 传递缺陷和 ready=任一候选可用的粗粒度定义要在 R6 明确；首阶段公共 API 不变。

R1 最少测试：general/cheap/complex/high_quality、自定义 task map、明确 override、不存在 Provider、所有可 failover 错误、永久错误、DeepSeek 一次 schema retry、无隐式备用、profile 隔离、恢复、旧序列化/缓存、真实 Pipeline 的 journal/usage/cost 与完成态。默认 suite 继续覆盖全部 RC Gemini/TTS/安全/CLI/Web。新增 test module 必须登记离线 tier。

后续 TTS 最少测试：standard/high/override、role/voice/style、长文本、retry/永久错误、单集一致性、失败不串联两个 Provider、跨 unit/segment 显式拒绝、旧配置恢复、脱敏、未知成本、无真实网络。真实 smoke 仅提供明确 opt-in 命令，另获授权后用自制短文本，不自动运行。

## F. 兼容与停止门槛

- 旧无配置仍 Mock；旧 deepseek-kokoro、deepseek-gemini、qwen-local 等方案保留。目标默认仅用于后续新增且显式选择的路由方案；替换既有默认须先说明。
- schema_version=1 的旧配置读写不凭空增加空路由字段；保存的 Job 配置优先于 cwd；R1 不新增 CLI 参数、HTTP 字段或改现有字段含义。
- 旧客户端/二进制不能读取带新字段的 opt-in 配置，需要回滚时使用原旧配置；不能承诺旧二进制向前理解新功能。
- 新路由按 D-014 保留已完成调用，不承诺重新选择 profile 会重制整本；已有 provider 不删除，无待删除旧代码。
- 公共 API、数据库 schema、默认行为、Provider 删除、新付费基础设施、批量真实调用均需停下来说明。

## G. 官方核对（2026-09-26，仅文档检索，未调用模型）

- [Qwen3.7 Flash 模型页](https://www.alibabacloud.com/help/zh/model-studio/qwen3-7-flash)：确认目标名称存在；R2 仍需核对部署区域、兼容 HTTP 请求与 thinking 参数。
- [DeepSeek 当前调用说明](https://api-docs.deepseek.com/guides/harness)：官方推荐 `deepseek-flash`，旧 `deepseek-v4-flash` 可接受但实际由 V4.1 Flash 服务。配置可保留用户指定别名，不能声称运行的是已退役 V4 固定权重。
- [Qwen TTS API](https://www.alibabacloud.com/help/en/model-studio/qwen-tts-api)：Qwen3 Instruct 系列支持 instructions，单输入有长度限制；区域 key/URL 需匹配；这是云服务，与本地 CustomVoice 不同。
- [Gemini TTS 指南](https://ai.google.dev/gemini-api/docs/speech-generation)：列出 `gemini-3.8-flash-lite-tts`，有 turn-level speech_metadata；只确认协议/产品存在，不等同本项目已验证听感或费用。
