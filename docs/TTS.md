# 本地中文语音

BookCast 的真实 TTS 首选 Kokoro 多语言模型，经 sherpa-onnx 在本机 CPU 上合成。无需 API Key、付费服务或 GPU；首次安装下载模型，之后生成不联网。默认未配置环境仍运行 Mock 测试音调，核心 CLI 不依赖 TTS extra。

高质量本地模型的Phase 11选型、Mac实测和接入门禁见 [TTS_PROVIDER_EVALUATION.md](TTS_PROVIDER_EVALUATION.md)。Qwen已作为显式实验Provider接入；CosyVoice未接入。下面Kokoro安装方式保持不变。

## 可选实验：Qwen原生Mac

仅验收Apple M2 Pro/32 GiB、macOS26.5.1、Python3.12.14、官方Qwen3-TTS-12Hz-1.7B-CustomVoice。Adapter固定MPS/BF16/SDPA，未支持CPU、CUDA、其他模型或克隆声音。上游并无全面原生Mac支持承诺；不能据本机结果保证较小内存设备可用。五类样本的持续生成加权RTF约2.84（生成耗时/音频时长），较Kokoro更重；是否音质更好仍待人工试听，不是默认quality模式。

用独立环境，保留现有Kokoro环境：

```sh
UV_PROJECT_ENVIRONMENT=data/qwen-env uv sync --frozen --extra qwen
```

qwen extra才安装PyTorch和Qwen依赖；普通CLI和Kokoro不需要。精确复现本次研究环境可用[冻结依赖](../scripts/spikes/qwen-macos-requirements.txt)，再在该环境安装本项目。模型约4.52GB，单独保存在被Git忽略的data目录，**generate不会联网下载**。

按[选型记录中的固定下载清单](TTS_PROVIDER_EVALUATION.md)从官方HF仓库准备`data/phase11/model`。必须固定修订`0c0e3051f131929182e2c023b9537f8b1c68adfe`、保留声明Apache-2.0的README及两个safetensors。Adapter在首次使用时校验全部配置/词表/权重SHA，任何缺失、篡改或资产符号链接都会停止；不允许下载自定义Python代码或替换成第三方量化权重。代码LICENSE和模型许可依据见选型记录。

```sh
data/qwen-env/bin/bookcast doctor --config examples/qwen-local.toml
data/qwen-env/bin/bookcast generate examples/content-demo.txt --config examples/qwen-local.toml --mode two_host --minutes 3 --output-dir output/qwen-demo
data/qwen-env/bin/bookcast resume JOB_ID --output-dir output/qwen-demo
```

最后一个命令的output-dir是jobs根目录；用`bookcast jobs --output-dir output/qwen-demo`查询JOB_ID。示例LLM为Mock；真实DeepSeek内容的三方比较复用现有已验证脚本，不重新调用LLM。

`examples/qwen-local.toml`中的local_tts参数：

| 参数 | 约束 |
| --- | --- |
| experimental | 必须显式为true；不宣称已完成人工音质验收 |
| model_dir | 相对配置文件所在目录解析；固定官方快照完整目录 |
| host_voice / guest_voice | Vivian或Uncle_Fu，必须不同；其他声线未开放 |
| style_instruction | 1–256字符，默认自然清晰的播客叙述 |
| threads | 1–8，默认4 |
| seed | 0–2147483647，默认42；不承诺跨设备逐位相同 |

不得配置base_url或api_key_env。每个SpeechUnit仍最多80字符，逐句落盘、审计、恢复；model revision、全资产SHA、运行时版本、voice/style/seed及音频契约影响缓存，安装路径不影响。切换音色只重建受影响的TTS，保留LLM；不同Provider的有效历史产物按D-014保留，要整本换声音用新的输出任务或既有tts_ab工具。

本机模型/内存/依赖失败作为需干预的永久错误，不隐式切换云端或Mock。doctor会给出检查独立环境、固定模型和MPS的指引。崩溃仍用Core resume；本地推理不保证固定延迟，无法完成时可中断再恢复。首次权重校验读取约4.52GB，doctor/新进程启动会有磁盘成本。

