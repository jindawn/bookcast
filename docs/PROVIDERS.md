# Provider 使用与扩展

Phase 7 的 [Web 界面](WEB.md) 只读显示 Registry 状态，配置仍在本地 TOML。CLI 与 worker 共用 composition.py；浏览器不接受密钥、端点配置，也不直接调用 Provider。恢复沿用任务快照，改配置须用 CLI 的显式 --config。

Phase 2 提供统一契约、配置、注册表、调用状态和选择性切换。默认全部 Mock；远程服务和本地模型均需用户自行准备。未增加厂商 SDK 或 Python 依赖。

## 命令与配置

```sh
uv run bookcast config providers --config examples/providers.toml
uv run bookcast doctor --config examples/providers.toml
uv run bookcast generate examples/example.txt --config examples/providers.toml --provider auto
uv run bookcast generate examples/example.txt --config examples/providers.toml --provider secondary --resume
uv run bookcast status <job> --json
```

`config providers` 输出机器可读 JSON，不连接服务、不读取密钥值。`doctor` 输出 JSON，检查 Python 3.12+、FFmpeg、Provider 健康状态和能力；兼容 LLM 只请求 `GET /models` 并检查指定模型是否存在，不生成内容。每条优先级链至少有一个可用且具有对应能力的 Provider、运行环境正常时退出 0，否则退出 1。健康检查是瞬时诊断，不能保证之后的额度或生成请求成功；不支持 models 接口的服务会被报告不可用。

[示例配置](../examples/providers.toml) 中的字段：

| 字段 | 含义 |
| --- | --- |
| `schema_version` | 配置契约版本，当前为 1 |
| `llm_priority` / `tts_priority` | 对应类型的 Provider 配置名称列表，按先后尝试 |
| `failover_on` | 默认四类可切换错误；可缩减，空列表禁用切换 |
| `providers[].name` | 唯一配置名称，命令行选择此名称 |
| `kind` / `type` | llm 或 tts，以及注册类型 |
| `model` | 请求和审计记录中的模型标识 |
| `base_url` | 兼容 API 根地址，通常含 `/v1`，不含密钥、账号、query 或 fragment |
| `api_key_env` | 可选密钥环境变量名；配置该字段但变量为空会报认证错误 |
| `timeout_seconds` | 单次 HTTP 操作超时，默认 30，范围 0.1–120 秒 |

注册组合：`llm/mock`、`tts/mock`、`llm/openai-compatible`、`llm/local`。兼容 LLM 使用 `/chat/completions`；结构化生成使用 JSON mode、提示中的 JSON Schema 和本地 Pydantic 校验，不自动降级为未校验文本。local 使用相同协议，仅允许 loopback 主机；远程地址要求 HTTPS，拒绝重定向。BookCast 不安装本地模型，不内置真实 TTS。

启用外部端点意味着允许将当前文本块、精选证据、综合主题、片段脚本、提示和 schema 发给该端点及已配置备用端点。只保存环境变量名，环境变量值不会进入配置输出；HTTP 请求中的认证头、服务错误正文和原始异常消息不写入 manifest。配置中不要在模型名、名称或 URL 路径夹带 Secret。生成内容仍是本地用户数据，不加入 Git。

## 错误与切换策略

| 错误类别 | retryable | 自动切换 |
| --- | --- | --- |
| `rate_limit` | true | 默认允许 |
| `quota_exhausted` | true | 默认允许 |
| `temporary_unavailable` | true | 默认允许 |
| `timeout` | true | 默认允许 |
| `authentication_error` | false | 禁止，修复凭证后显式 retry |
| `input_error` / `schema_error` / `business_error` | false | 禁止，修复原因后显式 retry |
| `interrupted` | true | 本次进程停止；下一次 resume 处理 |

