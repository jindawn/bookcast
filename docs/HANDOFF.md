# 给下一位 AI Agent 的交接

快照更新：2026-09-13T13:08:53Z。Phase 2 功能已提交并验证，最终交接完成。机器状态见 [STATE.json](STATE.json)。

## 当前目标

让 BookCast 的 LLM/TTS 可替换，模型故障或额度耗尽时从最小未完成任务恢复；本阶段不增加找书、真实语音、OCR 或 M4B。Phase 2 授权范围已完成，功能提交为 `2f06b0107af9470f6a8758b8a1620ac8ab8f7cbe`；本次提交保存在本地，尚未推送。

## 刚刚完成了什么

- 统一 LLMProvider/TTSProvider、能力、健康状态和安全错误分类；Pipeline 无具体适配器或厂商 SDK 导入。
- Registry 支持 Mock、OpenAI-compatible/local LLM 工厂；TOML 指定优先级，密钥仅用环境变量名引用。
- rate limit、quota、temporary unavailable、timeout 有界切换；认证、输入、schema、业务错误停止，不用模型替换掩盖错误。
- manifest v2 逐次记录 AI 调用状态、实际 provider/model、prompt_version、输入输出哈希和 UTC 时间；每次调用和产物立即持久化。
- 已完成章节不会因更换 Provider 重做；强制退出后恢复最小未完成分析/脚本/TTS，覆盖调用完成而步骤尚未完成的窗口。
- v1 manifest 原样备份为 manifest.v1.json，再验证迁移旧产物，legacy=true；没有旧调用审计时不伪造。
- CLI 增加 config providers、doctor、generate --provider auto / --tts-provider / --config。
- 修正旧 ROADMAP 对 Phase 1 提交受阻的过期描述，并将 Phase 2 范围调整为本次用户要求；深度解析移至后续候选。

## 修改的关键文件

- [provider_api.py](../src/bookcast/provider_api.py)、[provider_registry.py](../src/bookcast/provider_registry.py)、[provider_chain.py](../src/bookcast/provider_chain.py)：中立契约、组合与切换。
- [provider_config.py](../src/bookcast/provider_config.py)、[compatible.py](../src/bookcast/adapters/compatible.py)、[providers.py](../src/bookcast/providers.py)、[prompts.py](../src/bookcast/prompts.py)：配置、适配器、Mock 与提示版本。
- [pipeline.py](../src/bookcast/pipeline.py)、[models.py](../src/bookcast/models.py)、[cli.py](../src/bookcast/cli.py)：最小任务持久化、迁移与命令。
- [test_providers.py](../tests/test_providers.py)、[providers.toml](../examples/providers.toml)、[PROVIDERS.md](PROVIDERS.md)：故障注入测试、配置示例和使用指南。
- README、ARCHITECTURE、ROADMAP、PRODUCT、DECISIONS（D-009 / D-010）、STATE、HANDOFF、WORKLOG 同步本阶段状态；本地 bookcast.toml 加入 gitignore。

## 当前代码状态及运行方法

```sh
uv sync --extra dev
uv run bookcast config providers --config examples/providers.toml
uv run bookcast doctor --config examples/providers.toml
uv run bookcast generate examples/example.txt --provider auto
uv run bookcast generate examples/example.txt --provider auto --resume
uv run bookcast status <job> --json
```

默认使用 Mock；示例配置的三层 LLM 同样全部 Mock。真实兼容 LLM 为显式选择，外发范围、配置格式和错误规则见 [PROVIDERS.md](PROVIDERS.md)。TTS 仍输出测试音调 WAV，经 FFmpeg 合并 MP3。输出目录保持 output/{book_id}/ 的 Phase 1 布局。

Provider 配置改变后必须 --resume，保留已完成章节及原调用归属；需要整本改用另一个模型时选择新 --output-dir。恢复看 steps 与 ai_calls 两层记录，不应删除已有输出。运行 manifest v2 与仓库 docs/STATE 的 v1 是不同契约。

## 已运行测试与结果

环境为 macOS、Python 3.12、FFmpeg 可用。在功能提交 `2f06b0107af9470f6a8758b8a1620ac8ab8f7cbe` 上运行完整测试、编译、项目校验与提交空白检查，全部通过。`.venv/bin/python -m pytest -q`：78 passed、10 subtests passed；5 个既有 PyMuPDF SWIG 弃用警告。compileall 与 git diff --check 通过；python3 scripts/validate_project.py 通过。三级 Mock 配置的 config/doctor/generate/resume/status 冒烟通过：doctor ready、MP3 生成、integrity=ok，6 次 AI 调用且 resume 后 manifest 字节不变。

新增测试验证：A 正常、A 第七章额度/限流/超时/临时故障、B/三级链接管、TTS 切换、链耗尽后换配置恢复、强制进程退出、调用完成窗口恢复、旧任务迁移、永久错误与损坏输出不切换、HTTP 协议及无 Secret 错误记录。前六章哈希和修改时间保持不变。全部自动化测试离线运行。

## 未解决问题与技术债

- 当前无未解决测试失败或实现阻塞。新增测试首次出现 5 个失败，原因是断言错误地要求时间以 Z 结尾；修正为验证合法 UTC ISO 8601 后通过，应用时间格式未改。
- 真实远程服务和真实本地模型未联网验收；各服务对 JSON mode、models 列表和错误码的兼容程度可能不同。
- 内置真实 TTS、内容质量评估、超长章节分段、预算、后台退避和并发未实现。
- 远端成功但本地结果尚未落盘的中断窗口可能重复请求或计费；本阶段不承诺外部调用恰好一次。
- 历史 v1 调用没有完整 provenance，迁移仅验证旧产物；新旧产物可以混合，不能伪造旧模型信息。
- PDF 按页解析，扫描件无 OCR；复杂 EPUB/TXT 分章、M4B、跨平台音频验证、CI 和许可证仍是后续事项。

## 下一步建议

1. 按 AGENTS 阅读文档并核对实际 Git；当前无进行中任务或 blocker，下一步见 STATE.next_actions。
2. 用户确认后续范围后，再考虑用授权小样本验收真实兼容 LLM；不要将协议 Mock 测试当作内容质量验收。
3. 单独规划真实 TTS、长章节、成本边界或深度解析；当前未开始 Phase 3。

## 不要重复做的事情

- 不重新初始化仓库，不重建 Phase 0/1，不恢复已过期的提交权限 blocker。
- 不因换 Provider 重做有效章节，不把所有错误都配置为可切换，不绕过接口 import 厂商 SDK。
- 不把测试音调称为真实人声，不声称已验证付费服务，不提交书籍、输出或凭证。
- 不追逐 STATE 自引用提交哈希；遵守 D-006。

## 最近 Git commit

- 当前已有 Phase 1 功能提交：9e6e5aaa0db1a61320be9c28815caaf7c4b3f5cc；交接提交 4bc8123。
- Phase 2 功能提交：`2f06b0107af9470f6a8758b8a1620ac8ab8f7cbe`（feat: add provider registry and resumable failover），已验证。
- 最终交接快照只更新文档；其自身提交用 `git log -1 --oneline` 查询，遵守 D-006。
