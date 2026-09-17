# 给下一位 Coding Agent

更新时间：2026-09-17T11:03:27Z。实际代码、Git和实验产物优先；不要依据旧快照推断进程或远端状态。

## 当前目标

Phase 11：Research → 单候选真实 Spike → Go/No-Go → 最多一个正式 Adapter。当前尚未完成决定或接入。用户明确允许本机资源/稳定性不合格时 No-Go；不得强行加入半成品，不主动 push。

## 刚刚完成与关键文件

- 已读取入口、架构/决策/产品/Provider/任务/内容及状态文档；接手HEAD和origin/main均639e1bc4fc600ba1b7d385a60f1cac321b5ed02a，Phase 10已推送。历史快照中的“未push”不是当前远端状态。
- docs/TTS_PROVIDER_EVALUATION.md：官方Qwen/CosyVoice版本、许可、设备/能力/体积对照、单候选选择、固定权重及实验方法和数据。
- scripts/spikes/qwen_native.py：仅实验用，五类自制文本、权重SHA、离线门禁、耗时/RSS/MPS记录、超时、独立输出；不注册Provider，不创建Core任务。
- scripts/spikes/qwen-macos-requirements.txt：独立实验环境的冻结依赖；Core安装和pyproject未改。
- tests/test_qwen_spike.py：损坏权重拒绝、联网环境在import/写文件前拒绝；CI不需要Qwen、模型或网络。
- README/ARCHITECTURE/PROVIDERS/ROADMAP/TTS同步研究边界，并修正两处滞后描述：实际已有Gemini注册和Phase 9真实内容技术验收。

## 当前实际代码与实验状态

Core、Kokoro、Gemini、Registry和Job格式未修改。只选择Qwen官方1.7B CustomVoice；没有安装CosyVoice、第三方Mac fork或第二套Pipeline。

本机M2 Pro/10核/32 GiB、macOS26.5.1。隔离环境data/phase11/qwen-env：Python3.12.14、qwen-tts0.1.1、torch/torchaudio2.11.0。早期2.8.0仅做import/MPS预检；已核查并升级匹配的官方arm64 wheel。受限沙箱MPS不可见，宿主的禁止网络sandbox-exec配置可以使用MPS，不能把前者误判成模型不支持。

data/phase11/model的官方固定修订版0c0e3051f131929182e2c023b9537f8b1c68adfe已下载；约4.52GB，两项safetensors SHA与官方匹配。许可证/模型卡/元数据/逐文件hash/安装日志保存在data/phase11/research。不要重新下载有效文件。实验均env -i、HF离线开关、系统拒绝网络，未传API密钥或发送文本。

FP32/eager真实五项全部生成：中文7.12秒/耗时41.45秒；第二声线10.72/44.22；207字长段47.04/202.61；中英11.60/46.82；数字日期14.88/57.52。合计91.36秒音频/392.63秒合成，加权RTF4.30。模型加载11.39秒；RSS约4.38GB，MPS driver最大采样约13.23GB，不相加。产物output/phase11-qwen-mps，全部WAV完整解码通过。尚未人工听校或证明优于Kokoro。

截至此快照，BF16+SDPA同模型实验已启动：output/phase11-qwen-bf16-sdpa/events.jsonl，日志data/phase11/research/mps-bf16-sdpa.log。先检查实际产物/进程状态再继续，禁止重复启动相同目标或覆盖结果。

## 已运行测试

- 接手TTS/恢复专项：85 passed，15.64秒。
- 本阶段全量离线：369 passed、10子测试、2联网默认跳过、7个既有警告，56.66秒；之后实验记录字段/工作目录/attention参数有小改，专项2 passed。
- Project validator、compileall、git diff --check通过。
- FP32五份WAV由FFmpeg完整解码；技术检查不等同实际发音/音质验收。
- 阶段最终完整测试及提交后验证仍待完成。

## 未解决问题

- FP32实测RTF高于实验前设定的≤3目标；低精度是否更快且稳定尚未验证。
- 没有五类文本的人工逐字听校，也没有新模型与Phase 10完整相同脚本的三方试听。
- Phase 10主观试听/API项目free或paid资格仍未知；保留在原验收记录，不阻塞本次研究。
- 一次自动审批曾因额度不足拒绝查询PyTorch最新版本；恢复时间后用户继续，查询与后续实验均已成功，不是当前阻塞。

## 下一步

1. 读取BF16+SDPA结果；如有必要，仅对同一模型做有界精度诊断，不能同时再接CosyVoice。
2. 根据五类文本、资源与速度决定是否接入；通过后复用UnitTTSProvider/Core Step/Attempt/Artifact/cache/resume，并用scripts/tts_ab.py复用Phase 10内容；不得重调DeepSeek。
3. 如未达成本/稳定性门槛，明确记录限定环境的No-Go和后续可选路线，不把“未测”写成“失败”。
4. 完成三方表（未做/未听如实标示），全量测试、文档、STATE/HANDOFF/WORKLOG和小提交。不主动push。

## 不要重复做

- 不重写Kokoro/Gemini/Core/Job/Skill/UI，不重新生成Phase 9内容或有效Phase 10音频。
- 不把模型、音频、密钥、实验环境加入Git；不复用浏览器Cookie，不暗中调用云TTS。
- 不将预设两voice、WAV可解码、官方benchmark等同真人听感或无漏字。
- 不把隔离实验脚本称为已接入的生产Provider，不虚构resume或SIGKILL验收。

## 最近已存在的Git commit

- 639e1bc4fc600ba1b7d385a60f1cac321b5ed02a：Phase 10交接文档；本次接手时与origin/main相同。
- 8761c3911b5e9e43d272671b4b613c1f2c67b0dd：Phase 10功能，旧last_verified_commit。本快照自身的提交通过git log读取，避免自引用；不能据此推断当前是否已push。

Phase 9/10详细产物、Job ID、用量、恢复记录见[PHASE9_REAL_LLM.md](PHASE9_REAL_LLM.md)和[PHASE10_TTS_AB.md](PHASE10_TTS_AB.md)。
