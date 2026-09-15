# 本地中文语音

BookCast 的真实 TTS 首选 Kokoro 多语言模型，经 sherpa-onnx 在本机 CPU 上合成。无需 API Key、付费服务或 GPU；首次安装下载模型，之后生成不联网。默认未配置环境仍运行 Mock 测试音调，核心 CLI 不依赖 TTS extra。

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

本地使用没有按字符或分钟收取的 API 费用，算力与电力由自己的电脑承担。付费 TTS 以后可以按相同 Provider 契约增加，只有用户明确配置才启用；本阶段未实现付费适配器。书籍版权及输出转述约束继续适用，模型许可不授予书籍使用权。

## 本次真实验证

2026-09-15，Apple Silicon / Python 3.12.14 / sherpa-onnx 1.13.8：上面 generate 命令完成自制三章中文书。Job `8b3f9a9a16fb4cfeb0b8efe99205764e`，目录 `output/chinese-tts-demo/5bdad5ca96f5e42cd019ff30/`，51 步、37 次调用，其中 24 次真实 TTS。MP3 3,318,093 字节、24 kHz、单声道、276.429 秒（约 4 分 36 秒），SHA-256 `9a803a13a0977f5a403d69242163383b715c9b5064df8d693e09eb149183f2ae`。

执行真实 CLI resume 后，83 个产物/状态文件的字节与 mtime 均不变、没有新增调用，约 0.73 秒完成缓存检查。模型加载、非空音频、双音色映射、FFprobe 格式/时长均已验证；口音、自然度与长文本听感仍需人工试听。Mock 内容报告 needs_review，并警告脚本长度偏离预算，不能把该样例当作真实 LLM 内容质量验收。自动故障测试用合成 WAV 隔离昂贵模型，真实 SIGKILL 覆盖调用中和完成 Attempt/Step 提交间隙；记录见 tests/test_tts.py。
