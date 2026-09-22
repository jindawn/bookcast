# Phase 11 本地中文 TTS 选型

研究日期：2026-09-17（北京时间）。目标：在 Apple M2 Pro / 32 GiB、10核、macOS 26.5.1 / arm64 上选择一个可稳定运行的中文候选；研究范围只有 Qwen3-TTS 与 CosyVoice，Kokoro/Gemini 作为已有对照。可用磁盘约189 GiB。

流程：官方版本及许可证→源码/依赖核验→单候选真实 spike→Go/No-Go→最多一个 Adapter。已完成Qwen五类文本的FP32及BF16真实实验，选择BF16/SDPA接入唯一的实验Provider；尚未推荐为quality默认。

FACT为直接核实事实，PROJECT CLAIM为上游声明，INFERENCE为推断，UNKNOWN为未核实；最终推荐不能由宣传指标或音频格式检查代替人工试听。

接手基线：639e1bc4fc600ba1b7d385a60f1cac321b5ed02a与origin/main一致；85项TTS/恢复测试通过（15.64秒）。现有Phase 10同脚本音频会复用，不重新调用DeepSeek/Gemini。

## 官方版本与候选对照

查阅 [Qwen 官方仓库](https://github.com/QwenLM/Qwen3-TTS)、[releases](https://github.com/QwenLM/Qwen3-TTS/releases)、[PyPI](https://pypi.org/project/qwen-tts/0.1.1/)；CosyVoice 原 FunAudioLLM 地址已跳转到 [QwenAudio 官方仓库](https://github.com/QwenAudio/CosyVoice)，其 [releases](https://github.com/QwenAudio/CosyVoice/releases) 同样没有正式 GitHub Release。不能把 main 或“最新模型”称为语义化版本的稳定软件发布。

| 项目 | Qwen3-TTS | CosyVoice |
| --- | --- | --- |
| 固定源码快照（FACT） | 022e286b98fbec7e1e916cb940cdf532cd9f488e，2026-03-17；实验用正式 PyPI qwen-tts 0.1.1 | 074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc，2026-05-25 |
| 当前发布模型（FACT） | Qwen3-TTS-12Hz-1.7B-CustomVoice，2026-01 发布；另有0.6B、Base、VoiceDesign | 官方推荐 Fun-CosyVoice3-0.5B-2512（2025-12），含基础/RL权重 |
| 源码 License（FACT） | Apache-2.0，官方 LICENSE | Apache-2.0，官方 LICENSE |
| 模型权重许可（FACT） | 官方 HF model card 标注 apache-2.0；该快照没有独立 LICENSE 文件 | 官方 HF model card 标注 apache-2.0；该快照没有独立 LICENSE 文件 |
| Python（FACT） | 包声明 >=3.9；官方安装推荐3.12；本次3.12.14 | 官方推荐3.10；可选 ttsfrd wheel 固定 cp310/Linux；未验证3.12 |
| Apple Silicon（FACT/UNKNOWN） | Python API 接受 device_map；未发现正式原生Mac验收承诺；需本机实测 | 依赖包含 Darwin ONNX Runtime，但不等于完整Mac验收 |
| CPU（FACT/UNKNOWN） | 模型可指定CPU；真实成本待测 | 源码有CPU分支；本机速度未测 |
| MPS（FACT） | 上游设备解析增强 PR #345 仍未合并；不能据此认定支持或完全不支持 | 上游 MPS PR #1869 仍未合并；当前模型明确选择 CUDA 或 CPU |
| 强依赖 CUDA？（FACT） | 核心可选择其他设备；FlashAttention2 为可选CUDA优化，本次禁用 | 基础CPU分支存在；vLLM/TensorRT/DeepSpeed优化依赖CUDA/Linux，不能移植成Mac默认 |
| RAM/VRAM（UNKNOWN） | 官方没有适用于此Mac的最低/峰值内存保证；以实验测量为准 | 没有适用于此Mac的保证；0.5B不是整套模型内存 |
| 模型下载体积（FACT） | 固定快照4,520,218,951字节（约4.52 GB）；主权重3,833,402,552字节、codec682,293,092字节 | 完整仓库9,747,516,745字节（约9.75 GB），包含互为替代的RL/基础/ONNX；不等于最小必需下载量 |
| 首次加载（FACT/UNKNOWN） | 本机FP32 11.39秒、BF16 2.85秒；独立进程，文件缓存已热，不是冷磁盘结果 | 未实验，不能比较 |
| 中文质量（PROJECT CLAIM） | 官方宣称高质量多语言和表现力；非本项目听感结论 | 官方宣称中文方言、鲁棒性/自然度改进；非本项目听感结论 |
| 多 speaker（FACT） | CustomVoice 9个预设音色；两角色分别调用，并非原生双人对话上下文模型 | zero-shot参考音色，可为不同角色提供参考；不是已验证的Gemini式多角色接口 |
| style control（FACT） | 1.7B CustomVoice 接受自然语言 instruct；不能推断所有0.6B功能相同 | instruct2 / 自然语言控制；本次未测 |
| voice cloning（FACT） | Base变体支持；所选CustomVoice不克隆，不下载Base | zero-shot参考音频及文本；用户须拥有参考声音使用权 |
| streaming（PROJECT CLAIM/FACT） | 上游报告流式能力；本次公开generate_custom_voice包装器返回整段wave，不把 non_streaming_mode=False 视为已验证流式接口 | 官方支持双流模式，延迟宣传不是本机测量 |
| 商业使用限制 | Apache-2.0允许商业使用，须遵守许可/声明/专利条件；不授予任意人声音、作品或商标权 | 同左；上游示例免责声明不能替代模型卡许可，也不替用户解决声音授权 |
| 安装复杂度（INFERENCE） | 中等：PyTorch/Transformers/codec；官方PyPI包带Gradio等非Core必需依赖，隔离安装 | 高：递归子模块、固定旧版Torch/音频依赖、可选Linux wheel、多个模型文件；禁止直接混入Core环境 |

来源：Qwen [模型卡](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice)、[依赖声明](https://github.com/QwenLM/Qwen3-TTS/blob/022e286b98fbec7e1e916cb940cdf532cd9f488e/pyproject.toml)、[加载源码](https://github.com/QwenLM/Qwen3-TTS/blob/022e286b98fbec7e1e916cb940cdf532cd9f488e/qwen_tts/core/models/modeling_qwen3_tts.py)、[MPS PR #345](https://github.com/QwenLM/Qwen3-TTS/pull/345)；CosyVoice [模型卡](https://huggingface.co/FunAudioLLM/Fun-CosyVoice3-0.5B-2512)、[依赖声明](https://github.com/QwenAudio/CosyVoice/blob/074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc/requirements.txt)、[设备选择源码](https://github.com/QwenAudio/CosyVoice/blob/074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc/cosyvoice/cli/model.py)、[MPS PR #1869](https://github.com/QwenAudio/CosyVoice/pull/1869)。许可证分别见 [Qwen LICENSE](https://github.com/QwenLM/Qwen3-TTS/blob/022e286b98fbec7e1e916cb940cdf532cd9f488e/LICENSE) / [CosyVoice LICENSE](https://github.com/QwenAudio/CosyVoice/blob/074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc/LICENSE)。

## 单候选实验设计

选择 Qwen 1.7B CustomVoice：官方Python包、两个现成中文音色、明确style接口，比CosyVoice的参考音频/子模块/设备改造更适合一次有界实验。1.7B权重体积在32 GiB机器可尝试范围内，但文件大小不证明运行时内存或速度。未安装CosyVoice、未下载其权重，也未使用第三方Mac fork、MLX转换权重或未合并PR。

实验前约束：单次加载/合成240秒上限；MPS分配比例上限0.5（相对于PyTorch推荐工作集，不是物理内存的一半）；目标峰值RSS不超过16 GiB且无OOM/设备错误；初步实用速度目标 RTF≤3（合成秒数/音频秒数，非科学通用阈值）。同时记录MPS allocator/driver字节数；统一内存下它们与RSS有重叠，不能简单相加。必须先通过五类文本，才考虑Core Adapter及完整同脚本比较。仅格式合格不能进入quality推荐，还需要实际试听优于Kokoro。

固定实验：Python3.12.14、qwen-tts0.1.1、torch/torchaudio2.11.0、transformers4.57.3、accelerate1.12.0；通过PyPI wheel安装，禁止源码构建，隔离在被Git忽略的data/phase11/qwen-env。最初2.8.0仅用于import/MPS预检；正式模型实验前核对了当前官方PyPI（torch最新2.14.0，torchaudio最新2.11.0），采用具有cp312/macOS-arm64 wheel的匹配2.11.0组合，不把它称为最新版torch。来源：[torch 2.11.0](https://pypi.org/project/torch/2.11.0/)、[torchaudio 2.11.0](https://pypi.org/project/torchaudio/2.11.0/)。

设备检查：受限执行沙箱内MPS不可见；在真实宿主执行同一环境检查后，`torch.backends.mps.is_available()`为True，实际MPS张量运算成功。这是执行环境限制，不记为模型失败。

权重仅从官方HF固定修订版下载：`0c0e3051f131929182e2c023b9537f8b1c68adfe`。CosyVoice仅查元数据修订版`29e01c4e8d000f4bcd70751be16fa94bf3d85a18`。两个Qwen safetensors必须匹配官方LFS SHA-256：

```text
38b1d5971bdbd982b561cccec982669a53b0537c3cf5e9bd4778ed07bb2f5137  model.safetensors
836b7b357f5ea43e889936a3709af68dfe3751881acefe4ecf0dbd30ba571258  speech_tokenizer/model.safetensors
```

下载只接受固定文件名和HTTPS重定向；没有下载或执行模型仓库Python代码，没有trust_remote_code。模型卡、代码LICENSE、官方API元数据和依赖freeze留在data/phase11/research；配置和词表使用相同固定修订版。离线实验清空继承环境、HF缓存隔离，不传API Key。脚本 [qwen_native.py](../scripts/spikes/qwen_native.py) 只作实验记录，不是第二套Pipeline，也不伪造Job/Step/Attempt成功。

复现环境使用 [冻结依赖](../scripts/spikes/qwen-macos-requirements.txt)，不运行普通 `uv sync` 去污染已有环境：

```sh
uv venv --python .venv/bin/python data/phase11/qwen-env
uv pip install --python data/phase11/qwen-env/bin/python --only-binary :all: -r scripts/spikes/qwen-macos-requirements.txt
```

官方模型地址格式为 `https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice/resolve/0c0e3051f131929182e2c023b9537f8b1c68adfe/<文件>`；固定取 config.json、generation_config.json、merges.txt、preprocessor_config.json、tokenizer_config.json、vocab.json、README.md、model.safetensors，及speech_tokenizer下的config.json、configuration.json、preprocessor_config.json、model.safetensors。完整快照元数据含`.gitattributes`，运行时不需要它。保存到data/phase11/model，对照上文SHA后才运行。

执行示例（macOS；目标目录必须不存在；需要宿主MPS访问权限）：

```sh
/usr/bin/sandbox-exec -p '(version 1) (allow default) (deny network*)' \
  /usr/bin/env -i PATH=/usr/bin:/bin HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  HF_HOME="$PWD/data/phase11/hf" \
  data/phase11/qwen-env/bin/python scripts/spikes/qwen_native.py \
  data/phase11/model output/phase11-qwen-mps --device mps --dtype float32
```

不启用隐藏CPU fallback。首次import提示未安装FlashAttention和SoX；前者为有意禁用的CUDA优化，后者未被CustomVoice无参考音频路径调用。不要因此把未经执行的voice cloning标为通过。

## 实测及决定

权重下载完成，两项SHA-256全部匹配。FP32、BF16五项均已生成。决策为：**Go for experimental native MPS adapter；No-Go for default quality recommendation until listening acceptance**。旧任务、Kokoro/Gemini Adapter、Core持久格式未改；Registry仅增加qwen-local注册。

FP32/eager 基线已测：依赖import13.10秒，模型加载11.39秒；这不是首次下载安装总耗时，且读过文件做SHA校验，不能称为冷磁盘加载。加载后RSS峰值4,376,395,776字节，MPS allocator8,348,960,768字节；两者不能相加。模型声明的9个声线可枚举。

| FP32/eager case | 音频秒数 | 合成秒数 | RTF | 结果 |
| --- | ---: | ---: | ---: | --- |
| 中文一句 / Vivian | 7.12 | 41.45 | 5.82 | 24kHz/单声道/PCM16已生成 |
| 第二声线 / Uncle_Fu | 10.72 | 44.22 | 4.13 | 同上；两声线主观区分度尚无反馈 |
| 207字长段 | 47.04 | 202.61 | 4.31 | 完成，未触发240秒超时或OOM |
| 中英夹杂 | 11.60 | 46.82 | 4.04 | 完成，尚未逐字听校英文缩写 |
| 数字日期 | 14.88 | 57.52 | 3.87 | 完成，尚未逐字听校读法 |

FP32五项合计91.36秒音频、392.63秒合成（不含import/加载），加权RTF约4.30；全部WAV通过FFmpeg完整解码。所记录MPS driver最大13,227,769,856字节，allocator约8.35GB，RSS峰值约4.38GB；这些是不同口径，driver采样值也不是持续监控的峰值。

FP32速度超出预设目标；同一模型改为BF16及PyTorch SDPA后完成下表，未下载第二候选或修改上游实现。复现使用上面的命令改为新输出目录`output/phase11-qwen-bf16-sdpa`及`--dtype bfloat16 --attention sdpa`。

| BF16/SDPA case | 音频秒数 | 合成秒数 | RTF |
| --- | ---: | ---: | ---: |
| 中文一句 | 6.00 | 19.81 | 3.30 |
| 第二声线 | 4.40 | 10.63 | 2.42 |
| 207字长段 | 47.84 | 138.23 | 2.89 |
| 中英夹杂 | 12.24 | 33.91 | 2.77 |
| 数字日期 | 13.04 | 34.78 | 2.67 |

合计83.52秒音频/237.36秒合成，加权RTF2.84；首句仍略高于3，后续四项低于3。该目标作为整组持续生成的成本参考，首句额外开销如实保留。加载2.85秒、RSS峰值2,970,648,576字节、MPS allocator约4.17GB、driver最大采样10,292,985,856字节。全部音频为可解码的24kHz单声道PCM16，无超时/OOM。实验过程中存在轻量本地检查，两个配置运行时间不同，不作为严格性能benchmark。两份短句已提供用户试听，截至目前没有主观评分反馈，不能从采样长度变化推断发音优劣。

## 接入决定与范围

在已测M2 Pro/32 GiB上，BF16组的资源与持续生成速度足以进行可选实验接入；不声称官方全面支持macOS，也不推广到8/16 GiB机器、CPU或Windows/Linux。优于Kokoro的听感尚未证明，故必须显式设置`experimental=true`，不切换默认、不增加quality预设，不自动从Kokoro失败回退。

唯一新增`qwen-local` Adapter复用现有SpeechUnit（最多80字符），每句同样进入Core Step/Attempt/Artifact与D-014缓存。固定官方1.7B CustomVoice、MPS/BF16/SDPA；两个已测中文voice、有限style/seed/threads配置；依赖仅在显式qwen extra安装。缓存包含模型修订、全部资产SHA、运行时版本、音色/style/seed与音频契约。generate不下载、不允许端点或Key，不接受自定义模型Python代码。配置见[示例](../examples/qwen-local.toml)，运行见[TTS指南](TTS.md)。

不引入第二套Pipeline或外部HTTP服务。未来若原生路径在其他机器不合格，可单独评估本地HTTP隔离运行时或明确授权的GPU服务器；后者仍需云端隐私声明，不是当前已实现功能。CosyVoice本次仅研究，未用其未合并MPS PR做生产依赖。

## 同脚本三方对照

沿用Phase 10三份脚本JSON，Qwen通过现有scripts/tts_ab.py和Core重新合成独立目录；拒绝新LLM调用，系统层禁网络。Kokoro/Gemini有效历史产物直接保留。

| 项目 | Kokoro | Gemini | Qwen |
| --- | --- | --- | --- |
| 音色/模式 | 45/50，逐句 | Kore/Puck，多角色片段 | Vivian/Uncle_Fu，逐句 |
| 产物 | output/phase10-kokoro/podcast.mp3 | output/phase10-gemini/podcast.mp3 | output/phase11-qwen-ab/podcast.mp3 |
| 实际时长 | 320.267208秒 | 251.440秒 | 385.040秒 |
| 大小 | 3,844,269字节 | 3,018,285字节 | 4,621,581字节 |
| 完成TTS单元/片段 | 23单元 | 3片段 | 23单元，另有1次受控SIGKILL中断 |
| 自然度、停顿、多音字、对话感 | 待人工评分 | 待人工评分 | 待人工评分 |
| 英文缩写、数字日期、漏字/重复 | 待逐字听校 | 待逐字听校 | 五类spike已生成，仍待逐字听校 |
| 主观优于Kokoro？ | 基准，未评分 | 未判定 | 未判定，不推荐默认quality |

Qwen Job为`d2bfc59060ff403fb276e805eeccf663`，MP3 SHA-256为`2af78ad6733a03d328da91b7f3a7163b552d16002ddb20fc8e78daa41c2a383c`；24kHz单声道，FFmpeg完整解码通过。创建至完成935.62秒（15分35.62秒），包括受控中断、恢复及本地验证工作，不能等同纯推理耗时。三方脚本JSON SHA完全一致，Qwen沿用13条LLM审计记录且内容逐项不变，新增LLM请求为0。音频变长不能证明朗读更完整，必须听校。

Qwen真实任务在第7单元RUNNING时SIGKILL，退出137；此前6个单元已落盘。通过同一Core恢复，第7旧Attempt转failed_retryable并仅为第7启动新Attempt；前6份WAV/sidecar的SHA、mtime、大小均未变。完成后显式禁止模型加载、TTS推理、LLM和HTTP，再resume：84文件的SHA/mtime均不变，实际测试1 passed（2.69秒）。其中包含SIGKILL遗留的1个0字节临时文件，未作为产物引用，不影响恢复，本阶段未改Core清理逻辑。旧Kokoro83文件/Gemini43文件也在禁止推理的恢复复验中保持不变。

人工试听尚无反馈；三方主观评分与逐字听校未完成，保持实验Provider，不宣布quality推荐。自动指标不等于人工音质评价。

## 自动测试与复验

Qwen/实验脚本专项13 passed，实际模型测试默认跳过；旧TTS/Provider专项121 passed。完整离线380 passed、10子测试、3项显式启用测试默认跳过（LLM、Gemini和本地Qwen），无模型下载或GPU要求。配置包含可选extra后，默认dev/web/tts安装dry-run只需刷新本项目，不安装Torch；锁文件已有依赖版本未改变。Project validator、compileall、diff检查通过。

真实完成任务仅作读取/恢复验收，不会再次合成：

```sh
BOOKCAST_VERIFY_LOCAL_QWEN=1 BOOKCAST_QWEN_OUTPUT=output/phase11-qwen-ab \
  data/phase11/qwen-env/bin/python -m pytest tests/test_live_qwen.py -q
```

运行需要上述隔离运行时和已完成真实产物；测试强制禁止模型加载、合成及HTTP，缓存失效会失败而不是消耗算力重做。普通CI不依赖这些文件或联网服务。
