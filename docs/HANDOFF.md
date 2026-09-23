# 给下一位 Coding Agent

更新时间：2026-09-23T00:02:31Z。实际代码、Git和产物优先；禁止根据旧交接猜测实时状态。

## 当前目标与阶段状态

Phase 12工程工作：人工TTS试听协议与原子临时产物清理。工程技术验收目标完成；截至本次没有真人试听反馈，因此 **technical acceptance = completed；listening acceptance = pending**。qwen-local必须继续experimental，不提高默认优先级，不宣称胜过Kokoro。不要开始Phase 13。

## 已完成内容与关键文件

- [TTS_PROVIDER_EVALUATION.md](TTS_PROVIDER_EVALUATION.md)：三路试听盲码、分数锚点、14项标准表、错误定位模板及质量门禁；保留无评分事实和产品定位边界。
- src/bookcast/storage.py：新原子文件标记；只扫当前Job目录中的文件，跳过符号链接；清理标记的BookCast临时文件，以及manifest任务可对应的旧式语音unit零字节文件。
- src/bookcast/pipeline.py：在持Job锁后调用清理；不改manifest步骤、Attempt/Artifact、有效性检查或缓存键。
- tests/test_tts.py：真实子进程SIGKILL发生在原子写入窗口，恢复后清临时物、保留已完成WAV且只运行缺失语音单元；测试无关文件保护与目录符号链接。
- tests/test_live_kokoro.py、test_live_gemini.py、test_live_qwen.py：显式选择本地既有任务的零合成/零HTTP/零LLM恢复验证；默认pytest跳过这些真实产物测试。
- README、ROADMAP、PRODUCT和本文件记录工程收尾状态及三Provider产品定位。

## 临时文件清理规则

新的原子写临时文件命名为.bookcast-tmp-<target>.<8位随机串>.tmp，正常路径由atomic_target的finally移除。进程崩溃时，在同一Job锁内恢复会识别该保留前缀并清理。为兼容Phase 11旧命名，只在audio/units/中且target由manifest里的tts:<chapter>:<unit> step确定、同时文件为零字节时清理旧式.<chapter>-<unit>.wav.<8位随机串>.tmp。其他旧式临时文件不触碰。不用mtime判断；不跟进或移除符号链接；清理不改Artifact或manifest。

## 既有真实数据复验

不重新生成节目、不下载模型、不调用任何付费服务。三个完成任务的零调用测试均实际运行：

- Kokoro：显式真实任务恢复1 passed；阻断KokoroTTSProvider.synthesize_unit，音频产物哈希/mtime无变化。
- Gemini：显式真实任务恢复1 passed；阻断Gemini _request，无HTTP，产物哈希/mtime无变化。
- Qwen：显式真实任务恢复1 passed；阻断模型加载、Qwen合成、HTTP及LLM；Phase 11遗留的0字节文件被移除，其他文件哈希/mtime不变。

## 人工试听状态与产品定位

没有任何真人评分数据。已在选型文档加入统一1–5/N/A锚点、完整节目听校、三段对齐片段、双盲顺序、turn/时间戳记录、逐字错误统计及至少两位独立听者的门禁。具体评分空白，不能把自动指标冒充人耳结论。

当前定位仅基于已核实部署方式和资源：Kokoro是约350MB模型包的较轻量本地CPU离线基线，无同机公平速度benchmark，避免称极速；Gemini是明确用户同意发送文本后的云端多speaker API；Qwen本地MPS、所测模型约4.52GB，支持自然语言style instruction但相对风格表现与听感没有实评。无best/premium/high-quality宣传标签。

## 验收进度

- SIGKILL临时文件专项：3 passed。
- Job Recovery + Qwen Offline + Gemini segment 专项：70 passed。
- Kokoro/Gemini/Qwen既有真实任务禁调用resume：各1 passed。
- 功能提交7e71a2b8a24e678a93ac7d29ed8049f34d5faab5上完整pytest：381 passed、10 subtests、4个显式真实服务/产物测试默认跳过、7个既有warning（52.43秒）。
- 同一提交上Project validator、compileall、git diff HEAD^ HEAD --check及Kokoro/Gemini/Qwen实际零调用恢复均通过。

## 下一步

1. 若未来收到听者数据，按盲码先记录原始分数和错误证据，再揭盲；无人评审时维持Qwen experimental。

不要重跑DeepSeek/Gemini TTS/Qwen推理，不要下载模型，不要修改既有缓存语义，不要删除零规则以外的用户文件。不要把音频/模型/凭证提交Git。

## 最近Git状态

- 远程origin/main上已有Phase 12之前的提交e32d002；Phase 11功能验收SHA记录于STATE。
- 7e71a2b8a24e678a93ac7d29ed8049f34d5faab5：Phase 12功能提交，STATE.last_verified_commit指向此完整SHA。
- 本次交接快照自身的提交从git log -1读取，不在此文件自引用，遵循D-006；不主动push。