Qwen的SoX/FlashAttention提示不代表本次CustomVoice必须安装它们；本次没有使用参考音频路径。真实验收通过macOS系统sandbox-exec禁网并清空继承环境；普通CLI只保证本Adapter不主动请求网络/下载，第三方运行时的系统遥测行为不由Core控制。需要同等隔离时沿用选型记录的禁网启动方式；不要在禁网任务中请求新的云LLM。

## 安装与运行

在仓库根目录执行：

```sh
uv sync --extra dev --extra web --extra tts
.venv/bin/bookcast tts setup --config-output data/tts-local.toml
.venv/bin/bookcast doctor --config data/tts-local.toml
.venv/bin/bookcast generate examples/content-demo.txt --config data/tts-local.toml --mode two_host --minutes 3 --output-dir output/chinese-tts-demo
.venv/bin/bookcast serve --config data/tts-local.toml
```

Web 需要先按 [WEB.md](WEB.md) 构建页面；不使用 Web 时只需 `uv sync --extra tts`。使用 `.venv/bin/bookcast` 避免后续不带 extras 的 `uv run` 自动移除可选运行时。sherpa-onnx 与 sherpa-onnx-core 均锁定 1.13.8；显式 core 依赖修复 macOS 下上游发行元数据漏依赖导致的动态库缺失。

`tts setup` 默认安装到 data/models，并尝试新建 bookcast.toml；使用 `--config-output` 可以保留已有 LLM 配置。文件已存在则停止，不覆盖。手工合并时参考 [examples/tts-local.toml](../examples/tts-local.toml)。示例 LLM 仍是 Mock，真实人声不意味着已经使用真实语言模型理解书籍。付费 LLM/TTS 没有被自动启用，也不作为隐式失败回退。

模型包约 350 MB，安装建议至少留 1 GB 空间。固定官方发布 URL、字节上限、SHA-256，限量展开且拒绝路径穿越、链接、特殊文件与重复成员。保留上游 LICENSE 和逐文件校验收据 bookcast-model.json；不运行包内脚本。已安装完整模型可复用；损坏时报告错误，安装到新的 `--model-dir` 后更新配置，不覆盖原文件。下载安装中断只重试安装，不支持包下载 Range 续传。

网络无法访问 GitHub 时可在有网络的环境取得相同官方包，传入 `bookcast tts setup --archive /path/to/kokoro-multi-lang-v1_0.tar.bz2 --config-output data/tts-local.toml`，仍验证固定校验值。

## 音色与参数

`[[providers]]` 中 `type="kokoro-local"`、`kind="tts"`、`model="kokoro-multi-lang-v1_0"`；禁止为此本地适配器配置端点或密钥。`[providers.local_tts]` 参数：

| 参数 | 默认 | 含义 |
| --- | --- | --- |
| model_dir | 必填 | 已验证模型目录；相对配置文件所在目录解析，任务快照保存绝对路径 |
| host_voice | 45 | 主持人 A 的中文音色 |
| guest_voice | 50 | 嘉宾 B 的中文音色 |
| speed | 1.0 | 0.5–2.0，越大语速越快 |
| threads | 2 | CPU 线程数 1–16 |

中文音色 ID：45 小贝（zf_xiaobei）、46 小妮、47 小小、48 小伊；49 云健、50 云希（zm_yunxi）、51 云夏、52 云扬。两角色可配置为不同音色；目前不提供声音克隆。适配器支持中文及英语混读，日期、数字、电话通过上游规则处理。罕见字、多音字、英文缩写与表情符号的朗读仍需试听；不承诺方言或多语言质量。

## 任务与恢复

Core 按角色发言拆成不超过 80 字符的语音单元，优先在标点/空白处分割；每个单元调用一次中立 `synthesize_unit`，返回 24 kHz 单声道 PCM16 WAV 和音色信息。manifest 中每句都是 Step/Attempt，WAV 与信息均完成落盘及哈希校验后才标记成功。片段内单元间插入 180 ms 间隔，再通过既有 FFmpeg 流程生成 MP3。

```sh
.venv/bin/bookcast status JOB_ID --output-dir output/chinese-tts-demo
.venv/bin/bookcast resume JOB_ID --output-dir output/chinese-tts-demo
.venv/bin/bookcast retry JOB_ID --output-dir output/chinese-tts-demo --config data/tts-local.toml
```

