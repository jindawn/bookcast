# Phase 19.3B Dialogue HTTP 400 离线审计

日期：2026-09-27。**没有新真实 API 请求。**对照历史成功的 56.46 秒 E2E `script:0001`、B（thinking off）与 C（精简 Prompt）各一次失败记录。原始 E2E 的 `manifest.json` 保存了 ProviderSpec 与输入哈希，但没有完整 HTTP request body；B/C 的安全物理遥测只保存端点、模型、状态与内部错误类型，没有 request body。下表的完整 wire 字段是从冻结 fixture、原 manifest ProviderSpec 和**同一个正式 `CompatibleLLMProvider`**离线拦截 `_request` 前的参数重建，不冒充已捕获的历史逐字节 HTTP 包。`CompatibleLLMProvider._chat` 的 AST 与 E2E 阶段代码 `6b9584d` 完全一致。

| 最终 wire 字段 | A 历史成功（离线重建） | B 失败（离线重建；端点/模型有遥测） | C 失败（离线重建；端点/模型有遥测） |
| --- | --- | --- | --- |
| endpoint | `https://api.deepseek.com/chat/completions` | 相同 | 相同 |
| model | `deepseek-flash` | 相同 | 相同 |
| messages shape | system schema 指令 + user 冻结 Prompt；system 1384 字、user 2694 字 | 与 A 两条消息哈希均相同 | system 与 A 相同；user 2559 字，仅 `instruction` 改动 |
| thinking | 不发送 | `{"type":"disabled"}` | 不发送 |
| reasoning-related params | 不发送 `reasoning_effort` 等 | 除 thinking 外不发送 | 不发送 |
| response_format | `{"type":"json_object"}` | 相同 | 相同 |
| max_tokens | 不发送 | 不发送 | 不发送 |
| stream | `false` | `false` | `false` |
| tools | 不发送 | 不发送 | 不发送 |
| tool_choice | 不发送 | 不发送 | 不发送 |
| timeout | 120 秒 | 120 秒 | 120 秒 |
| 其他 extra/body 参数 | 无 | 无 | 无 |

历史 ProviderSpec 与 runner 共同使用 `openai-compatible`、`api_key_env=DEEPSEEK_API_KEY`、同端点/模型/超时，`generation` 和 `reasoning_policy` 在 A/C 均为 null。B 只增加 thinking disabled。runner 直接调用正式 `CompatibleLLMProvider.for_task(...).generate_structured(...)`，并未维护第二套 HTTP payload 生成器；与正式 Pipeline 的差别在于不经过 `ProviderChain` 的恢复/纠错编排，这不会改变本次首个 HTTP 请求的 wire body。冻结 A/B prompt 的 manifest 输入哈希已吻合。

## 两次 HTTP 400 的现存响应证据

| 字段 | B | C |
| --- | --- | --- |
| HTTP status | 400 | 400 |
| 内部归类 | `input_error`，不可重试 | `input_error`，不可重试 |
| 物理请求数 / 自动重试 | 1 / 0 | 1 / 0 |
| 脱敏 HTTP response body | **未保存** | **未保存** |
| 上游 error code | **未保存** | **未保存** |
| 上游 error message | **未保存** | **未保存** |

`src/bookcast/adapters/compatible.py` 的 `_request` 在 HTTPError 分支读取最多 64 KiB body，`classify_http` 只用 `code/type` 分类，然后将原始 body、code 和 message 全部丢弃；物理遥测只保留内部 `error_kind`。本地 `output/llm-reasoning-ab/dialogue/` 仅有两份失败 receipt、`physical_requests.jsonl` 和旧 baseline metrics；仓库的 [one-shot 证据](phase19-3b-dialogue-one-shot.json)已归档这些安全记录。不能从 `input_error` 逆推出上游 code/message，更不能编造脱敏响应正文。

## 结论与边界

两次 400 的**共同条件**是端点、模型、JSON 结构化模式、正式 Adapter、超时及当前环境凭证；它们与历史 A 的可重建 wire 字段相同。B 特有的 thinking 参数不能解释 C 同时失败，C 特有的 Prompt 精简也不能解释 B 同时失败。可能涉及服务端模型/账号/请求要求变化，但现有证据无法区分。**确切根因未定位；没有可证实的代码修复；当前不具备安全重跑 B/C 的条件。**

最小下一步是取得两次历史请求的可信脱敏上游 `error.code/type/message`（若提供方控制台或代理日志仍有），并核对当前官方 API 文档；这一步不应发起新请求。若旧响应无法追回，需要用户另行授权独立、有界的诊断请求，并先设计只记录 allowlist code/message 的安全观察点。不要覆盖旧 receipt，不要改生产 Provider/Router、TTS 或进入 Consistency。
