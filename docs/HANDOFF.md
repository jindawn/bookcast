# 给下一位 Coding Agent

更新时间：2026-09-15T04:14:17Z。

## 当前目标

Phase 7 最小本地 Web App 已完成，功能提交及其全量/浏览器验证通过。UI → Application API → Core，不能把业务逻辑移入页面。本快照按 D-006 单独提交，不开始后续阶段。

## 刚刚完成与关键文件

- web/app：Next.js 静态页面，文件/书名、明确版本、三模式/分钟预算、进度、Provider、历史、播放与恢复；已查看桌面与移动截图。
- src/bookcast/web_api.py：FastAPI、本机同源边界、32 MiB 流式上传、Core 校验、状态/音频 Range。
- web_service.py/web_worker.py：提交先落盘、客户端幂等键、独立进程、内核锁、最多两个 worker；Core Job 创建后以其 manifest 为权威。
- composition.py：提取 CLI 已有配置快照恢复与 Provider 注入供 worker 共用。新增 serve 命令；解析、下载、内容、质量、Provider算法、音频与 manifest 格式不变。
- tests/test_web.py 与 web/e2e：Core 委托、真实 SIGKILL、quota/timeout/schema、缓存、播放和恢复。docs/WEB.md 与 D-015 记录运行/边界及 Tauri 评估。

## 当前代码与运行

安装 `uv sync --extra dev --extra web`，运行 `npm --prefix web ci`、`npm --prefix web run build`，然后 `.venv/bin/bookcast serve`，打开 http://127.0.0.1:8765。Core/CLI 不需 Node 或 web extras。

Web 默认 data/web，历史仅含本工作空间提交。Core 目录在 jobs/{web_id}/output/{book_id}，可从面板展开并交给 CLI。关闭页面/API 不清空记录；worker 可继续，电脑重启后手动恢复。修改 Provider 参数需用 CLI 显式 --config，UI 重试沿用快照。

本次已启动8765端口预览（纯Mock examples/providers.toml）；进程是否仍在运行以本机核对为准。通过本地API导入自制content-demo并生成：Web ID 4590ab11918d4751b00da9841dcbdc3f，Core ID bb28c7579f31432fba843ffe17dda0f9，3章/27步骤/16次调用，MP3为184077字节。文件在忽略的 data/web/jobs/4590ab11918d4751b00da9841dcbdc3f/output/5bdad5ca96f5e42cd019ff30，不随Git分发。

## 已运行测试

- Phase 6 基线：223 passed、10 subtests passed。
- Phase 7 API：13 passed；真实 SIGKILL 后保留完成章节字节/mtime/调用次数。
- Playwright：3 passed，真实上传/生成/播放/刷新历史、quota恢复、候选交互及移动布局；候选交互为 HTTP fixture，来源资格在 pytest 中经 Core 验证。
- Next 静态 build、TypeScript、compileall、项目校验及 diff 检查通过。
- 已验证功能提交82c49da上的最终全量回归：236 passed、10 subtests passed；3项浏览器E2E和build/typecheck/compile/项目校验也在该提交上重跑通过。
- 已知警告：既有5个PyMuPDF/SWIG，加Starlette TestClient/httpx与anyio的2个依赖弃用警告。浏览器首轮定位器与Next无障碍alert重名，已修复并重跑通过。

## 未解决问题与下一步

当前阶段没有未完成任务或阻塞。下一位先核对本快照与实际Git，按新授权决定是否推送、做真实中文TTS/内容验收或扩展桌面发行。

真实人声、OCR、M4B、桌面包未实现；默认音频是 Mock 测试音调。Provider 真实中文内容质量未验收。Tauri 仅评估，没有 Rust 或平台包。Web 轮询复用 Core 完整性哈希，大书库 I/O/分页优化、旧 CLI 任务导入、自动调度未做。无账户/支付/云同步。来源网络若需显式 --resolve，先用 CLI 获取再上传，不降低 Core 网络保护。

## 不要重复做

不要重写 Core、引入前端 Provider 调用或第二套 AI 任务状态；不要根据提交记录覆盖 Core 成功/错误；不要给永久错误自动换模型。不要将书籍、音频、截图、依赖目录或凭证提交。Web 截图与运行数据均在忽略目录，测试数据来自自制样本。

## 最近 Git commit

接手 HEAD：892eb61 — docs: finalize Phase 6 skill verification and handoff，已核对与 origin/main 同步。
最近已验证 Phase 7 功能提交：82c49da946607a66a5ab1f5cbd59da4d27d81db7 — feat: add local Web client over BookCast Core。
功能提交包含33个文件；最终交接快照通过 git log -1 查看，避免自引用。本阶段未执行 push，origin/main仍指向Phase 6交接提交892eb61。
