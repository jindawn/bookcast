# Phase 10 同脚本 TTS 验收

日期：2026-09-16。技术链路已完成，主观试听待用户反馈；这不是科学 benchmark。免费层资格尚未确认，不将一次成功请求推断为免费使用。

## 输入与复现

使用自制 `examples/content-demo.txt` 经 Phase 9 真实 DeepSeek 生成的三个播客脚本，共1182字符。原任务 `7229d03765db4d1c860c7bd18d62b178` 位于 `output/phase9-deepseek/5bdad5ca96f5e42cd019ff30/`。两份新任务导入13个已有 LLM Attempt 作为来源记录，未重新生成内容；`tts-ab-source.json` 保存原 manifest 摘要及脚本哈希，原 manifest 摘要复验不变。既有两处 source 归属警告保留。

| 原脚本 | SHA-256（两份副本一致） |
| --- | --- |
| 0001.json | fb51bc7f421ee805c6ee6d753f5bd1740ea787a362e9af082240f189b224a25f |
| 0002.json | eaa3a4e2e175e48af369ee6dc94a81602444a8d6b8f0101de87c88533e4ae04e |
| 0003.json | 06a7a00cb05708bb864f30d175cf8379d1142ef23e2ad2588525ec695b34517a |

在已有 Phase 9 本机产物上运行（输出目录首次必须不存在；本地文件不随 Git 分发）：

```sh
.venv/bin/python scripts/tts_ab.py output/phase9-deepseek/5bdad5ca96f5e42cd019ff30 output/phase10-kokoro --config data/phase9-demo.toml
.venv/bin/python scripts/tts_ab.py output/phase9-deepseek/5bdad5ca96f5e42cd019ff30 output/phase10-gemini --config examples/gemini-tts.toml
# 只按目标任务保存的配置恢复；内容失效则停止，不会调用 LLM
.venv/bin/python scripts/tts_ab.py output/phase9-deepseek/5bdad5ca96f5e42cd019ff30 output/phase10-gemini --config examples/gemini-tts.toml --resume
BOOKCAST_RUN_LIVE_GEMINI=1 BOOKCAST_LIVE_GEMINI_OUTPUT=output/phase10-gemini .venv/bin/pytest tests/test_live_gemini.py -q
```

`data/phase9-demo.toml` 是本机忽略文件，等价公开 `examples/deepseek-kokoro.toml` 的 Kokoro 设置将 speed 改为0.8。A/B 工具只取传入配置的 TTS，LLM 继续使用原任务配置及有效缓存。Google Key 只在环境中；没有将 Secret 或原始服务错误写入文档/manifest/log。

## 实际结果

| 项目 | A：Kokoro local | B：Gemini Developer API |
| --- | --- | --- |
| Job ID | 9bc80d198c7e467c9c812ce7701d1313 | 73d55e8127b04176af60545146de5240 |
| 模型 | kokoro-multi-lang-v1_0 | gemini-3.1-flash-tts-preview（响应同名） |
| 双角色 | 45 / 50，speed=0.8 | Kore / Puck，原生 multiSpeakerVoiceConfig |
| 最小成功任务 | 23个 speech_units | 3个 speech_segments |
| 新增 LLM 请求 | 0 | 0 |
| 新增 TTS 尝试 | 23次成功 | 3次成功、1次 schema_error |
| MP3 | output/phase10-kokoro/podcast.mp3 | output/phase10-gemini/podcast.mp3 |
| 时长 | 320.267208秒 | 251.440秒 |
| 大小 | 3,844,269字节 | 3,018,285字节 |
| 格式 | MP3、24kHz、单声道 | MP3、24kHz、单声道 |
| 生成历时 | 首个TTS开始到完成约225.14秒 | 首轮约119.38秒；显式重试约42.92秒，另有人工诊断等待 |
| SHA-256 | 1f653023b4fd3eb529ed91291f3bd338aa00642855c012296d4933979f3ddfe4 | 347ebf02830745ca2f6e65a3319bc5b7278226b076dacc0236edceb0004d0382 |

