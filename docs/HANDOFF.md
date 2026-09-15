# 给下一位 Coding Agent

更新时间：2026-09-15T09:19:31Z。

## 当前目标

Phase 8 免费开源中文 TTS 已完成并验证功能提交54c05ad；本快照按D-006单独提交。沿用 UI → Application API → Core，中立 Provider 接口和 D-014 缓存策略。用户本阶段未要求 push。

## 刚刚完成与关键文件

- adapters/kokoro.py：可选 Kokoro/sherpa-onnx CPU 适配器，中文双音色、语速/线程、非空 PCM 校验，无网络调用。
- tts_setup.py：显式下载固定官方模型包、大小/SHA 校验、拒绝不安全展开、逐文件收据、安装复用；不覆盖用户配置/损坏模型。
- provider_api/config/registry：speech_units 能力、SpeechUnit/SpeechInfo、local_tts 参数与 Registry；可选 tts extra 同时锁定 sherpa-onnx/core 1.13.8，修复本机动态库漏依赖。
- speech.py：Core 逐语音单元 Step/Attempt、原子保存、配置缓存、片段拼接及实际音色/类型/时长。纯 Mock 保留整段接口，旧有效音频不重做。
- CLI 的 tts setup/doctor/generate 和 Web 播放区：显示实际类型/时长；现有 Skill 只调用 Core，更新了能力说明。
- tests/test_tts.py、tests/test_web.py：安装/参数/缓存/故障/真实SIGKILL与客户端音频类型；docs/TTS.md、examples/tts-local.toml、D-016 和术语说明。

## 当前代码与运行

安装 `uv sync --extra dev --extra web --extra tts`。首次执行 `.venv/bin/bookcast tts setup --config-output data/tts-local.toml`，再执行 doctor --config 和 generate --config。模型约350 MB，安装推荐至少1 GB空间；如果配置已存在，setup 停止而不覆盖，可直接使用已有配置。

本机模型已安装在 data/models/kokoro-multi-lang-v1_0，配置 data/tts-local.toml 已存在。可直接运行：

```sh
.venv/bin/bookcast doctor --config data/tts-local.toml
.venv/bin/bookcast generate examples/content-demo.txt --config data/tts-local.toml --mode two_host --minutes 3 --output-dir output/chinese-tts-demo
.venv/bin/bookcast resume output/chinese-tts-demo/5bdad5ca96f5e42cd019ff30
.venv/bin/bookcast serve --config data/tts-local.toml
```

Web 需 npm --prefix web run build；用新配置启动服务才影响新任务，已存在的任务继续使用快照。旧8765预览曾使用纯Mock，是否仍在运行以进程核验为准，不假设其已切到Kokoro。本机数据与模型不随Git分发。

真实样例 Job：8b3f9a9a16fb4cfeb0b8efe99205764e，Core目录 output/chinese-tts-demo/5bdad5ca96f5e42cd019ff30。MP3约4分36秒、3,318,093字节、24kHz/mono；SHA-256为9a803a13a0977f5a403d69242163383b715c9b5064df8d693e09eb149183f2ae。两个中文声音为小贝45/云希50；51步、37次调用，其中24次真实TTS。完整证据见TTS.md。

## 已运行测试

- 接手基线：236 passed、10 subtests passed。
- 最终工作区全量：262 passed、10 subtests passed；7个既有依赖弃用警告，无失败。
- 功能提交54c05ad上再次验证：262 passed、10 subtests passed（53.03秒）；3项Playwright、Next构建、类型/编译/项目及提交差异检查通过。
- 当前专项：25项TTS +14项Web，共39 passed；两处真实SIGKILL为调用running和完成Attempt/Step提交间隙，验证已完成句子的字节/mtime不变。
- 真实Kokoro CLI完整生成、FFprobe、resume通过；83个产物/状态文件完全不变、无新增调用，缓存检查约0.73秒。
- 功能提交上实际合成2.22秒云希中文样音，重新恢复原demo，83文件与调用数再次保持不变；安装器官方HTTPS重定向也已用1字节探测验证，不重复下载完整模型。
- Playwright 3 passed；Next静态构建、TypeScript、compileall、项目校验、diff检查与Git忽略规则通过。
- 既有7个依赖弃用警告仍在。新增永久错误测试最初误断言底层异常类型，已按Core公开BookCastError契约修正后通过；浏览器首轮沙箱禁止端口绑定，授权本机测试端口后通过。上游漏依赖问题已通过显式core锁定修复。

## 未解决问题与下一步

当前阶段已完成，没有已知失败或阻塞。接手先核对实际Git和STATE，按用户授权决定是否推送或开展下一步。

后续先试听样例，再按授权改进多音字、停顿和长节目自然度。真实中文LLM内容质量未验收，本次脚本来自Mock；质量报告needs_review并提示脚本长度偏离预算。分钟预算不等于实际时长。没有主观听感评分、跨平台实机、M4B、声音克隆、付费TTS或桌面包验收。

本地CPU运行不按HTTP timeout_seconds自动取消；用户终止后手动resume。模型完整性每实例核验后复用摘要，运行中不要修改模型。BookCast自身许可证仍未选择；模型和第三方运行时许可不能混同，见TTS.md。模型下载包不支持Range续传。

## 不要重复做

不要重新下载/生成已验证且完整的本地样例。不要将模型、书籍、音频、配置或密钥提交Git。不要重写Core或把合成/下载放入Skill/UI。不要为永久错误盲目换模型、自动回退付费服务或Mock音调。

从旧Mock整本改成人声应选新的输出目录；按D-014替换Provider会保留已完成音频。改同名Provider音色/语速则用显式--config恢复，仅重建相应语音。不要删除用户旧产物或伪造历史音色归属。

## 最近 Git commit

接手HEAD：a41861a — docs: finalize Phase 7 verification and handoff。
最近已验证功能提交：54c05adcc61d0023acfaaabf0dbd2648df2944a6 — feat: add free local Chinese TTS with resumable speech units（34个文件）。
origin/main为892eb61（Phase 6交接）；Phase 7/8本地提交未push。快照自身的提交用git log -1查看，避免自引用。
