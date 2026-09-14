# Phase 4 实际内容 Demo

验证时间：2026-09-14T04:11:16Z。本记录来自实际 CLI 运行与 ffprobe 检查；未调用远程模型或真实 TTS。

输入是仓库自制的三章中文短书 [content-demo.txt](../examples/content-demo.txt)，讨论分工收益、协作代价与调整空间；并非外部版权书稿。

```sh
uv run bookcast generate examples/content-demo.txt --mode two_host --minutes 6 --output-dir output/phase4-final-demo
uv run bookcast generate examples/content-demo.txt --resume --output-dir output/phase4-final-demo
uv run bookcast status output/phase4-final-demo/5bdad5ca96f5e42cd019ff30 --json
ffprobe -v error -show_entries format=duration:stream=codec_name,sample_rate,channels -of json output/phase4-final-demo/5bdad5ca96f5e42cd019ff30/podcast.mp3
```

结果目录：`output/phase4-final-demo/5bdad5ca96f5e42cd019ff30/`，被 Git 忽略；新克隆可用以上命令重新生成。脚本、分析和质量报告都是可读取的 JSON。

| 实测项 | 结果 |
| --- | --- |
| 原书章节 / 节目片段 | 3 / 3 |
| 章节证据覆盖率 | 100% |
| 20 字符窗口重复率 | 4.93% |
| 脚本字符 / 预算字符 | 1285 / 1440 |
| 估计讲话时长 | 321.2 秒；预算 360 秒 |
| Host B 字符占比 | 27.47% |
| 空泛短语 / 长段复述检测 | 0 / 未发现 |
| 来源形式错误 / 阻断项 | 0 / 0 |
| 质量状态 | needs_review；Mock 语义核验需人工检查 |
| AI attempt 数量 | 16，全部已完成，含每段复核和 TTS |
| MP3 实际时长 | 15.250000 秒测试音调，非人声 |
| 音频格式 | MP3，24000 Hz，单声道 |
| 恢复验证 | 全部产物及 manifest 字节、SHA-256、mtime 不变；没有新增调用 |

源文件 SHA-256：`4db2ed61acd27f17a47becf9df81483a40567ef81c260c3e58795509f9b02bfa`。
MP3 SHA-256：`85bd0e272e9c8ca1375ba5fd3d0c482a791e0035eed34a9323eb8bab133f4d13`。

## 第一段实际话语

以下摘自生成的 `scripts/0001.json`，没有人工改写。Mock 展示角色和证据契约，不声称具备真实阅读理解能力。

- **主持人**（discussion / transition）：我们从第一章 分工的收益切入。这是离线规则示例，重点展示观点、追问和证据的关系。
- **主持人**（source / explain）：这里的讨论起点是分工能减少任务切换；还需联系这一点：却不代表每一种工作都应拆成更小的步骤。
- **嘉宾**（discussion / question）：你刚才把“分工能减少任务切换”作为起点。这个结论依赖哪些条件？如果条件不成立，是否还说得通？
- **主持人**（source / explain）：本章还提供了一条相关线索：这里的讨论起点是因为参与者知道下一步由谁接手；还需联系这一点：重复解释的负担也随之降低。
- **主持人**（discussion / explain）：需要先区分文中陈述和我们推出来的结果。已有片段能定位讨论起点，但不能据此断言所有场景都适用。这里保留条件，比给出一个无条件结论更准确。
- **嘉宾**（hypothetical / counterexample）：假设换一个参与者目标不同、可用资源也不同的场景，同样的做法可能得出另一种结果。这是检验适用边界的假设反例，不是书中已经发生的事实。
- **主持人**（discussion / explain）：这个反例提醒我们：判断一个观点时，既要看它解释了什么，也要检查它没有覆盖什么。回到引用位置核对作者是否限定了条件，才能继续往下推。
- **主持人**（discussion / transition）：带着这个边界，下一段我们继续看第二章 协作的代价。

## 验证边界

本例的定量规则全部通过，唯一警告是来源发言的语义尚未验证。规则型 Mock 使用有限句子和模板，不能由此推断长书论证已充分保留、两位主持人足够自然或真实 AI 已达到发布质量。音频只验证编排和封装；说话时长估计与测试音调时长分别报告。
自动测试另覆盖三种模式、18 章及跨多个文本块的长章、有界综合树、各阶段切换、分块中断恢复、错误引用/数字/复述/角色/矛盾阻断、指定片段修订与上下文传播。完整命令为 `.venv/bin/python -m pytest -q`；最新结果见 STATE 与 HANDOFF。