以上历时来自持久事件时间，运行时还同时执行了测试与对照生成，不能视为标准性能比较。实际 WAV 严格检查并通过 FFmpeg 全量 MP3 解码及非静音检查。Gemini REST 返回音频转换为标准 WAV，两个真实 voice 被配置并记录；主观可区分程度需试听。

Gemini 首轮前两段成功，第三段永久 schema_error。未保存原始响应，不能追溯确认具体坏字段；错误没有被自动模型切换掩盖。一次人工控制的显式重试只调用第三段，返回单 candidate、单音频 part、STOP，并完成 MP3。前两段 WAV/sidecar 哈希复验不变。不能据此宣称 preview 输出100%稳定。

服务端用量（input/output tokens）：第一段338/2448，第二段344/2549，第三段失败373/3970，第三段重试373/3051。全部尝试合计1428/12018，成功调用合计1055/8048；reasoning/cache-hit 未返回，保留null。失败响应也可能计费，这些是官方用量字段，不是价格或账单估计。

实际模型查询成功；故意无效的测试 Key 被官方拒绝并映射 authentication_error。真实 quota/rate-limit/403/timeout/5xx 未人为制造，仅以官方结构化协议离线注入验证。API 项目 free/paid 未知；模型文档有免费层不代表此 Key 使用免费层。订阅、credit、数据条款及官方来源见 [TTS.md](TTS.md)。

## 恢复与兼容

完成后真实验收测试禁用 Gemini HTTP，再次恢复；43个文件的 SHA/mtime、Attempt 数均不变，1项通过（0.28秒）。离线测试覆盖第6段 quota 后B只做6–8段、原5段不变；真实 SIGKILL 覆盖调用中和完成 Attempt/Step 间隙；损坏单段只重做该段；同名音色配置失效不重做 LLM；完成的旧 Mock/Kokoro 任务保留原产物。Kokoro Adapter 未修改。

远端已生成、但本地尚未落盘时被杀的请求，仍可能重复计费。两个模式的未完成任务不能直接混链，需原能力链恢复或独立任务。预览 API 使用官方文档仍提供的 generateContent（Legacy）；当前主指南推荐 Interactions，未来迁移限于 Adapter。

## 人工试听表

已向用户提供两份本地音频，当前没有收到评分；以下全部为待验收，不填写虚构分数。

| 项目 | A评分/备注 | B评分/备注 |
| --- | --- | --- |
| 自然度与机械感 | 待试听 | 待试听 |
| 停顿与断句 | 待试听 | 待试听 |
| 中文发音与多音字 | 待试听 | 待试听 |
| 英文缩写、数字日期 | 待试听；当前脚本样本有限 | 待试听；当前脚本样本有限 |
| Host A/B 区分度 | 待试听 | 待试听 |
| 对话感与语气连续性 | 待试听 | 待试听 |
| 长段稳定性 | 仅约5分钟样本，未验收长节目 | 仅约4分钟样本，未验收长节目 |
| 缺字、增字或异常重复 | 未逐字听校 | 未逐字听校 |

主观量表建议1–5分，同时记录时间点和具体发音/断句，不把分数包装成科学测量。自动检查确认音频格式与文本输入一致，不证明音频逐字正确或质量优秀。

## 测试状态

新增 Gemini 专项48 passed；完整离线367 passed、10 subtests passed、2联网默认跳过、7个既有弃用警告（49.78秒）。Project validator、compileall、diff检查通过；196个受检源码/文本产物/日志无环境Secret字面值。额外同时禁止所有 LLM/TTS 调用恢复两份真实任务，Kokoro83文件和Gemini43文件均保持SHA/mtime不变。前端没有修改，未重跑浏览器E2E；Python全量含Web后端。

功能提交8761c3911b5e9e43d272671b4b613c1f2c67b0dd上再次完整复验367通过/10子测试/2跳过（49.53秒）；实际任务显式恢复1通过（0.27秒），未新增API请求。validator、compileall、提交diff通过。
