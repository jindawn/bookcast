# 给下一位 Coding Agent

更新时间：2026-09-22T23:30:53Z。实际代码、Git和产物优先；不要用历史快照推断当前远端状态。

## 当前目标与结论

Phase 11唯一实验Qwen Adapter及真实技术验收已完成，最终离线/本地恢复复验通过。人工三方试听尚未收到反馈；保持experimental，不推荐默认quality。CosyVoice仅研究，未安装或接入。用户要求不主动push，不开始下一阶段。

## 刚刚完成与关键文件

- [TTS_PROVIDER_EVALUATION.md](TTS_PROVIDER_EVALUATION.md)：官方版本、许可、设备/能力/体积比较，单候选五类文本实验、Go/No-Go及同脚本三方表。
- src/bookcast/adapters/qwen.py、qwen_assets.py：唯一可选Adapter、固定官方修订和12项SHA，延迟导入运行时，标准24kHz单声道PCM16。
- provider_config.py、provider_registry.py、cli.py：显式experimental配置、注册和doctor；复用local_tts字段避免旧任务配置序列化变化。
- pyproject.toml、uv.lock、examples/qwen-local.toml：可选qwen extra及无密钥配置；默认安装不增加Torch，原依赖版本未变。
- tests/test_qwen_tts.py、test_live_qwen.py及spike测试：离线边界、缓存、Core恢复及显式真实产物零调用复验。
- README、架构、Provider、TTS、Job、产品和路线图同步范围。运行见[TTS.md](TTS.md)。

## 当前代码与真实产物

Core Pipeline/Job/SpeechUnit、Kokoro和Gemini Adapter未改。Qwen复用逐句Step/Attempt/Artifact；voice/style/seed、模型资产、运行时版本和音频契约参与缓存。没有第二套Pipeline或默认云端回退。

宿主Apple M2 Pro/10核/32 GiB、macOS26.5.1。隔离环境data/phase11/qwen-env：Python3.12.14、qwen-tts0.1.1、torch/torchaudio2.11.0。官方模型data/phase11/model修订0c0e3051f131929182e2c023b9537f8b1c68adfe，约4.52GB，固定文件SHA通过。模型、许可卡、日志和环境在忽略目录data/phase11；不要重复下载。

FP32/eager五类文本：91.36秒音频/392.63秒生成，加权RTF4.30。BF16/SDPA：83.52秒音频/237.36秒生成，加权RTF2.84；首句3.30，其余低于3。BF16加载2.85秒，RSS峰值约2.97GB，MPS driver最大采样约10.29GB；两种口径不能相加。全部WAV完整解码，无超时/OOM；不等于发音正确或听感优于Kokoro。

正式Qwen产物：output/phase11-qwen-ab/podcast.mp3，385.040秒（6分25秒）、4,621,581字节。Job d2bfc59060ff403fb276e805eeccf663，SHA 2af78ad6733a03d328da91b7f3a7163b552d16002ddb20fc8e78daa41c2a383c。Vivian/Uncle_Fu共23成功单元，另有1次受控SIGKILL中断。创建至完成935.62秒包括中断、恢复和检查，不是纯推理benchmark。

Kokoro/Gemini历史产物分别output/phase10-kokoro/podcast.mp3和output/phase10-gemini/podcast.mp3，320.267208/251.440秒。三方脚本JSON SHA一致，Qwen沿用13条LLM审计且逐项不变，新增LLM请求0。实际Qwen生成清空环境、HF离线开关、macOS系统禁止网络，没有云API调用。全部实验进程已结束。

## 已运行测试

- Qwen/实验脚本专项13 passed；旧TTS/Provider专项121 passed。
- 本次工作区完整离线380 passed、10子测试、3显式验收默认跳过、7既有警告，48.37秒。
- 真实第7单元RUNNING时SIGKILL退出137；恢复前6个WAV/sidecar SHA、mtime、大小均不变，只重新开始第7中断任务。
- 完成后禁止模型加载/TTS/LLM/HTTP再resume：84文件SHA/mtime不变，显式测试1 passed（2.69秒）；Kokoro83/Gemini43历史文件复验不变。
- 三份MP3完整解码；Qwen为24kHz单声道、非静音。最终validator、compileall及diff通过；本次实际禁推理复验1 passed/3.43秒。108仓库文件与75份Phase 11文本产物/日志扫描环境Secret值无匹配，模型音频被Git忽略。

## 未解决问题

- 真人三方试听和逐字听校未完成；自然度、停顿、多音字、英文缩写、数字日期、漏字及角色区分未打分。实验Provider可交付，quality推荐需等待实际证据。
- 仅测试上述M2 Pro/32 GiB，未验收8/16 GiB、CPU或其他系统，也未验证长书稳定性。未实现voice cloning或流式输出。
- SIGKILL留下0字节临时文件audio/units/.0001-0007-0001.wav.cmb62mzp.tmp，未被引用为Artifact、不影响恢复。本阶段保留此清理债务，不修改Core。
- 原真实内容仍有两项来源归属needs_review；Phase 10免费/付费账户层级及人工试听仍未知，详见原记录。

## 下一步与不要重复做

1. 收集同脚本三方试听反馈；无反馈时保持experimental，不能声称优于Kokoro。
2. 不主动push，不开始新模型/新阶段，不重写Core/Kokoro/Gemini/Job/Skill/UI。
3. 不重调DeepSeek，不重生成有效音频，不重复下载已校验模型；本地零调用复验命令见选型文档，缓存失效会失败而非偷偷推理。
4. 不把模型、音频、密钥或环境加入Git；不将可解码及两预设voice等同人工验收。

## 最近已存在的Git commit

- 6902999b2a0e4183fbce54d59058e759ae3fe41a：Phase 11官方研究与隔离spike。
- 639e1bc4fc600ba1b7d385a60f1cac321b5ed02a：Phase 10交接，接手时HEAD和origin/main均为此提交。
- 8761c3911b5e9e43d272671b4b613c1f2c67b0dd：先前已验证Phase 10功能；功能提交验证后更新STATE完整SHA。

本快照自身提交通过git log -1读取，遵循D-006避免自引用；不据此推断当前远端状态。Phase 9/10历史证据见[PHASE9_REAL_LLM.md](PHASE9_REAL_LLM.md)和[PHASE10_TTS_AB.md](PHASE10_TTS_AB.md)。
