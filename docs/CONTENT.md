# 分层内容生成（Phase 4）

Phase 7 的 [Web 客户端](WEB.md) 将三种模式和时长预算传入同一 Core，面板可展开质量警告；不会在前端生成脚本。界面默认 40 分钟预算，Core/CLI 默认值仍是 10 分钟，内置音频仍为测试音调。

新任务默认 `two_host`、10 分钟脚本预算；没有密钥时使用离线规则 Mock。这里验收的是分层处理、来源约束、质量诊断与恢复机制，不能据此宣称真实模型的中文写作或事实准确率已经达标。

```sh
uv run bookcast generate examples/content-demo.txt --mode two_host --minutes 6 --output-dir output/phase4-demo
uv run bookcast generate examples/content-demo.txt --mode summary --minutes 3 --output-dir output/summary
uv run bookcast generate examples/content-demo.txt --mode deep_read --minutes 6 --output-dir output/deep-read
uv run bookcast generate examples/content-demo.txt --resume --output-dir output/phase4-demo
uv run bookcast generate examples/content-demo.txt --resume --revise-segment 0002 --output-dir output/phase4-demo
```

`acquire --generate` 同样支持 `--mode` / `--minutes`。预算接受 1～120 分钟，是内容取舍和脚本长度的估计，不保证实际音频恰好等长。恢复时省略模式和预算会沿用任务记录；修改它们需要新输出目录，避免混合不兼容脚本。

## 层次与产物

```text
Chapter → 有界分块 → 九类 Chapter Analysis → 章节综合树
        → Book Synthesis 综合树 → 全局 Episode Plan
        → 各 Segment Dialogue → 各 Segment Consistency Review
        → 全局 Quality Report → TTS → MP3
```

所有 LLM 调用通过现有 ProviderChain 与 attempt 日志，业务内容模块不依赖厂商。规划和指标计算使用确定性本地算法，不产生隐式 AI 调用。

| 路径（在任务目录内） | 内容 |
| --- | --- |
| `chapters/0001.json` | 原始解析章节及 source_locator |
| `analysis/chunks/0001-0001.json` | 一个文本块的九类结构化 finding |
| `analysis/0001.json` | 本章所有块的索引、九类 claim ID 索引与章节综合 |
| `analysis/claims.json` | 全部可定位证据：转述、原文短片段、字符偏移、章节与类别 |
| `synthesis/chapters/0001/层-节点.json` | 章节的所有分层综合结果 |
| `synthesis/book/层-节点.json` / `synthesis/book.json` | 整书综合节点与最终代表性主题 |
| `plans/episode.json` | 写脚本前生成的完整规划、预算与覆盖取舍 |
| `scripts/0001.json` | 节目片段的发言、角色职责、引用与归属 |
| `evaluation/segments/0001.json` | 按 source 发言索引核对证据的结构化复核 |
| `evaluation/quality.json` | 全局指标、阻断项、警告和方法局限 |
| `audio/0001.wav` / `podcast.mp3` | 片段音频与合并音频；Mock 为测试音调 |

九类信息为 `core_ideas`、`arguments`、`evidence`、`examples`、`people`、`concepts`、`counter_arguments`、`connections`、`key_passages`。不存在的内容返回空列表；核心观点至少一项。每类最多六项，每项转述最多 240 字符、私有证据最多 160 字符。`start/end` 是解析后整章字符串的 Python 字符偏移（不是原文件字节或 PDF 坐标），必须与 quote 完全匹配。chapter_id、chunk_id 和后续引用均校验，不符合时作为永久错误停止，不切模型掩盖问题。

长章节按不重叠的 4,000 字符块完整遍历；各级最多合并四个主题输入，模型每次最多输出八个主题，每个主题最多引用八项证据。上层使用子节点代表性主题，不会发送整本原文。业务提示 JSON 限 28,000 字符，超限显式失败；单段脚本总长限 12,000 字符以便有界复核。此限制不等同于任意厂商的 token 上限，适配端点仍需支持对应请求。

上层压缩会舍弃细节，原始块及九类提取结果始终保留。当前分块不按句子边界、不重叠，边界语义与极长书的代表性选择是后续改进点；不能把根节点摘要解释成所有细节已进入播客。

## 全局规划与角色

