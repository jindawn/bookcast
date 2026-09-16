# 给下一位 Coding Agent

更新时间：2026-09-16T12:15:32Z。实际代码与 Git 优先；不要依据旧快照推断远端状态。

## 当前目标

Phase 10：保持 Kokoro 逐句恢复，扩展 Gemini 原生多说话者片段 TTS。技术实现和真实同脚本双路生成已完成；人工试听及 API 项目 free/paid 层级仍待用户确认，不能宣布全部主观验收通过。不 push，不开始大型本地 TTS 模型阶段。

## 刚刚完成与关键文件

- provider_api.py / provider_config.py：中立 SpeechTurn、SpeechSegment、SegmentSpeechInfo 与 speech_segments/multi_speaker/cloud 能力，严格显式云发送配置、音色/style、永久 permission_denied。
- adapters/gemini.py / provider_registry.py：官方 generateContent REST，标准库无新增依赖，环境 Key，安全错误枚举、usage/model 审计、PCM s16le→标准 WAV。模型来自配置，Core 无 Google SDK。
- speech_segments.py / speech.py / content.py / pipeline.py：同主题最多600字符/24发言，一段一个 Step/Attempt，沿用缓存、锁、故障接管、WAV/MP3 合并；Kokoro Adapter 未修改。
- scripts/tts_ab.py：只导入已校验的内容检查点，在独立输出目录委托现有 Pipeline 重渲染音频；阻止任何 LLM 请求，来源记录标明导入 Attempt，不篡改历史归属。
- test_gemini_tts.py / test_tts_ab.py / test_live_gemini.py：离线协议/错误/缓存/额度/真实 SIGKILL、A/B不重算内容、环境变量显式启用的实际任务恢复验收。
- examples/gemini-tts.toml、TTS/PROVIDERS/JOBS/ARCHITECTURE/CONTEXT、D-019、PHASE10_TTS_AB、README/ROADMAP/PRODUCT 同步实现与边界。

## 当前代码与真实产物

Phase 9 来源目录 output/phase9-deepseek/5bdad5ca96f5e42cd019ff30，Job 7229d03765db4d1c860c7bd18d62b178；原 manifest 摘要复验未变，不要重调13次真实 LLM。两份新任务脚本逐字及 SHA 一致，均继承已有两处来源归属警告。

- Kokoro：output/phase10-kokoro/podcast.mp3，Job 9bc80d198c7e467c9c812ce7701d1313。23单元成功，音色45/50、speed=0.8，320.267208秒，3,844,269字节。
- Gemini：output/phase10-gemini/podcast.mp3，Job 73d55e8127b04176af60545146de5240。实际模型 gemini-3.1-flash-tts-preview，Kore/Puck，3段成功，251.440秒，3,018,285字节。第三段曾一次 schema_error，受控显式重试后成功；前两段产物不变。
- Gemini 全部4尝试服务端 input/output tokens 为1428/12018；成功3次为1055/8048，reasoning/cache-hit 未返回保持null，无费用估算。失败也可能计费。
- 文件哈希、生成时间、复现命令与未试听表在 PHASE10_TTS_AB.md。音频/模型/Key/运行日志均在忽略目录，不随 Git 分发。
- 环境 GEMINI_API_KEY 已可用，不打印值、不读取浏览器凭证。不要由模型调用成功推断免费层或 AI Pro credit。云端声明 data_tier=unknown。

## 已运行测试

- 接手基线135 passed；TTS/内容专项103 passed；新增 Gemini 专项48 passed（包含第6段quota恢复与单段损坏修复）。
- A/B客户端与联网隔离专项47 passed、1 skipped（该次早于新增两个恢复用例）。最终完整367 passed、10子测试、2联网默认跳过，7个既有警告，49.78秒。
- 真实 Gemini 任务显式联网验收测试1 passed、0.28秒；完成后禁HTTP恢复43个文件 SHA/mtime 和 Attempt 数完全不变。额外同时禁止LLM/Kokoro/Gemini调用恢复两份真实任务，Kokoro83文件、Gemini43文件完全不变。
- 官方模型查询可用；故意无效Key的实际请求正确归为 authentication_error。其他服务故障是离线注入，未实际耗尽额度或制造5xx。
- 两份MP3通过FFprobe、FFmpeg完整解码和非静音检查，24kHz单声道。
- project validator、compileall、diff检查已通过；196个源码/文本产物/日志秘密字面值扫描无匹配。前端未修改，未重跑浏览器E2E；Python全量含Web后端。

## 未解决问题与下一步

1. 用户已收到A/B音频和试听问题，尚未评分。自然度、停顿、多音字、英文缩写、数字日期、双角色听感、漏字/增字和长段稳定性均不得编造；约4–5分钟样本不等于长节目验收。
2. API项目实际free/paid未知，已询问用户；不能声称免费层实际可用。免费层与付费层的数据使用政策不同，见TTS官方链接。
3. Gemini preview第三段首轮schema失败，未保存原始响应，具体坏字段无法确认；不是100%稳定。无自动schema重试，不清理失败记录。
4. 同能力链内failover；未完成逐句任务不能直接改用分段链。完成历史仍按D-014保留；另建音频对照用tts_ab客户端。
5. 远端已处理但本地未保存的窗口可能重复计费；跨平台/重启整机/长书暂无实机验收。当前REST官方页已标Legacy，未来如需迁移Interactions只改Adapter。
6. 收到人工反馈后补 PHASE10_TTS_AB/STATE；未授权不要推进新阶段或push。

## 如何运行

```sh
.venv/bin/bookcast doctor --config examples/gemini-tts.toml
.venv/bin/bookcast status output/phase10-gemini --json
.venv/bin/bookcast resume output/phase10-gemini
.venv/bin/pytest -q
python3 scripts/validate_project.py
.venv/bin/python -m compileall -q src tests scripts
git diff --check
```

普通pytest不调用网络；真实测试需 BOOKCAST_RUN_LIVE_GEMINI=1、GEMINI_API_KEY 和 BOOKCAST_LIVE_GEMINI_OUTPUT。永久错误修复后retry。A/B继续只渲染音频使用scripts/tts_ab.py --resume，完整命令见验收记录；常规resume依旧使用正常Core缓存规则。

## 不要重复做

不重写Core/Provider/Job/Skill/Web，不重装现有Kokoro模型，不重调已完成DeepSeek内容；不静默云上传、不回退Mock伪装成功、不保存原始服务错误或Key、不编造试听分数。不删除原任务或失败尝试，不主动push。

## 最近 Git commit

本阶段接手 HEAD 与本地 origin/main 均为 91b4bcb43d35acf43fe2c40a8bfd19aa3e836914（Phase 9交接），当时工作区干净，Phase 9已推送。此阶段尚待创建功能提交；此前last_verified_commit仍为17fc1f1f9bac7f31aa1f9ab4c539fa625811421a。按D-006在实际功能提交上验证后再保存最终SHA；快照自身通过git log -1获取。
