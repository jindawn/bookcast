# Phase 9 真实 LLM 验收记录

日期：2026-09-16（Asia/Shanghai）。**真实 DeepSeek → 分层内容 → Kokoro → MP3 技术链路通过；内容仍为 needs_review，人工试听未完成。** 本记录包含 Coding Agent 对脚本的逐段审读，不冒充真人听感评价或独立事实核查。

## 官方协议与接入

编码前核对官方 [V4.1-Flash 发布说明](https://www.deepseek.com/en/news/deepseek-v4-1-flash/)、[thinking 指南](https://api-docs.deepseek.com/guides/thinking_mode/)、[Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)、[错误码](https://api-docs.deepseek.com/quick_start/error_codes/) 和 [缓存说明](https://api-docs.deepseek.com/guides/kv_cache/)（2026-09-15）。模型 `deepseek-flash`，端点 `https://api.deepseek.com`；官方当时将该名称映射到 V4.1-Flash，实际响应 reported_model 仍为 deepseek-flash，没有额外版本号可独立核验。

复用 CompatibleLLMProvider、Chat Completions JSON mode 和本地 Pydantic schema，不增加 SDK 或厂商专属 Pipeline。thinking enabled/disabled、reasoning_effort low/high/max、max_tokens 为强类型有界配置；不接受任意 JSON 或 headers。Provider 默认和任务策略可显式覆盖；实际参数进入 Attempt 和缓存摘要。默认 pytest 离线，不调用收费 API。

用户授权的凭证已复制到 Git 忽略的数据目录，通过进程环境 `DEEPSEEK_API_KEY` 加载。此前 Downloads 文件访问阻塞已解除；健康检查 DeepSeek/Kokoro 均可用。密钥不在参数、TOML、日志或 manifest 中，受检源码和 Phase 9 文本产物未发现密钥字面值；未记录原始服务错误正文。

## 正式样例

- 输入：仓库自制 `examples/content-demo.txt`，三章关于分工收益、协作代价、调整空间的短文。非外部版权书籍；不能据此推断长书质量。
- 源 SHA-256：`4db2ed61acd27f17a47becf9df81483a40567ef81c260c3e58795509f9b02bfa`。
- Job ID：`7229d03765db4d1c860c7bd18d62b178`；book ID：`5bdad5ca96f5e42cd019ff30`。
- 目录：`output/phase9-deepseek/5bdad5ca96f5e42cd019ff30/`；主要产物为 `manifest.json`、`analysis/`、`synthesis/`、`plans/episode.json`、`scripts/`、`evaluation/quality.json`、`podcast.mp3`。本机数据被 Git 忽略，其他克隆需自行生成。
- LLM：deepseek-flash，13次调用（3分析、3章节综合、1全书综合、3脚本、3复核），正式样例全部首次成功，无 Mock、无隐藏重试。Planner 为现有本地确定性计算。
- TTS：Kokoro CPU，中文音色45/50。首版 speed=1.0 的 MP3 为251.343292秒（4分11秒），未达5分钟；只将同名TTS改为0.8后经现有Core重新合成，禁止网络请求，13次LLM记录完全不变。没有用额外收费生成补时长。

最终音频与恢复的精确数据见下方最终核验表。

## 文本审读与自动指标

三章的主要条件和限制均进入脚本：分工能降低切换成本但不是越细越好；可靠交接决定局部熟练能否形成整体收益；分工需随反馈和需求变化调整。未观察到捏造人物、历史或数字；仍有两处来源归属问题，不能称为所有事实均已验证。

Host B 提问沟通成本是否被转嫁、紧急创意讨论是否适合拆工序，并提出明确标为假设的产品经理/开发交接例子；Host A 回应交接条件和返工代价。第三段质疑频繁调整会否破坏稳定，A 回应反馈和边界。两人形成问答，未仅轮流念摘要。开头和过渡仍使用“第一/二/三章”的机械表述，且输入很短，部分内容是主持人延展讨论。

质量报告：覆盖3/3章，1182字符，目标1440字符，Host B占44.33%，重复20字符窗口占比0%，无命中固定空泛词表，无80字符连续原文复述，is_mock=false。自动指标只表示这些检查通过，不等于自然、准确或版权合规的保证。

逐段复核保留两项 unverifiable，均在片段0001：

1. turn_index=4：“判断标准是分工是否真的减少切换与沟通”是合理推论，但原文没有明确给出该判断标准，应归为讨论。
2. turn_index=6：“边界不清，重复解释又会回来”是从正面例子延展的推论，不应完整归为原文观点。

报告为 `needs_review`，无阻断项，按既有警告策略允许TTS。未关闭质量门禁或把推论改成伪造证据。需要对外发布时先审读并使用已有片段修订入口，再检查受影响脚本和音频。尚未真人试听，发音、停顿和低语速自然度没有结论。

## 推理策略的实际观察

| Core 任务 | 本次配置 | 观察与局限 |
| --- | --- | --- |
| 分块事实/九类分析 | thinking disabled | 改为选择证据ID后正式3次成功；其它独立运行曾有schema失败 |
| 章节综合 | enabled/low | 保留章节条件和限制；没有外部事实核查 |
| 全书综合 | enabled/high | 形成跨章收益、代价、动态调整关系；未与low对照 |
| Episode Planner | 无AI请求 | 仍使用既有预算/覆盖/去重规则 |
| 对话 | enabled/low | 形成追问与反例，但有两处推论被归为source |
| 一致性复核 | enabled/low | 指出上述两处归属问题；其余supported并非独立保证 |

所有请求 max_tokens=16384。本样本支持保留有任务差异的 `bookcast-v1` 作为可覆盖的起点；未证明最优强度，也未为追求通过率无差别提高推理。精确偏移问题通过契约职责修复：Core预切证据，模型选择ID，Core查表还原既有RichAnalysis；错误ID永久失败。分析提示升为 `content-analysis-v3`，其余 content-v1，见D-018。升级分析提示会使旧分层分析失效，不能冒称升级前后绝不重算。

## 失败记录与错误协议边界

| 情况 | 实际观察或验证方式 |
| --- | --- |
| 无效 API Key | 实际联网 GET /models 返回401，映射authentication_error、不可重试 |
| 正确Key | 实际health check和收费structured generation成功 |
| 原引文字符偏移 | 第一个测试任务累计4次business_error；正确引文配错偏移，提供预计算偏移仍不稳定；均保留失败Attempt，没有切Provider |
| 新证据ID契约的schema失败 | 第二个独立测试首个分析曾schema_error；未持久化原始响应，无法断言具体坏字段；人工显式诊断重试成功，历史未清除 |
| HTTP402/429、额度、timeout、5xx、400/422 | 官方协议与离线transport fixtures验证分类和接管；没有实际耗尽余额或制造服务故障 |
| malformed JSON / schema mismatch / length截断 | 离线覆盖永久schema_error；真实运行也观察到schema_error，但不据此推断为哪一种具体格式问题 |
| quota接管与进程中断 | 离线实际兼容适配器路径和SIGKILL测试，不将其报告为真实DeepSeek发生过额度切换 |

第二个测试人工恢复完成后，显式 `BOOKCAST_LIVE_OUTPUT` 指向该缓存任务运行联网测试：1 passed。它验证真实分析/综合/脚本与禁网resume，并未重新收费生成。测试语音是Mock音调，正式样例才是Kokoro人声。新目录的真实JSON稳定性仍不能保证100%。

## Provider 报告用量

以下是本次所有三个任务manifest中48次LLM尝试的服务端计数，包含失败和因提示升级失效的历史调用；不是本地token估算。

| 任务 | LLM尝试（成功/失败） | input tokens | output tokens | cache hit tokens | 已报告reasoning tokens / 缺失次数 |
| --- | --- | ---: | ---: | ---: | --- |
| 测试01：995b0fc256c74940bc64ef9388fc116b | 21（17/4） | 22,919 | 32,149 | 5,760 | 21,735 / 9 |
| 测试02：13a94aa86e2048e0873162ba36b08d76 | 14（13/1） | 16,944 | 22,803 | 6,653 | 17,823 / 4 |
| 正式音频：7229d03765db4d1c860c7bd18d62b178 | 13（13/0） | 16,422 | 24,445 | 5,501 | 19,839 / 3 |
| 合计 | 48（43/5） | 56,285 | 79,397 | 17,914 | 59,397 / 16 |

reasoning字段缺失仍为null，不当作0；表内仅求已报告部分之和。reasoning通常包含在output中，cache hit包含在input中，不重复相加。本阶段没有估算价格，不能据此声明实际账单金额。恢复和调整本地语速没有新增LLM用量。

## 复现与测试

新机器先按[TTS指南](TTS.md)安装；本机不要重复下载模型。将API Key安全注入环境，不能写在TOML/命令参数里：

```sh
.venv/bin/bookcast config providers --config examples/deepseek-kokoro.toml
.venv/bin/bookcast doctor --config examples/deepseek-kokoro.toml
# 有Key且显式开关才调用收费API；新测试默认使用临时目录。
BOOKCAST_RUN_LIVE_LLM=1 .venv/bin/pytest tests/test_live_deepseek.py -q
.venv/bin/bookcast generate examples/content-demo.txt --config examples/deepseek-kokoro.toml --provider deepseek --mode two_host --minutes 6 --output-dir output/phase9-deepseek
.venv/bin/bookcast resume output/phase9-deepseek/5bdad5ca96f5e42cd019ff30
```

正式样例的0.8语速配置位于本机忽略的`data/phase9-demo.toml`，与示例仅speed不同。首次从1.0改为0.8使用`resume JOB --config data/phase9-demo.toml`；以后普通resume沿用保存的快照。模型本身有随机性，以上命令不保证相同文本、时长或用量。已完成测试可用`BOOKCAST_LIVE_OUTPUT=测试输出根目录`复验缓存；永久失败修复后必须显式retry，不自动隐藏失败。

已完成任务在禁用HTTP和TTS推理下resume；全目录（排除.lock）文件集合、SHA与mtime一致、Attempt数不变。对临时副本改全局thinking=disabled时，已disabled的分析命中，首个章节综合需要重做；在发送请求前以故障注入停止，原任务不变。这是D-014的真实产物缓存验证，不是再次付费比较生成质量。远端已处理但本地尚未记录成功的崩溃窗口仍可能重复计费。

## 最终核验数据

| 项目 | 结果 |
| --- | --- |
| 最终MP3 | 320.283秒（5分20秒），3,844,557字节，24kHz单声道 |
| MP3 SHA-256 | dc00547eb4a5a0e57fd02924a0a12463cef3d594a63cdc64a697240be83c0b93 |
| 音频检查 | FFmpeg完整解码成功、非静音；不等同人工试听 |
| Job | completed，50个步骤；13次LLM和46次本地TTS历史调用（23单元×两种语速） |
| 完成后resume | HTTP与TTS调用被禁止；81个文件集合/SHA/mtime不变，调用59→59 |
| 配置变更 | 临时副本全局disabled：分析命中，首个章节综合失效；HTTP前注入停止，原任务不变 |
| 专项测试 | 142 passed |
| 完整离线测试 | 318 passed、10 subtests passed、1收费联网测试默认跳过；7个既有警告 |
| 显式真实任务测试 | 已恢复任务复验1 passed；没有新增收费调用 |
| 静态检查 | project validator、compileall、git diff --check通过 |

不重跑浏览器E2E：本轮未修改Web界面/API/构建配置，完整Python套件覆盖原有Web后端。最新提交上的验证和交接状态见STATE/HANDOFF。