规划器按综合结果的 importance（1～5），加上是否被全书综合引用进行排序；先尽量为各章分配一个主题，`deep_read` 再补充论证主题。主题文本忽略大小写及空白后相同的条目合并；LLM 综合也被要求合并重复观点，但没有嵌入相似度去重。片段数上限为 24，同时受预算每 30 秒一个名额限制。选定片段按来源顺序排列，时间按重要性权重分配，总和等于预算。排序去重和根节点代表性都属启发式，不保证最优选题。

`summary`：紧凑讲解核心结论及限制；`deep_read`：进一步讨论论据、条件和推理；`two_host`：主持人（Host A）解释，嘉宾（Host B）追问、反例和应用，主持人回应。两人只轮流讲解会被角色检查拦截。角色语义仍要人工听读，意图标签不能证明对话自然。

每段获得已保存的全局片段计划、精选证据、前后主题及上一段真实结尾（最多两条发言、各 240 字符）；这些内容全部计入输入指纹。书中主张使用 `attribution=source` 且必须给出规划内的 claim IDs；主持人讨论与假设分别标为 `discussion` / `hypothetical`，假设还需在实际话语中明确说明。

规划覆盖与脚本覆盖分开：计划提及某章不代表脚本真正使用了该章证据。报告只按 source 发言中的有效引用计算章节覆盖。预算省略、去重或写作未引用的章节会明确列出；解析覆盖仍在 metadata，不能互相替代。

## 一致性与质量门禁

LLM 逐段复核每条 source 发言，给出 supported / contradicted / unverifiable 与原因；检查结果也有独立检查点。Mock 没有语义理解能力，始终将这些发言标为 unverifiable，不能伪造“事实已验证”。真实 Provider 的复核同样不等同于独立事实核查。

本地评估还检查：

- 重复率：去除非字母数字字符后，重复的 20 字符窗口占比；超过 25% 警告。
- 章节覆盖：有有效来源发言的章节 / 所有解析章节；不足 100% 列出缺失章并警告。
- 脚本长度：按每分钟 240 中文字符等效量估时；相对预算偏离超过 35% 警告。
- 角色比例：two_host 的 Host B 字符占比不在 25%～65% 时警告；缺任一主持人或缺 B 追问/反例/应用则阻断。
- 空泛表达：固定短语表计数并警告，不是完整语言质量模型。
- 事实形式：无效引用、无证据 source 发言、证据不含的阿拉伯数字、未明说的假设会阻断；语义复核 contradicted 也阻断，unverifiable 警告。
- 逐字复述：源文本与连续话语（跨发言边界）出现至少 80 个归一化字符相同的片段时阻断；所有来源统一采用此产品约束。这个数值不是法律许可界线，未触发也不证明满足所有版权要求。

有阻断项时质量报告仍成功落盘，但任务失败，尚未开始本轮 TTS；警告可以继续生成，报告状态为 `needs_review`。零警告的 `checks_passed` 只表示这些检查通过。事实一致性、语言自然度、完整论证和版权边界都仍需人工复核，不自动无条件重试或换模型。

## 恢复、修订与兼容

Phase 5 新任务 `manifest.schema_version=3`、`pipeline_version=2`，附带 `content_options` 和 `segment_revisions`；仓库 STATE 和获取 acquisition 的版本保持不变。旧 `pipeline_version=1` 任务保留原分析/脚本/音频路径，仍可校验复用产物，并原样备份迁移 manifest v1/v2；不会被静默升级、重新生成或冒称有 Phase 4 质量报告。想对旧书运行分层流程应使用新 `--output-dir`。

块分析、综合节点、片段脚本、复核、TTS 都立即原子落盘并校验哈希。更换为另一 Provider 保留已完成调用的实际归属；同名实例配置变化会使其调用缓存失效。中断从最小未完成任务继续。按 Job ID 恢复、永久失败重试和缓存策略见 [JOBS.md](JOBS.md)。根综合、规划、质量报告同样缓存，上游变化通过依赖指纹传播。

如果质量失败或需要改写，可执行 `bookcast retry JOB_ID --revise-segment 0002`，也兼容原 `generate 源文件 --resume --revise-segment 0002`。片段修订号先持久化，重新生成该片段；其来源分析和全局规划保持不变。若新的结尾变化，后续依赖它的脚本会重新生成；复核与 TTS 仅在输入变化时重做。调用审计保留，当前产物文件更新。修订后再次崩溃只需普通 `--resume`，不要重复增加修订号。没有自动重写循环；若证据提取本身错误，应修复相应实现/提示版本，或用新输出目录重新验证。

验证命令和实际 demo 见 [PHASE4_DEMO.md](PHASE4_DEMO.md)。
