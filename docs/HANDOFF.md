# 给下一位 Coding Agent

更新时间：2026-09-23T04:49:42Z。实际代码和 Git 是权威来源；远端状态请重新核对。

## 当前目标与状态

Phase 14 已完成首次使用与 Provider 配置体验；等待用户授权下一阶段。没有新增 AI Provider，也没有修改 Pipeline、Job、缓存或默认 Mock。Qwen 真人试听仍无评分，继续 experimental。

## 刚完成的工作与关键文件

- `src/bookcast/onboarding.py`、`src/bookcast/cli.py`：`bookcast setup` 列出四个固定方案或排他写入严格校验的本地配置。Demo 明确为测试音调；DeepSeek+Kokoro 是云端 LLM + 本地真实语音；Gemini TTS 要 `--allow-cloud-tts`，Qwen 要 `--allow-experimental` 和已有模型目录。仅 `--install-model` 会下载 Kokoro 官方模型。没有任何 Key 值写入配置。
- `bookcast doctor --human` 增加 ✓/△/✗ 及操作提示；默认 JSON、原字段和退出码保留，新增安全 `onboarding` 摘要。
- `src/bookcast/web_api.py`、`web/app/page.tsx`：首页显示首个可用 LLM/TTS、模型、本地/云端/实验状态、是否真实人声和缺失步骤；网页不接收或保存 Secret。原 Web 创建与播放逻辑不变。
- README 顶部 5 分钟 Quick Start、PROVIDERS/WEB/ARCHITECTURE/ROADMAP 和 `tests/test_onboarding.py`、Playwright 断言同步。

## 当前代码与 Git 状态

已验证功能提交 `c33a2b27da97a1658e345669dedc6a705a7c7943`。交接快照提交自身请用 `git log -1` 获取，避免文档自引用（D-006）。当前已完成 Phase 14 范围；不主动 push。`bookcast.toml`、本地模型、书籍、音频和 Secret 均不应进入 Git。Phase 9 已有真实 DeepSeek+Kokoro 内容/音频技术验收，本阶段没有为 onboarding 重复消耗 API 额度。

## 验证

- 功能提交上完整 pytest：393 passed、4 skipped、10 subtests passed，7 个既有依赖 warning；收费/真实服务测试默认跳过。
- 首次安装：临时隔离克隆（叠加 Phase 14 代码）用 Python 3.12 执行 `uv sync --extra web`，再 `setup --profile demo`、`doctor --human`、自制 TXT Mock 生成；2 章任务完成，MP3 106893 字节。没有调用收费 API 或下载语音模型。最初离线 uv 缓存缺包，正常公共包安装后通过。
- Next.js build/TypeScript 和 Playwright 3 项 E2E 通过；浏览器显示 Mock 测试音调提示，既有上传播放/M4B 下载通过。首次 E2E 沙箱拒绝绑定本地端口，按测试权限重跑成功。
- 专项 24 passed；project validator、compileall、`git diff --check` 通过。最终快照后再检查 validator 与差异。

## 未解决问题、下一步与不要重复做的事

本阶段没有阻塞。新 `setup` 生成的 DeepSeek+Kokoro 路径只做了配置/健康提示的离线回归；本阶段没有再次运行收费 DeepSeek 内容生成或 Kokoro 长音频推理。若需专门验收 onboarding 的真实路径，须用户显式 opt-in 收费调用，并使用新隔离输出目录；现有 Phase 9 真实链路结果不可冒称为本次首次安装验证。

下一阶段由用户决定。可继续收集已有 Kokoro/Gemini/Qwen 三方节目的真人试听评分；没有评分前不要改变默认或称 Qwen 音质优胜。不要重复下载模型、重跑已有真实节目、将 API Key 写入配置/网页/日志，或在未授权时推送远端。
