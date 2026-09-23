# 给下一位 Coding Agent

更新时间：2026-09-23T05:18:35Z。请按 [AGENTS.md](../AGENTS.md) 接手，先读取项目入口和状态，再查看实际 Git；本文件不能替代代码与 Git 历史。

## 当前目标与状态

Phase 15 工程验收已完成。BookCast 已有 PR 级 GitHub Actions 配置、离线测试分层、第三方许可清单、发布检查表和独立临时克隆验收。版本仍为 `0.1.0` / pre-1.0；项目许可证未选择，不得对外声称当前项目或二进制可再分发。没有新增 AI Provider。

## 刚完成的工作与关键文件

- [`.github/workflows/ci.yml`](../.github/workflows/ci.yml)：Python Core、Web、Static validation 三个 job。PR 默认不会下载 Qwen、调用 DeepSeek/Gemini 或运行 Playwright；Python 3.12 安装锁文件，执行默认 pytest、compileall、validator 和 Mock MP3/M4B 冒烟；Web 执行 `npm ci`、typecheck、build。
- [`tests/conftest.py`](../tests/conftest.py)：测试文件须归入 `unit`、`integration`、`live` 或 `large_model`，未分类模块会让 pytest collection 失败。`pytest` 默认 `-m 'unit or integration'`；`manual_listening` 只用于人工验收，不作为自动测试。
- [`scripts/ci_smoke.py`](../scripts/ci_smoke.py)：写入系统临时目录，显式创建 demo 配置并验证 doctor、两章 Mock 任务、MP3、AAC/M4B 与 ffprobe 章节；不读开发机配置或数据。
- [`tests/test_release_large_book.py`](../tests/test_release_large_book.py)：只有设置 `BOOKCAST_RUN_RELEASE_LARGE_BOOK=1` 并明确选择 `-m large_model` 才会联网获取 Gutenberg 3300，再执行全书 Mock、缓存恢复和 M4B 检查。
- [`src/bookcast/content_mock.py`](../src/bookcast/content_mock.py) / [`tests/test_content.py`](../tests/test_content.py)：修复英文大书 chunk 边界落在纯标点时 Mock 分析器因空 clause 崩溃的问题。保留任务失败记录后从最小步骤恢复，全书完成。
- [`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md)：记录直接依赖、模型、eSpeak 数据、FFmpeg 与 Web lock 的许可证发现及资产级待审内容；没有创建 root `LICENSE`。
- [`docs/RELEASE_CHECKLIST.md`](RELEASE_CHECKLIST.md)：各发布项标记为 `verified`、`manual`、`optional` 或 `blocked`，明确 0.1.0/pre-1.0 门槛。

## 当前代码与 Git 状态

最新已验证功能提交：`ec1ad6f4e99526205ebb749ef472e7baef1fbcab`。前一 Phase 15 工程提交为 `fe6a75a644393042be206dcf377f8ab8f2d85525`。HEAD 位于 `main`；此前核实本地领先 `origin/main` 两个提交，尚未 push。交接快照自身提交请从 `git log -1` 查询，避免 `STATE.json` 自引用，遵守 D-006。

## 验证结果

- 功能提交上的默认完整离线套件：394 passed、5 个 live/large-book 显式测试 deselected、10 subtests passed、7 个既有依赖 warning，53.38 秒。
- 临时全新 `git clone --no-local`：创建新虚拟环境，按锁文件安装 Python 与 npm 依赖，Web typecheck/build、doctor、Mock MP3/M4B、ffprobe 两个章节标记及 `validate_project.py` 全通过。该次本地 clone 使用 CPython 3.14.7；CI 明确固定 Python 3.12。
- CI 冒烟在空配置的临时中文路径运行：Mock MP3 103725 字节，M4B 74210 字节，ffprobe 读到 2 章；没有网络 AI 调用。
- 完整合法样本：核对既有 Gutenberg 3300 获取元数据标为 `public_domain`、jurisdiction `US`，源文件 SHA-256 匹配，2,468,951 字节、67 章。隔离复制后 Mock 全流水线 1507 个步骤成功；完成态 resume 的文件字节增长为 0；导出的 M4B 有 20 个节目 segment 章节，Job 文件总量 17,701,350 字节。恢复时没有新增外部 LLM/TTS 调用。
- `validate_project.py`、`compileall`、`uv lock --check`、CI YAML 解析、`git diff HEAD^ HEAD --check` 通过。
- 全新目录直接从官方 Gutenberg 重新 acquisition 的 opt-in 测试未能通过：本机代理/DNS 返回非公开来源地址，安全下载器按规则拒绝请求；未下载文件、未调用服务。已记录为 manual，待公开 DNS 环境重跑。

## 未解决问题与下一步

1. 推送或创建 PR 后核对 GitHub Actions 的三个远端 job；本阶段没有 push，因此不声称 GitHub 上已有通过结果。
2. 维护者需选择 BookCast 项目许可证，并决定必需依赖 PyMuPDF 的 AGPL/商业/替代路径。Kokoro bundle 中 `espeak-ng-data` 的独立许可和所需 notices 尚未确认；相关分发项保持 blocked。
3. 在正常公共 DNS 网络运行 `BOOKCAST_RUN_RELEASE_LARGE_BOOK=1 uv run pytest -m large_model tests/test_release_large_book.py -v`，验证从官方目录重新获取后的长书完整测试。
4. 发布前执行独立 secret scan，并对目标平台 FFmpeg build flags、Python/npm 二进制与模型资产重新审计。

不要重复下载或推理 Qwen/Gemini/DeepSeek；不要用 ignored `imports/`、`output/`、`data/` 作为 CI fixture；不要把临时长书成功等同真实模型质量或真人试听结论。Qwen 人工试听仍 pending，继续 experimental。
