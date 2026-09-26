# 本地 Web 与 Application API

Phase 14 首页从同源 `/api/providers` 的安全摘要显示当前优先级链中首个可用的 LLM/TTS、模型、本地/云端/实验性、是否真实人声和缺失步骤。未配置时明确标注 Mock 测试音调；需要 Key 时只给出环境变量名与本机终端操作提示。Web 不接收、不持久化 Provider Secret；请先运行 `bookcast setup` 与 `bookcast doctor --human`，再用同一配置启动 `bookcast serve`。`doctor` 默认 JSON 契约保持不变。

Phase 7 的 Next.js 界面只负责表单、状态和播放。FastAPI 将请求交给已有 Core；没有前端 AI SDK，也没有第二套解析、下载、生成或缓存算法。CLI 和 Skill 继续独立可用。

## 运行与测试

需要 Python 3.12+、FFmpeg、Node.js 20.9+；本机验证 Node 22.22.3。仓库根目录运行：

```sh
uv sync --extra dev --extra web
npm --prefix web ci
npm --prefix web run build
.venv/bin/bookcast serve --port 8765 --data-dir data/web --ui-dir web/out
```

访问 http://127.0.0.1:8765。构建后只需一个 Python 服务，Node 不是生成任务的运行依赖。页面未打入 Python wheel，需从仓库构建或用 --ui-dir 指向构建结果。修改页面后重新 build；未构建时 `/` 返回带提示的 503，API 仍可运行。

```sh
.venv/bin/python -m pytest tests/test_web.py -q
.venv/bin/python -m pytest -q
npm --prefix web run typecheck
npm --prefix web exec -- playwright install chromium
npm --prefix web run test:e2e
```

E2E 使用已构建页面、真实 API/worker/FFmpeg、临时数据目录和 8877 端口，不占用用户数据。首次安装依赖与浏览器需联网，自动测试使用自制样本，无 API key 或公网书籍依赖。截图与失败 trace 位于忽略的 web/test-results。

## 用户流程与范围

- 上传有权处理的 EPUB/PDF/TXT，最大 32 MiB；MIME、签名与 EPUB 容器交给 Core 验证。扫描 PDF 尚无 OCR。
- 书名搜索仅用 Gutenberg 目录。每个候选均需手动选择；作者、语言、版次、年份未知时明确保留。中文译名未必匹配，首次需下载目录。获取前仍由 Core 验证书籍版权依据；美国公有领域声明不代表全球授权。
- 模式为 summary/deep_read/two_host；1–120 分钟整数控制脚本预算，新建 Web 任务默认 20 分钟。默认 Mock 为测试音调；按 [TTS.md](TTS.md) 安装后，用 `bookcast serve --config data/tts-local.toml` 启用中文人声。播放区显示实际音频类型和秒数，预算不保证播放时长。
- 显示阶段、章节、Provider、完成/剩余、最近错误、Core ID/目录、质量与解析警告。规划前的总步骤量并不固定。
- 完成任务保留 MP3 播放与下载；用户通过 CLI 显式导出 M4B 后，且产物与源音频哈希仍有效时，页面额外显示 M4B 下载。浏览器不编码音频，也不自动调用 Provider。API 为 `GET /api/jobs/{id}/audio.m4b`。
- 书架只显示当前 Web 工作空间的提交；旧 CLI output 任务继续用 CLI 管理。新提交使用独立输出根，不同模式不会冲突。
- Provider 显示 Registry 的 health_check 状态、能力与安全错误分类。服务可用性不证明真实内容质量；端点/模型优先级由本地 TOML 配置，外部服务可能接收书稿。

## 持久化提交与执行

```text
Browser (Next static)
  → FastAPI / WebService
    → submission.json
    → detached python -m bookcast.web_worker
      → Core Acquirer / shared composition / Pipeline
        → acquisition.json / manifest.json / artifacts / podcast.mp3
```

`data/web/uploads/{id}` 保存安全文件名与上传凭据；`searches/{id}.json` 保存服务端检索结果，客户端只提交 ID。`imports` 是 Core 获取缓存。`jobs/{web_id}/submission.json` 版本 1 保存请求、候选、无密钥配置快照与派发状态；`jobs/{web_id}/output/{book_id}` 保持原 Core 产物结构。Web ID、Core Job ID、book_id 不可混用。

提交先原子落盘再派发。Idempotency-Key 是客户端生成的 32 位十六进制 ID：相同 ID/参数返回已有提交，不同参数拒绝。响应丢失时用原 key 重发，避免重复任务。未派发记录可手动恢复；每工作空间最多两个 Web worker，容量不足返回 409，但提交保留在书架等待恢复。

