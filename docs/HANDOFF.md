# 给下一位 Coding Agent

更新时间：2026-09-23T04:35:00Z。实际代码、Git 与现有产物优先；本文件的 Git 远程状态会过时。

## 当前目标与阶段状态

Phase 13 已完成：已完成的 BookCast Job 可显式导出带章节的 AAC/M4B，原有 MP3/Pipeline/Provider 保持原样。没有新增 LLM/TTS Provider，也没有重新调用 DeepSeek、Gemini、Kokoro 或 Qwen。Phase 12 的 Qwen technical acceptance 已完成，真人试听仍 pending，继续 experimental；勿把本阶段封装成功当作播客内容或 TTS 音质验收。

## 刚完成的工作与关键文件

- [export.py](../src/bookcast/export.py)：Pydantic 导出元数据、Job 锁、FFmpeg/ffprobe、M4B brand、AAC、节目或旧源书章节、合法可选封面、输入/输出哈希与只读有效性检查。语言按已支持映射写入 ISO 639-2/3 音轨标记；sidecar 不保存绝对路径、URL 或密钥。
- [cli.py](../src/bookcast/cli.py)：新增 bookcast export JOB_ID --format m4b，可给 --output-dir 或直接传任务目录、--cover 本地 JPEG/PNG。
- [web_service.py](../src/bookcast/web_service.py)、[web_api.py](../src/bookcast/web_api.py)、[page.tsx](../web/app/page.tsx)：只有 M4B 实际存在且缓存/Job 完整性有效才显示下载；原 MP3 播放与下载保留。
- [test_export.py](../tests/test_export.py)、[test_web.py](../tests/test_web.py)、[app.spec.ts](../web/e2e/app.spec.ts)：单/多章节、中文标题与路径、旧 Job、封面、损坏输入、重复导出、源变更、语言标签、Web 条件下载和浏览器实际下载。
- README、ARCHITECTURE、ROADMAP、PRODUCT、WEB、DECISIONS D-020 与 WORKLOG 同步了边界和验收。

## 当前代码和产物状态

功能提交 ed479cd3eea1e1dc87da7ea71e65390325b73294 已创建并在该提交上完成核心回归；最终 Git 快照提交用 git log -1 获取，避免在文件中自引用。新分层任务的 M4B chapter kind 为 podcast_segment，同时记录 source_chapter_ids，不假定与原书章节一一对应。无 EpisodePlan 的旧任务按 source_chapter 标记。章节边界取真实 WAV 帧数并对齐实际 MP3 时长；FFmpeg 编码后用 ffprobe 校验 AAC、章节数和时长。导出缓存键含 MP3、章节 WAV、计划、元数据、封面和音频契约；同输入复用 M4B，改变源音频只重导出，不重做 AI/TTS。

现有忽略目录中的真实样例：Phase 2 旧 Job 的 4.640 秒、2 章 M4B 位于 output/phase2-smoke-gd5jfq00/15902d020a918466ff5da3a5/podcast.m4b；Phase 9 中文节目 320.283 秒、3 章 M4B 位于 output/phase9-deepseek/5bdad5ca96f5e42cd019ff30/podcast.m4b。这些音频、来源书稿与模型不进入 Git。ffprobe 确认 M4B major brand、AAC、标题/作者/来源及连续中文章节；小样本完整音频解码成功。

## 测试与结果

- 功能提交上完整 pytest：390 passed、4 skipped、10 subtests passed、7 个既有依赖 warning；4 个显式真实服务/产物测试默认跳过。
- 专项 M4B + Web API：9 passed；FFmpeg 真实生成、ffprobe 容器/章节核对和小样本全解码通过。
- Next.js build、TypeScript typecheck 与 Playwright 3 项浏览器 E2E 通过；其中浏览器验证显式导出后显示 M4B 下载并收到 podcast.m4b。
- 项目 validator、compileall、提交 diff --check 通过。最终快照后再次运行 validator 与 git diff --check。

## 未解决问题与下一步

没有 Phase 13 阻塞。M4B 是完成 Job 的显式附加导出，Web 不在浏览器内编码，也不自动生成 M4B。封面仅接受用户明确提供并有权使用的 JPEG/PNG；尚无从合法来源自动读取封面的流程。已有 Qwen 真人试听与其它历史质量限制仍记录在 STATE 和 TTS_PROVIDER_EVALUATION。

下一位 Agent 先核对 git status、git log -1、origin/main 与 STATE；等待用户授权下一阶段或推送。不要重跑 DeepSeek/Gemini TTS/Qwen 推理、不要重下模型，不要将音频或密钥提交 Git。
