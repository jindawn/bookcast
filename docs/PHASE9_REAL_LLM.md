# Phase 9 真实 LLM 验收记录

日期：2026-09-15。状态：**离线实现已完成，真实生成验收待凭证，Phase 9 尚未验收完成。**

## 官方协议核验

编码前核对官方 [V4.1-Flash 发布说明](https://www.deepseek.com/en/news/deepseek-v4-1-flash/)、[thinking 指南](https://api-docs.deepseek.com/guides/thinking_mode/)、[Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)、[错误码](https://api-docs.deepseek.com/quick_start/error_codes/) 和 [缓存说明](https://api-docs.deepseek.com/guides/kv_cache/)。截至核验时：

- `https://api.deepseek.com`，模型 `deepseek-flash` 指向 DeepSeek-V4.1-Flash；新配置不用旧别名。
- `thinking.type` 为 enabled/disabled；`reasoning_effort` 使用 low/high/max。未指定时服务默认为 thinking enabled/high；BookCast 不依赖这个隐式默认。
- 继续使用 Chat Completions 的 JSON mode 和本地 schema 校验；不新增 DeepSeek Adapter、SDK 或 Responses API。
- usage 映射见 PROVIDERS；未返回的计数为空，没有成本估算或固定价格表。

## 本次实际观察与证据边界

功能提交 ff3c48ac960e8435889dcc303665626e4438f186 已验证：313 passed、10 subtests passed、1联网测试跳过；project validator、compileall、提交差异检查通过。新51项离线测试不等同于51次真实模型调用。

用户已提供并授权读取本机Downloads中的凭证文件，但普通/提权执行均返回PermissionError（Operation not permitted），尚未读到内容或设置DEEPSEEK_API_KEY。已请用户复制到Git忽略的data/deepseek-key.txt，之后只加载为环境变量。没有读取其他应用凭证、没有将key打印或提交Git。

| 项目 | 结果与证据性质 |
| --- | --- |
| 官方 API 无效凭证 | 实际联网 `GET /models` 使用固定无效测试值，HTTP 401，适配器返回 authentication_error、不可重试；未保存原始错误正文 |
| HTTP 402、429、408/504、5xx、400/422 | 官方协议 + 离线 transport fixtures 验证；未声称实际制造余额耗尽、限流或服务故障 |
| malformed JSON / schema mismatch / length 截断 | 离线验证为永久 schema_error，不通过切换掩盖错误；响应已含 usage 时仍记录用量 |
| typed thinking / effort / max_tokens | 严格枚举、类型、范围和额外字段拒绝测试通过 |
| 最小任务缓存 | 离线真实适配器传输路径用 Mock 内容 fixtures 验证：完整 resume 不新增网络调用且文件字节/mtime 不变；配置改变仅失效实际参数改变的 AI 任务 |
| 402 接管与中断恢复 | 离线实际适配器路径验证第二章失败后由 B 接管；已完成第一章不重做；完成 Attempt/Step 间隙中断后复用已落盘结果 |
| 真实 structured generation | **未执行：缺少有效 API Key** |
| DeepSeek + Kokoro 5–10分钟样例 | **未执行：依赖真实内容生成**。Phase 8 已有 Kokoro 音频的脚本来自 Mock，不能替代本项 |
| provider_reported_usage | 协议与 fixtures 已验证；暂无有效生成响应，不能报告真实 token 数或费用 |
| 人工文本/听感质量结论 | **尚无**；自动指标不等同于优秀播客质量 |

## 任务推理策略与待验证假设

策略集中在 `generation.py`，示例显式选择 `bookcast-v1`。当前是待真实质量实验验证的候选方案，不宣称已选出最优强度。

| 实际 Core 任务 | 候选设置 | 需要观察的质量问题 |
| --- | --- | --- |
| analysis:章:块（九类事实抽取/结构化分析） | disabled | 引文与精确字符偏移、漏项和无依据扩写 |
| synthesis/chapters/... | enabled/low | 条件和反例是否保留 |
| synthesis/book/... | enabled/high | 跨章关系及限制是否保留，额外推理耗时/usage 是否合理 |
| 本地 Episode Planner | 无 AI 请求 | 继续检查确定性预算、覆盖、去重；不为推理策略新增模型调用 |
| script:片段 | enabled/low | A 回应 B 的追问、假设明确、篇幅和衔接 |
| consistency:片段 | enabled/low | 否定关系、数字、来源归属，以及误报 |

首次实测如暴露格式、字符定位或语义问题，保留失败 Attempt，先定位原因；不能为通过验收关闭质量门禁、伪造偏移或回退 Mock。需要比较策略时使用不同输出目录和明确覆盖配置，记录输入、实际参数、usage、耗时与质量差异；有限自制样本也不能证明所有长书均稳定。

## 凭证就绪后的可执行验收

本机已安装 Phase 8 模型 `data/models/kokoro-multi-lang-v1_0`。新机器先按 [TTS 指南](TTS.md) 安装。API Key 由本地 shell/安全凭证管理注入 `DEEPSEEK_API_KEY`；不要写入 TOML、命令历史或报告。

```sh
.venv/bin/bookcast config providers --config examples/deepseek-kokoro.toml
.venv/bin/bookcast doctor --config examples/deepseek-kokoro.toml
# 显式选择才调用收费API；缺少任一环境变量时测试跳过。
BOOKCAST_RUN_LIVE_LLM=1 .venv/bin/pytest tests/test_live_deepseek.py -q
# 正式音频使用独立输出目录；目标6分钟，实际时长需FFprobe验证。
.venv/bin/bookcast generate examples/content-demo.txt --config examples/deepseek-kokoro.toml --provider deepseek --mode two_host --minutes 6 --output-dir output/phase9-deepseek
.venv/bin/bookcast status output/phase9-deepseek/5bdad5ca96f5e42cd019ff30 --json
.venv/bin/bookcast resume output/phase9-deepseek/5bdad5ca96f5e42cd019ff30
```

`tests/test_live_deepseek.py` 仅验收自制短文本的真实分析、综合、双人脚本及无网络 resume，语音使用测试音调，不报告为真实播客。正式 CLI 样例只配置 DeepSeek 和 Kokoro，没有 Mock 备用。测试临时产物不替代正式可审计样例。

恢复默认使用任务快照；修改同名 Provider 后用 `resume JOB --config 新配置`。永久错误修复后用 `retry JOB --config 新配置`；不重复建立输出目录掩盖失败。原始样例书稿 ID 以 generate 实际打印为准。

验收补录：Job ID、源 SHA、模型及参数、各状态调用数量、provider_reported_usage（含失败调用，未知为空）、MP3 大小/SHA/FFprobe 时长，完整 resume 前后文件哈希/mtime 与调用数量。在 resume 时禁用网络仍成功才是更强证据；远端已处理但本地未记录成功的崩溃窗口仍可能重复计费，见 D-014。

逐段人工检查：三个章节主要观点是否覆盖；有无原文未支持的断言；B 是否提出追问/反例并获 A 回应；机械总结和衔接；是否长段逐字复述；结构化输出失败次数；试听角色、发音和停顿。填写实际观察与例子，不用自动 coverage/ratio 分数替代人工判断。
