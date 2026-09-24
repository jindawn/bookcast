# 给下一位 Coding Agent

更新时间：2026-09-24T00:09:21Z。请按 [AGENTS.md](../AGENTS.md) 读取 README、架构、路线图、STATE，再核对实际代码与 Git。审计详情在 [PHASE16_AUDIT.md](PHASE16_AUDIT.md)；聊天记录不是接手依据。

## 当前目标与状态

Phase 16 V1 前全仓审计已完成，当前没有正在执行的开发任务。基线是 `eb3bc6c64ebccb6e9b552a18a57e067e529c3a8e`；确认 Critical 0、High 3 并全部修复，Medium 7、Low 2 记录为后续 backlog。BookCast 保持 `0.1.0` / pre-1.0，**尚不具备可再分发 V1 release candidate 条件**：缺项目 LICENSE，PyMuPDF 许可路径与 Kokoro 包内 `espeak-ng-data` 通知未定；实验 Qwen extra 还有锁定依赖公告。不得把技术测试通过等同分发许可或真人音质验收。

## 刚完成的工作与关键文件

- [`src/bookcast/pipeline.py`](../src/bookcast/pipeline.py) / [`tests/test_sources.py`](../tests/test_sources.py)：直接 CLI 生成先限制 100 MiB、校验导入文件，旧 parse 缓存 resume 也必须通过来源校验；恶意 EPUB 和超大 TXT 反例已回归。
- [`src/bookcast/tts_setup.py`](../src/bookcast/tts_setup.py) / [`tests/test_tts.py`](../tests/test_tts.py)：Kokoro 官方归属由独立固定资产 fingerprint 核对，不能仅凭可编辑收据自证；已核对现有官方 archive 和 377 个安装资产，无下载/推理。
- [`src/bookcast/adapters/qwen_assets.py`](../src/bookcast/adapters/qwen_assets.py) / [`tests/test_qwen_tts.py`](../tests/test_qwen_tts.py)：固定 12 项模型资产，拒绝可被加载的额外配置、目录及 symlink；已核对现有 12 个固定资产，无推理。
- [`docs/PHASE16_AUDIT.md`](PHASE16_AUDIT.md)：各等级证据、文件、复现、风险、最小修复建议与正常路径检查。 [`docs/RELEASE_CHECKLIST.md`](RELEASE_CHECKLIST.md) 和 [`docs/ROADMAP.md`](ROADMAP.md) 已同步发布门槛；本快照更新 STATE/WORKLOG。

## 当前代码与 Git 状态

最新已验证功能提交是 `acecfbfc336112d2a3fc226cd3ed334a6bfc0fa4`（`fix: close Phase 16 source and model integrity gaps`）。当前分支为 `main`，本地功能提交未推送；此前核对 `origin/main` 为 `eb3bc6c64ebccb6e9b552a18a57e067e529c3a8e`。交接快照自身提交请用 `git log -1` 读取，遵循 D-006，不让 STATE 自引用。接手时重新执行 `git status --short --branch`，不要把本段 Git 描述视为实时状态。没有新增 Provider 或产品功能，没有改变既有音频。

## 已运行测试与结果

- 修复后默认完整离线 `pytest -q`：**398 passed、5 deselected、10 subtests、7 个既有依赖 warning**；基线为 394 passed。Source/HTTP/Web 专项 100 passed，Qwen/TTS 专项 13 passed。
- Web `typecheck`、`build`、Playwright **3 passed**；首次浏览器运行被沙箱阻止绑定本地端口，授权测试端口后通过。
- `python3 scripts/validate_project.py`、`compileall`、`uv lock --check`、`git diff --check` 通过；首轮 lock 检查受沙箱缓存权限阻止，授权只读缓存后通过。
- GitHub Actions 在已推送基线 `eb3bc6c` 上 Python Core、Web、Static validation 三个 job 成功：[远端运行](https://github.com/jindawn/bookcast/actions/runs/35830514918)。本次新提交未推送，所以没有其远端 CI 结果。
- 官方 npm registry 对完整 Web lock 为 0 公告；`uv audit --locked --no-extra qwen` 对 36 包为 0。`uv audit --locked` 对可选 Qwen extra 报 14 条 OSV 记录（含别名），见审计 M-07，**未通过全可选依赖安全门槛**。
- 秘密扫描未发现活动凭证值进入 Git、manifest、日志或当前 Web 构建产物。旧的忽略 `.next/cache` 曾命中一个环境值；清理该生成缓存并用随机假凭证全新构建后未复现。不要把 `.next` 打入发行包。
- 离线故障注入覆盖 ENOSPC、rename、FFmpeg merge 恢复；已完成的 11 条 AI Attempt 保留，无重复 AI 调用。本阶段未调用 DeepSeek/Gemini，也未下载或推理 Kokoro/Qwen。

## 未解决问题与下一步

1. 维护者确定 BookCast LICENSE、PyMuPDF AGPL/商业/替代路径，确认 Kokoro `espeak-ng-data` 许可/通知；在此之前可继续开发但不得发布可再分发 V1 包。
2. 逐项处理审计报告 M-01～M-07、L-01～L-02。M-06 是服务调用返回后、Attempt 提交前的窄崩溃窗口，当前不能承诺“恰好一次”计费。Qwen extra 上游公告解决和本机兼容验证前保持 experimental、不可进入默认发行组件。
3. 在公共 DNS 正常的环境显式运行 `BOOKCAST_RUN_RELEASE_LARGE_BOOK=1 uv run pytest -m large_model tests/test_release_large_book.py -v`，复验全新官方目录获取；本机代理/DNS 把 Gutenberg 域名解析为非公开地址，下载器正确拒绝。
4. 若将本次提交推送，再核对新 HEAD 的三个 GitHub Actions job；发布前复查目标平台 FFmpeg notices、最终包与 Web 产物的 secret scan。不要把基线 CI 成功冒充新提交 CI 成功。

不要重复生成已有 DeepSeek/Gemini/Kokoro/Qwen 任务，不要下载 4.5GB Qwen 模型，也不要用忽略的 `data/`、`imports/` 或 `output/` 作为 CI fixture。Qwen 真人试听仍 pending，不能将它提升为默认 quality Provider。