也可以直接传任务目录。恢复读取任务中的配置快照，不需要重复传源文件；配置路径以外的工作目录变化不影响模型定位。崩溃中的单句会重试，已经完成的语音不重新合成。音色、语速、线程、运行时版本、模型/词典内容均进入 Provider 配置摘要，同名 Provider 配置变化时重建其语音缓存，保持分析与脚本缓存。改音色后需显式 `resume --config`；永久错误修复后用 `retry --config`。

**从旧 Mock 换成新 Provider 时，D-014 保留有效的已完成音频。想把整本书换成人声，使用新的输出目录。** 混合历史产物保留各自归属，不根据当前配置猜测旧音频类型。纯 Mock 保持原整段调用方式；逐句链要求全部成员声明 speech_units 能力。Mock 可作为用户明确配置的链成员，但安装配置只包含 Kokoro，不自动降级为音调。

新增产物：audio/units/*.wav 和 *.json（单句）、audio/{segment}.json（汇总）。audio/export.json 记录 audio_kind（speech/mock/mixed/unknown）、实际 WAV 总时长与角色音色；manifest 继续记录模型、Provider、版本、输入/输出哈希与时间，不保存 Secret。CLI/Web 根据这些记录区分人声与音调。目标分钟数控制脚本预算，不保证实际音频等长。

## 错误处理与边界

- 缺少依赖/动态库：重新执行带 `--extra tts` 的 uv sync，再运行 doctor。
- 缺失/损坏模型：检查 model_dir、安装收据及模型；安装到新目录，修正配置后继续。
- 非法参数或音频格式：永久错误，停止；不盲目换 Provider 掩盖问题。
- 可重试错误：沿用 rate limit、quota、timeout、unavailable 策略。运行时本地计算不按 HTTP timeout_seconds 中断；要停止可终止进程，然后恢复未完成单句。
- 已在 macOS Apple Silicon CPU 上验证；其他平台未实机验收。无后台开机自启、无 M4B；CPU 速度取决于硬件与文本长度。doctor 验证安装与加载依赖，不代表已经对所有发音进行质量验收。

## 来源与许可

[Kokoro 模型卡](https://huggingface.co/hexgrad/Kokoro-82M/blob/main/README.md) 标注模型权重为 Apache-2.0；[sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx/blob/master/LICENSE) 主项目使用 Apache-2.0。本适配器采用其[中文/英语多语言发布包](https://k2-fsa.github.io/sherpa/onnx/tts/pretrained_models/kokoro.html)，固定 SHA-256 为 `c5f7e2d2caf082bc1d20fb70334a61d99d20b484500aad32e7cf84c128ea3298`。运行栈包含第三方组件，例如 [eSpeak NG 的 GPLv3 许可](https://github.com/espeak-ng/espeak-ng/blob/master/COPYING)；不能把整个桌面发行包一概标成 Apache-2.0，分发打包前需整理依赖许可及来源。当前模型与运行数据不进 Git，BookCast 自身发行许可证仍待选定。

本地使用没有按字符或分钟收取的 API 费用，算力与电力由自己的电脑承担。Phase 10 增加显式配置的 Gemini 云端 TTS，见下文；Kokoro 仍为免费离线底座。书籍版权及输出转述约束继续适用，模型许可不授予书籍使用权。

## 可选 Gemini 多说话者

`examples/gemini-tts.toml` 是不含密钥的示例，LLM 为 Mock、TTS 为真实云服务。安全注入 `GEMINI_API_KEY` 后运行：

```sh
.venv/bin/bookcast doctor --config examples/gemini-tts.toml
.venv/bin/bookcast generate examples/content-demo.txt --config examples/gemini-tts.toml --minutes 6 --output-dir output/gemini-demo
.venv/bin/bookcast resume JOB_ID --output-dir output/gemini-demo
```

将示例中的 TTS 配置合并到已有 DeepSeek 配置可生成真实内容；不要将 Mock LLM 样例称为真实内容验收。无需 Google SDK/extra，也不会影响未配置 Key 的 Kokoro 安装和生成。`cloud_tts.send_text_to_cloud=true` 必填且仅接受布尔值；`data_tier=free|paid|unknown` 是人工声明，不查询或改变实际账户。双角色映射 `host_voice=Kore`、`guest_voice=Puck`，可更换不同官方 voice；可选 style_instruction 最多512字符。

文本将发送给 Google Developer API。免费/未付费服务通常可能将输入与输出用于产品改进和人工审核；付费服务不用于该产品改进用途，仍有其他保留/安全条款及地区例外。敏感脚本优先选本地 Kokoro。AI Pro/Gemini App 订阅对话额度不是 API 额度；部分 Developer Program 权益需领取并满足地区、账单资格，BookCast 不推断用户拥有 credit。API 免费层是否可用由模型、项目和实际配额决定，不保证任何固定 RPM；Paid 需按官方计费流程启用。

核验日期2026-09-16：当前实验模型 `gemini-3.1-flash-tts-preview`，输入8192 tokens、输出16384 tokens，原生最多两位说话者。Core 使用更小的每段600字符/24发言边界，Adapter 限制完整提示 UTF-8 不超过6000字节；这不是精确 token 计数。官方返回24kHz单声道 PCM s16le，Adapter 严格校验 MIME/长度/完成状态并封装 WAV，Core 校验后拼接 MP3。单请求最长120秒，失败不隐式重试。Preview 名称可配置，API 行为需随官方变化复核。

片段保存于 `audio/segments/`，步骤为 `tts_segment:节目片段ID:序号`。每段成功立即落盘；quota 后恢复仅继续未完成段。完成后的无网络恢复不产生额外 API 调用。同名 Provider 改音色/style/model 仅失效相关 TTS；更换 Provider 名称保留有效历史归属，不清理 LLM 或旧音频。逐句与逐段链不能混用，也不允许云端→Mock 掩盖失败。已完成旧任务保留原产物；未完成跨模式切换需回原链恢复，或另建任务。

429 使用结构化 quota violation 区分按日/额度为零与速率限制；未知429保守记 rate_limit。401或无效Key原因→authentication_error，403→permission_denied（永久），402/明确额度原因→quota_exhausted，408/504→timeout，其他5xx→temporary_unavailable，其余非法请求→input_error，坏JSON/音频契约→schema_error。只保存枚举，不保存响应正文。usageMetadata 的 promptTokenCount/candidatesTokenCount 映射 input/output tokens，未提供值保持null，不估算账单。

官方依据：[模型](https://ai.google.dev/gemini-api/docs/models/gemini-3.1-flash-tts-preview)、[当前 TTS 指南](https://ai.google.dev/gemini-api/docs/speech-generation)、[本 Adapter 使用的 generateContent TTS（Legacy）](https://ai.google.dev/gemini-api/docs/generate-content/speech-generation)、[价格与免费层](https://ai.google.dev/gemini-api/docs/pricing)、[配额](https://ai.google.dev/gemini-api/docs/rate-limits)、[计费](https://ai.google.dev/gemini-api/docs/billing)、[数据条款](https://ai.google.dev/gemini-api/terms)、[API Key](https://ai.google.dev/gemini-api/docs/api-key)、[错误](https://ai.google.dev/gemini-api/docs/troubleshooting)、[Developer Program 权益](https://developers.google.com/program/plans-and-pricing)。同脚本 A/B、实际账户证据及试听限制见 [PHASE10_TTS_AB.md](PHASE10_TTS_AB.md)。

## 本次真实验证

2026-09-15，Apple Silicon / Python 3.12.14 / sherpa-onnx 1.13.8：上面 generate 命令完成自制三章中文书。Job `8b3f9a9a16fb4cfeb0b8efe99205764e`，目录 `output/chinese-tts-demo/5bdad5ca96f5e42cd019ff30/`，51 步、37 次调用，其中 24 次真实 TTS。MP3 3,318,093 字节、24 kHz、单声道、276.429 秒（约 4 分 36 秒），SHA-256 `9a803a13a0977f5a403d69242163383b715c9b5064df8d693e09eb149183f2ae`。

执行真实 CLI resume 后，83 个产物/状态文件的字节与 mtime 均不变、没有新增调用，约 0.73 秒完成缓存检查。模型加载、非空音频、双音色映射、FFprobe 格式/时长均已验证；口音、自然度与长文本听感仍需人工试听。Mock 内容报告 needs_review，并警告脚本长度偏离预算，不能把该样例当作真实 LLM 内容质量验收。自动故障测试用合成 WAV 隔离昂贵模型，真实 SIGKILL 覆盖调用中和完成 Attempt/Step 提交间隙；记录见 tests/test_tts.py。
