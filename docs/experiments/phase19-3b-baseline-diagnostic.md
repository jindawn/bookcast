# Phase 19.3B Baseline A 单次诊断（2026-09-27）

冻结段0001的原 prompt/schema hash 与历史 `baseline_input_hash` 一致。诊断使用正式 `CompatibleLLMProvider._chat`，`deepseek-flash`、`https://api.deepseek.com/chat/completions`、`response_format=json_object`、`stream=false`、120秒超时；未发送 thinking、reasoning 参数、tools 或 max_tokens。使用 `_chat` 避免结构化校验层可能发起的修复重试。

独立收据位于忽略目录 `output/llm-reasoning-ab/dialogue/diagnostics/baseline-0001.json`，发送前创建。该尝试返回内部 `temporary_unavailable`，物理遥测 `http_status=null`、`retryable=true`、耗时约0.016秒；没有 HTTP 2xx/4xx响应、上游 code/message/request_id 或 usage。没有自动重试。当前不能判定 Baseline A 在 DeepSeek 上会是200还是400，也不能判定 model、JSON mode 或 payload schema 的兼容性。按条件门禁，Candidate C 和 B 均未调用。

正式适配器新增仅针对 DeepSeek HTTP非2xx的安全观察：状态、选定的上游code/message、request/trace ID、content-type、有界且重建的脱敏body摘要；物理遥测只加安全字段，不保存摘要、原始响应头、Authorization、Key或prompt；原有 `error_kind` 分类不变。离线注入回归覆盖400分类、限长、脱敏、遥测和单请求收据。

下一步先确认此执行环境到 DeepSeek 的网络通路。用户限定的本轮Baseline诊断已尝试一次，不能自动重发；再次真实请求需要新的明确授权。不要运行 C/B、Consistency、TTS或完整E2E。