worker 对提交目录持内核锁，Core 对产物目录持自己的锁。只有持锁者执行；重启不根据 PID 或时间抢占。Core manifest 存在后，进度、失败、完整性、缓存与恢复均从 Core 投影。外部 CLI 恢复成功或 worker 未及时更新完成标志时，仍以 Core 成功状态为准。

关闭页面不影响任务。Ctrl+C 停止 API 后，已派发 worker 继续；以同一 data-dir 重启 API 可读取结果。电脑重启或 worker 崩溃后，在书架手动恢复。没有自动开机执行、无限重试、取消或删除功能。Web 服务不维护第二份 AI 步骤状态。

## 配置与恢复

```sh
.venv/bin/bookcast serve --config examples/providers.toml
.venv/bin/bookcast jobs --output-dir data/web/jobs --json
.venv/bin/bookcast status /absolute/path/to/core-job --json
.venv/bin/bookcast resume /absolute/path/to/core-job
.venv/bin/bookcast retry /absolute/path/to/core-job --config bookcast.toml
```

新提交读取服务指定的配置并保存快照。恢复使用任务快照，不隐式覆盖为当前目录 TOML。更换端点/模型/优先级时，通过 CLI 使用 Core 目录和显式 --config；变更密钥环境变量后重启 API，使新 worker 继承新环境。浏览器不提供密钥编辑器。

quota/timeout 等可恢复失败点击“从断点恢复”；永久错误先修复，再显式重试，不自动切换模型。损坏音频不可播放，先通过 Core 恢复并重新验证。CLI 修复后 Web 能显示完成结果。上游已完成但本地未落盘的调用仍有重复请求风险，遵循 D-014。

## HTTP 契约与本地边界

| 方法/路径 | 输入与行为 |
| --- | --- |
| GET /api/health | 本地服务与 FFmpeg 状态 |
| POST /api/uploads?filename=... | 原始文件流，匹配格式的 MIME 或 application/octet-stream；返回 upload_id，无需 multipart |
| GET /api/books/search?title=... | 可选 author/language，返回 search_id、候选、complete 与警告 |
| POST /api/jobs | Idempotency-Key；JSON 为 upload_id 或 search_id+edition，以及 mode/minutes；202 |
| GET /api/jobs | Web 任务列表与隔离的损坏记录提示 |
| GET /api/jobs/{web_id} | 状态、进度、目录、警告、音频 URL、可恢复/可重试标志 |
| POST /api/jobs/{web_id}/resume | 恢复可重试失败，永久错误拒绝，完成任务原样返回 |
| POST /api/jobs/{web_id}/retry | 用户修复后显式重试，保留有效检查点 |
| GET /api/jobs/{web_id}/audio | 完整性有效的完成 MP3，支持 Range/206 |
| GET /api/providers | 状态、能力与优先级，无密钥值、端点或上游原始正文 |

错误：参数 422，未知记录 404，冲突/无效源文件/需修复 409，过大文件 413，格式/MIME 不支持 415。损坏记录给出安全提示，原文件保留。浏览器不能提交任意本机路径、URL、shell 参数或 Provider 配置。

serve 固定绑定 127.0.0.1，Host 限制 loopback，Origin 必须同源，跨站 Fetch-Site 拒绝，无 CORS 放行。无 Origin 的本地 HTTP 客户端可用；同机进程属于信任边界。这是单用户本机工具，不是公网多用户服务。

轮询约每 1.5 秒一次，复用 Core 的完整性哈希验证；大书库可能产生较多磁盘 I/O。暂未分页/索引、没有自动调度、旧 CLI 任务导入或多主机支持；这些是后续规模优化事项。

## 验证范围与 Tauri 评估

API 测试验证三模式 Core 生成、来源资格、上传边界、幂等、Range、损坏音频修复、额度/超时/永久错误与真实 SIGKILL。浏览器验证上传到实际播放、刷新历史、真实 quota 失败恢复且首章字节/mtime/调用次数不变、移动布局无横向溢出。候选交互使用 HTTP fixture；真实来源资格/获取委托在 pytest 中使用合成样本，不声称浏览器测试访问了公网 Gutenberg。

Tauri 后续可复用静态页面，并通过 sidecar 分发 Python/FFmpeg。仍需解决各 OS 打包、FFmpeg 分发许可、签名/公证、端口、进程生命周期和更新机制。本阶段选单 Python Web 服务，不安装 Rust、不生成桌面包。

参考：[Next.js 安装](https://nextjs.org/docs/app/getting-started/installation)、[静态导出](https://nextjs.org/docs/app/guides/static-exports)、[Tauri sidecar](https://v2.tauri.app/develop/sidecar/)。框架能力依据官方文档；单服务与推迟桌面包是项目取舍，见 D-015。
