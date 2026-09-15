# 给下一位 Coding Agent

更新时间：2026-09-16（Asia/Shanghai）。实际代码与Git优先；不要依据旧快照推断远端状态。

## 当前目标与结果

Phase 9真实DeepSeek + Kokoro技术验收已完成。正式三章样例的13次LLM调用首次成功，输出5分20秒中文双人MP3。内容为needs_review，有两处推论被归为原文观点；没有真人试听，不宣称播客质量优秀。没有开始Phase 10，没有push。

## 刚刚完成与关键文件

- 已有generation.py、compatible.py、provider_chain.py：复用中立Provider与Chat Completions；强类型thinking/effort/max_tokens、中央任务策略、usage和有效配置缓存，仍无DeepSeek SDK。
- content.py / content_models.py：真实分析反复算错引文偏移后，改为EvidenceAnalysis选择预切证据ID；Core精确查表转换原有RichAnalysis再严格校验。非法ID仍永久失败，不模糊修补。分析提示content-analysis-v3，其他content-v1；理由见D-018。
- content_mock.py / providers.py及test_content/test_generation/test_job_recovery：更新离线契约，加入Unicode、标点极端输入、重复证据定位、无效ID和旧错误偏移回归。
- test_live_deepseek.py：默认跳过收费API；显式BOOKCAST_LIVE_OUTPUT可复验人工恢复的真实任务，保留失败历史且检查最新调用完成、禁网resume，无自动重试。
- README、PROVIDERS、CONTENT、ARCHITECTURE、PRODUCT、JOBS、ROADMAP、DECISIONS、PHASE9_REAL_LLM、STATE、WORKLOG同步实际结果。

## 当前代码和产物状态

模型deepseek-flash，响应同名；示例examples/deepseek-kokoro.toml仅DeepSeek+Kokoro，无Mock后备。抽取disabled、章节综合low、整书综合high、对话/复核low；Planner仍本地确定性。正式样例说明策略可运行，尚未做多强度对比。

本机正式任务：

- Job：7229d03765db4d1c860c7bd18d62b178。
- 目录：output/phase9-deepseek/5bdad5ca96f5e42cd019ff30/。
- MP3：320.283秒、3,844,557字节、24kHz单声道；SHA-256为dc00547eb4a5a0e57fd02924a0a12463cef3d594a63cdc64a697240be83c0b93。
- 当前保存配置来自data/phase9-demo.toml，仅Kokoro语速从示例1.0改为0.8。首版4分11秒，调整仅重做本地23个语音单元，13次LLM记录未变。历史共59次调用，50个步骤完成。
- 原书、密钥、模型、音频、日志在忽略目录，不在Git；其他克隆须自行准备。现有本机模型不要重复下载。

用户已将授权凭证复制到data/deepseek-key.txt，之前Downloads拒绝访问不再是阻塞。应用只从环境变量读取Key；不打印、不放命令参数、不写配置/manifest/报告。不从其他应用寻找凭证。

## 已运行测试与结果

- 工作区专项142 passed（参数/协议/Provider/内容/任务恢复），全量318 passed、10 subtests passed、1收费联网测试默认跳过；7个既有弃用警告。
- 真实测试02最初schema_error，人工显式诊断重试后完成；对已完成真实任务显式运行pytest为1 passed，0.56秒，没有再次付费生成。语音为Mock测试音调，不与正式Kokoro样例混淆。
- 正式任务13次LLM首次成功，FFmpeg完整解码MP3成功，非静音（mean -23.2dB、max -2.3dB）；没有真人听感判断。
- 禁止HTTP和TTS推理恢复：81文件集合、SHA、mtime不变，调用59→59。临时副本全局thinking改disabled后分析命中、首个章节综合失效；HTTP前注入停止，原任务未变。
- project validator、compileall、git diff --check通过。秘密字面值扫描207个受检源码/文本产物无匹配。Web代码未改，没有重跑浏览器E2E；Python全量包含Web后端。
- 正式usage input16422/output24445；所有三个测试/正式任务48次尝试合计input56285/output79397（含5次失败）。reasoning缺失保持null，未估算价格。完整分项见PHASE9_REAL_LLM。

## 未解决问题和下一步建议

1. 保留正式报告needs_review。两处合理推论不应完整标source；若要发布，先审读并通过已有片段修订机制处理，再验证脚本与受影响音频。真人试听发音、停顿和0.8语速自然度待完成。
2. 真实JSON并非100%稳定：测试01累计4次旧坐标业务失败，测试02新证据契约曾1次schema_error；都保留历史，没有切Provider或无限自动重试。原始响应未保存，不能断言具体坏字段。
3. 402、额度、限流、timeout、5xx依官方协议与离线fixtures验证；不要声称实际触发服务故障。没有长书或多推理强度质量对照。
4. 当前阶段到此；Phase 10必须等用户授权。建议下一阶段先扩大合法长文本质量样本、改进归属与自然度，不重建Core。

运行入口：

```sh
.venv/bin/bookcast status output/phase9-deepseek/5bdad5ca96f5e42cd019ff30 --json
.venv/bin/bookcast resume output/phase9-deepseek/5bdad5ca96f5e42cd019ff30
.venv/bin/pytest -q
python3 scripts/validate_project.py
```

联网新任务需要显式Key且可能收费；默认pytest离线。改同名Provider要显式--config，永久错误修复后retry。content-analysis-v3的提示/schema变更会使旧分层分析失效，其他任务按实际输入和产物传播；不要承诺升级提示后仍全部命中。纯完成任务原配置恢复不会重新收费。

## 不要重复做

不要重写Provider/Pipeline/Job/Skill/Web或增加DeepSeek SDK；不要重复下载模型、重新收费生成已完成内容或清除历史失败。不要关闭质量门禁、把自动指标视作优秀内容证明、把未试听写成已试听。不要提交密钥/模型/书籍/音频，不自动push。远端已处理但本地尚未持久化的窗口不能保证无重复计费，见D-014。

## 最近 Git commit

本轮接手HEAD：48eb78b（凭证访问阻塞快照）。此前已验证功能提交：ff3c48ac960e8435889dcc303665626e4438f186；本地origin/main在接手时为4137d8550a7cf1c603248732e0be97710e217eb1。以上只是接手观察；本轮未fetch/push。当前修复与真实验收将独立提交，验证后按D-006写入last_verified_commit；快照自身使用git log -1获取，避免自引用。
