# 给下一位 Coding Agent

更新时间：2026-09-15T14:41:34Z。

## 当前目标

Phase 9：真实 DeepSeek 分层内容 → Kokoro 中文双人播客。离线增强已验证；**用户已授权读取Downloads凭证文件，但普通和提权执行都被系统拒绝（Operation not permitted），尚未读到Key**。已请用户复制到Git忽略的data/deepseek-key.txt，之后安全加载为DEEPSEEK_API_KEY，不打印内容。真实验收仍未完成，不开始Phase 10，不主动push。

## 刚刚完成与关键文件

- generation.py：严格 thinking enabled/disabled、effort low/high/max、max_tokens；集中 bookcast-v1 候选策略，无任意JSON透传。
- compatible.py：复用 Chat Completions，任务调用视图、usage/响应模型、402分类、截断/坏JSON/schema永久失败；不记录服务原文或思考内容。
- provider_chain.py / pipeline.py / models.py：每次Attempt在调用前保存实际参数，结束时保存可选usage；以任务最终配置核验缓存，D-014替换Provider保留旧结果规则不变。
- provider_api/config/cli：可选任务契约、向后兼容配置与 config providers 的 effective_generation 输出。
- examples/deepseek-kokoro.toml：正式无密钥配置，仅DeepSeek+Kokoro，无Mock备用。
- tests/test_generation.py / test_live_deepseek.py：51项离线新专项及显式环境开关联网内容测试；README、PROVIDERS、相关架构文档、D-017和PHASE9_REAL_LLM记录范围与验收边界。

## 当前代码状态与运行

未配置 reasoning 的旧调用保留原适配器哈希；same-name配置改变只使有效参数改变的AI任务失效。Provider key不入缓存；旧manifest v3可读，新增审计字段缺省null。Core/Skill/Web不重写，Planner仍本地确定性规划。

本机已安装Phase 8模型 data/models/kokoro-multi-lang-v1_0；不要重复下载。模型/本地配置/书籍/音频均被Git忽略。已有Phase 8人声示例见TTS.md，其脚本为Mock，不是本次真实LLM证据。

```sh
.venv/bin/bookcast config providers --config examples/deepseek-kokoro.toml
.venv/bin/bookcast doctor --config examples/deepseek-kokoro.toml
BOOKCAST_RUN_LIVE_LLM=1 .venv/bin/pytest tests/test_live_deepseek.py -q
.venv/bin/bookcast generate examples/content-demo.txt --config examples/deepseek-kokoro.toml --provider deepseek --mode two_host --minutes 6 --output-dir output/phase9-deepseek
```

联网测试同时要求环境Key和开关，默认pytest跳过；只验收真实内容，测试语音为Mock音调。正式CLI样例才使用Kokoro。永久错误修复后retry；resume沿用配置快照，修改同名Provider需显式--config。完整操作和人工检查清单见PHASE9_REAL_LLM.md。

## 已运行测试与实际网络观察

- 接手HEAD 4137d85的关键基线86 passed。
- 工作区专项99 passed，1联网测试跳过；包括新51项参数/HTTP/usage/缓存/中断测试。
- 工作区全量313 passed、10 subtests passed、1联网测试跳过；7个既有依赖弃用警告，43.73秒。
- 功能提交ff3c48a上全量再次通过：313 passed、10 subtests passed、1联网测试跳过，44.35秒；project validator、compileall和提交差异检查通过。
- project validator、compileall、git diff --check通过。仅既有后端和CLI受影响，未重新运行浏览器E2E。
- 实际向官方 /models 使用固定无效测试凭证探测：HTTP401 → authentication_error。402/429/timeout/5xx等为官方文档+离线fixtures验证，不能声称实际触发。
- **有效生成没有执行，实际token数未知，尚无DeepSeek+Kokoro MP3或人工内容/听感结论。**
- 凭证接续本轮重新验证Provider专项：99 passed，5个既有警告（14.49秒）。代码未改，尚未执行新的API请求。

## 未解决问题与下一步

1. 功能提交已验证，STATE记为blocked且清空in_progress；只剩真实验收需凭证。接手核对Git，不相信易腐远端状态描述。
2. 用户已提供凭证文件但系统拒绝读取；等待复制到data/deepseek-key.txt后安全加载环境变量，执行有界联网测试。系统拒绝并非自动审批拒绝；不要绕过Downloads访问限制。失败保留真实原因，不盲目换模型/修补schema掩盖问题。
3. 执行6分钟自制文本CLI样例；核验引文字符偏移、来源覆盖、A/B追问与回应、幻觉、复述、时长和真实音频；候选reasoning策略须据实际质量决定。
4. 补记Job/产物SHA/官方usage与调用数、断网resume不新增调用、配置变化结果；更新状态再完成Phase 9提交。

## 不要重复做

不要重写Provider/Pipeline/Job/Skill/Web或新增DeepSeek SDK。不要将Mock内容或既有Kokoro样例冒称真实LLM验收；不要为通过验收关闭质量门禁。不要提交模型/密钥/源书/音频。不自动push，不开始Phase 10。

无API Key不应继续制造收费测试失败或读取别的应用凭证。真实usage缺失保留null，不能估算为官方计费。已完成记录的resume不重复调用；远端已处理但本地未记录成功的崩溃窗口无法保证不重复计费（D-014）。

## 最近 Git commit

2026-09-15接手核验：HEAD与本地origin/main均4137d8550a7cf1c603248732e0be97710e217eb1（Phase 8最终交接），此前Phase 7/8已推送。旧快照中的“未push”描述已纠正，这只是接手时观察。
最近已验证功能提交：ff3c48ac960e8435889dcc303665626e4438f186 — feat: add task reasoning and usage audit for compatible LLMs（24个文件）。本阶段没有push；快照自身提交用git log -1获取，避免自引用。
本轮接手HEAD为62b849f57f5397453fe68aeaa4b971e36f1c9265（交接快照），main比本地origin/main领先两个提交；此为本轮开始时观察。