429 根据错误 code/type 区分额度耗尽和限流；401/403 为认证错误，408/504 为超时，其余 5xx 为临时不可用，其余 HTTP 错误为输入错误。额度分类依据 [OpenAI 错误码文档](https://developers.openai.com/api/docs/guides/error-codes)，兼容厂商若返回不同代码需在适配器补充映射。异常消息不参与分类；未识别的异常归为业务错误，避免切模型隐藏程序缺陷。

同一次运行，每个失效 Provider 最多尝试一次后禁用，成功接管者继续后续任务；没有后台重试、无限轮询或隐式 Mock 回退。重启从最近可匹配调用位置恢复；新的运行可重新尝试先前失效的服务。链全部耗尽则保存失败并退出，用户修复配置或额度后用 `--resume` 继续。`retryable` 描述故障性质，实际切换还受 `failover_on` 限制。

## 调用状态与可复现性

manifest v3 的 `ai_calls` 为逐次尝试日志。每个 attempt 在调用前先保存 `pending`，再保存 `running`；写入并校验输出后保存 `completed`，异常保存 `failed_retryable` 或 `failed_permanent`。同一次尝试的状态原地更新，切换 Provider 会追加一个新 attempt。每个条目包含：

`id`、`task`（例如 `analysis:0007:0002` 或 `consistency:0002`）、`kind`、`status`、`provider`、`model`、`prompt_version`、`input_hash`、`output_hash`、`artifacts`、`timestamp`、`error`、`retryable`，以及只读六态 `state` 和具体实例的 `provider_config_hash`（旧调用可为空）。

未成功的调用 `output_hash=null`。当前每次调用输出一个 JSON 或 WAV，output_hash 为文件 SHA-256。输入哈希覆盖版本化提示/结构 schema 或脚本哈希/音频契约版本。时间是 UTC ISO 8601。`provider_status` 报告各已调用 Provider 的名称、模型、可用性、last_error、retryable、rate_limited、quota_exhausted、authentication_error；`doctor` 报告当前配置的全部实例。

Phase 4 的文本块分析、综合节点、片段脚本、逐段一致性复核和 TTS 是最小任务；规划是本地确定性计算。完成步骤同时检查输入、提示、产物哈希和 Provider 配置摘要。同名实例配置变化会使该实例的旧调用失效；选择另一实例接管保留有效完成调用及其原始归属。具体规则见 D-014 和 [JOBS.md](JOBS.md)。进程在 attempt 完成后、步骤完成前退出时，可从 attempt 产物恢复；遗留 pending/running 标为 interrupted 后重新处理该最小任务。远端已完成但本地尚无完成记录的窗口无法保证不重复请求或计费。

v1/v2 迁移分别保留原始 `manifest.v1.json` / `manifest.v2.json`；v1 使用原配置指纹验证旧产物，再标记步骤 `legacy=true`。不会伪造旧调用的 provider/model/time。旧 pipeline_version=1 保留原流程；新内容任务使用 pipeline_version=2、content-v1 提示。模式/预算和修订规则见 [CONTENT.md](CONTENT.md)。仓库开发状态 [STATE.json](STATE.json) 仍为独立的 v1 契约。

## 恢复配置

CLI 创建任务时保存通过严格 schema 校验的配置快照及 LLM/TTS 选择，只有 api_key_env 的环境变量名，没有密钥值。resume/retry 和 generate --resume 默认沿用该快照；使用 --config 才读取新的配置文件。旧任务没有快照时，只允许能匹配原配置摘要的默认 Mock；其他任务要求显式 --config，避免切换工作目录后误用服务。

链位置恢复还匹配具体实例配置摘要；同名而配置改变的实例不会沿用旧调用位置。永久失败使用 retry；原 generate --resume 为兼容保留显式重试语义。环境变量值改变不修改配置摘要，但可修复认证后重试。

## 增加 Provider

1. 实现 [provider_api.py](../src/bookcast/provider_api.py) 契约：公共 name/model/cache_key、health_check、capabilities；LLM 实现 generate 和 generate_structured，TTS 实现 synthesize（24 kHz 单声道 PCM16 WAV）。
2. 在适配器内将故障转换成安全的 ProviderError；单个生成方法对应一个被追踪的服务调用，不在内部隐藏重试或多个付费子调用。
3. 使用 `ProviderRegistry.register(kind, type, factory)` 注册工厂，在组合入口配置注册表；不要改 Pipeline，也不要让业务代码 import 厂商 SDK。CLI 默认注册项集中在 [provider_registry.py](../src/bookcast/provider_registry.py)。
4. 加入离线契约和故障注入测试；更新文档与决策后再启用真实服务验证。

未知注册类型、错误优先级、重复名称、错误 kind 和永久错误切换策略均在启动时拒绝。当前运行串行、单本书加文件锁；并发调度、成本预算和真实语音尚未实现。Phase 4 的复核不新增 Provider，沿用相同错误分类，不因质量失败盲目换模型。
