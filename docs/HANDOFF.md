# 给下一位 Coding Agent

更新时间：2026-09-25。先读 AGENTS.md 和项目文档并核对 Git。已验证功能提交：54257b4883b6d82831fa7c20bbbd777bf4c73714；未 push。

## 当前目标和证据

用户报告《狂人日记》DeepSeek `schema_error`。本地两份对应 Web 上传实际是 EPUB（不是 PDF），均在 `analysis:0004:0001` 失败，前 3 章成功。失败段是《鸭的喜剧》。两次都有 DeepSeek `reported_model=deepseek-flash` 和 usage，HTTP 已收到响应。旧实现丢弃了响应解析/Pydantic 的底层异常；未保存原始响应。当前进程没有 DeepSeek key，因此无法还原历史的具体无效字段，不能声称真实 API 已修好。

## 修改和验证

- `src/bookcast/adapters/compatible.py`：确认当前仅发送 `response_format=json_object`，未发 OpenAI `json_schema/strict`；DeepSeek 端补充 JSON schema 类型、空数组和必填项指令。响应错误转为安全的 `error_type`、`validation_field`、`validation_reason`，保留严格 Pydantic 校验。
- `provider_api.py`、`provider_chain.py`、`models.py`、`pipeline.py`：错误元数据沿真实调用链进入 Attempt 和 events，不保存响应内容或密钥。`tests/test_generation.py` 覆盖正常请求、错误字段、显式 retry 保留前章及 OpenAI 不变；已有 malformed 测试仍通过。
- 专项 `tests/test_generation.py tests/test_providers.py` 100 passed；扩展四模块隔离本地配置后 144 passed、1 deselected。未隔离运行有一项既有 CLI 测试失败，因为本机 `bookcast.toml` 选择 DeepSeek 且无 key，和本次改动无关。项目 validator、compileall、diff check 待最终提交前复验。

## 下一步

在有 `DEEPSEEK_API_KEY` 的原服务环境对现存 Web Job 显式 retry。检查 `logs/events.jsonl` 的 `error_type`、`validation_field`、`validation_reason`；若仍失败，针对确切字段继续修复，不要保存/输出原始响应或 key。不要从当前证据推断旧失败的具体字段，不要重新生成前 3 章。任务 ID 见本地 `data/web/jobs`（被 Git 忽略）。其他 Phase17 发布阻塞仍见 STATE.json。

---

# 给下一位 Coding Agent

更新时间：2026-09-24T05:16:45Z。按 [AGENTS.md](../AGENTS.md) 阅读项目文档并核对实际代码与 Git。OCR 详情见 [OCR.md](OCR.md)，架构决定见 D-021；聊天记录不是项目状态。

## 当前目标与代码状态

Phase 17 可选本地 OCR 工程验收完成，V1 原生解析和后续 Content/LLM/TTS/Export 保持原链路。`bookcast inspect-document 文件.pdf` 在隔离进程分类 text/image/mixed/blank；显式 `bookcast generate 文件.pdf --ocr auto`（也支持 EPUB）才在 macOS 使用本地 Apple Vision。未启用 OCR 的扫描 PDF 不会假装完整。BookCast 仍为 0.1.0 / pre-1.0，项目 LICENSE、PyMuPDF 许可路径及 Kokoro espeak-ng-data 通知继续阻止可再分发 V1 包；OCR 不阻塞既有主流程。

## 刚完成的工作与关键文件

- [document_extraction.py](../src/bookcast/document_extraction.py)：中立 OCR 契约、隔离解析/检测及安全错误；[apple_vision_ocr.py](../src/bookcast/adapters/apple_vision_ocr.py)：本机 Vision 中英识别，不传 AI 凭证。
- [pdf_extraction.py](../src/bookcast/pdf_extraction.py)：先分类，再定向识别扫描区域；限制源大小、页数、像素、区域、文本及进程时间。[parsers.py](../src/bookcast/parsers.py)：仅 OCR EPUB 包内图片，不访问文档 URL。
- [models.py](../src/bookcast/models.py) 保留源 SHA、页/资源、区域、置信度；[pipeline.py](../src/bookcast/pipeline.py) 将 OCR 配置及适配器版本纳入 parse 指纹；[cli.py](../src/bookcast/cli.py) 增加显式入口。[test_ocr.py](../tests/test_ocr.py) 覆盖安全反例、恢复和真实 Vision。
- README、PRODUCT、ARCHITECTURE、ROADMAP、DECISIONS、OCR、STATE 和 WORKLOG 已同步。没有安装 Tesseract、下载模型、调用 DeepSeek/Gemini 或重跑已有真实音频。

## 已运行测试及结果

- 默认完整离线 `pytest -q`：410 passed、1 skipped、5 deselected、10 subtests、7 个既有依赖 warning。OCR 模块默认 12 passed、真实系统 1 skipped。
- 显式 `BOOKCAST_RUN_OCR_LIVE=1 .venv/bin/pytest -q tests/test_ocr.py -k real_chinese`：1 passed；自制中文/英文扫描图、90° PDF 与隔离子进程均实际识别。Swift 编译缓存需要本机用户缓存写入权限，无云 API。
- `python3 scripts/validate_project.py`、`compileall`、`git diff --check` 通过；功能提交上已复验项目校验、编译和 `git diff HEAD^ HEAD --check`。完成 parse 后恢复测试确认 Chapter 产物 mtime 未变、无再次 OCR。

## 未解决问题与下一步

1. OCR 仅 macOS/Swift/Apple Vision 显式可用。复杂多栏、表格、脚注、低质扫描、矢量描边字及非 macOS 均未验收；后续应用有权使用的样本人工核对原页，不得将技术成功写成普适准确率。
2. 当前 OCR 恢复粒度是整份文档 parse Step；提交前强杀可能重做识别，提交后 SHA 有效则直接复用。逐页持久缓存需另行设计并沿用 Job/Artifact 状态机。
3. 处理发布许可/资产通知、Phase16 Medium/Low backlog、Qwen 可选依赖公告和公共 DNS 下全新 Gutenberg 获取复验。

## Git 与不要重复做的事情

本阶段接手时 `main`/`origin/main` 同为 `da4ca8e465dfa5037f6c451b2348d689aa5d1638`；Phase16 旧交接的“尚未推送”已过期。最新已验证功能提交为 `3ff4eb360e4fcf9ecfffd7cafa280be4246a769c`（`feat: add opt-in local OCR extraction for PDFs and EPUBs`）；交接快照自身的 HEAD 必须用 `git log -1` 查询，STATE 不自引用。Phase17 未主动 push，远端 CI 不可冒充本地验证。不要重复生成已有 DeepSeek/Gemini/Kokoro/Qwen 任务，不要下载大型模型，不要为了 OCR 改写内容或语音 Provider。
