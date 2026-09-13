# 给下一位 AI Agent 的交接

快照更新：2026-09-13T23:28:15Z。Phase 3 已完成，功能提交已验证，交接快照已同步；状态见 [STATE.json](STATE.json)。

## 当前目标

从书名识别候选，明确版本与来源依据，再安全获取并解析；可选接入原有 Pipeline。本阶段不开发 UI、盗版爬虫、访问控制绕过或真实音频功能。

## 刚刚完成了什么

- 建立 BookIdentity（title/authors/language/isbn/edition/publication_year）、EditionCandidate、SourceOffer、BookSourceProvider 和 Source Registry。
- 从 Gutenberg 官方镜像取得 CSV 目录并缓存；查询所选条目的 RDF，区分书籍版权声明与元数据 CC0。首批只接受明确声明美国公有领域的条目，下载 UTF-8 TXT 并核对头部 eBook 编号。
- 多候选必须显式 --edition，可按作者/语言筛选；未知 ISBN/印刷版次/出版年份不猜填，目录发行日期单列。
- 用户 --url 需要格式和 --rights-confirmed；本地 --file 支持 EPUB/PDF/TXT。身份及使用权声明标记为用户提供，不冒充独立核验。
- 下载检查 MIME、声明/实际大小、公开 IP、TLS 和重定向；拒绝私网、异常压缩、路径穿越、实体声明和不安全容器。不会执行下载内容或将服务文件名用作本地路径。
- acquisition.json v1 持久化下载和解析阶段，重复获取校验哈希后复用；解析失败不重下书籍。acquire --generate 将来源 metadata seed 注入既有 Pipeline，默认仍是 Mock 音调。
- 实际查询 The Wealth of Nations 得到 gutenberg:3300 / gutenberg:38194 两个候选；显式选择 3300，成功下载 2,468,951 字节并解析 67 个文本段。重复获取时文件、解析结果和获取记录的字节/mtime 均不变。

## 修改的关键文件

- [source_api.py](../src/bookcast/source_api.py)、[sources.py](../src/bookcast/sources.py)：中立身份/来源契约、官方与用户 Adapter、Registry。
- [source_http.py](../src/bookcast/source_http.py)、[source_validation.py](../src/bookcast/source_validation.py)：有界 HTTPS 与容器安全检查。
- [acquisition.py](../src/bookcast/acquisition.py)：版本选择、导入、解析和检查点。
- [cli.py](../src/bookcast/cli.py)、[models.py](../src/bookcast/models.py)、[pipeline.py](../src/bookcast/pipeline.py)：acquire 命令及来源元数据桥接；旧 AI 契约未变。
- [test_sources.py](../tests/test_sources.py)、[test_source_http.py](../tests/test_source_http.py)：82 项新增离线测试场景。
- [SOURCES.md](SOURCES.md)、[PHASE3_DEMO.md](PHASE3_DEMO.md)、README、CONTEXT、PRODUCT、ARCHITECTURE、ROADMAP、DECISIONS（D-011/D-012）、STATE、HANDOFF、WORKLOG：完整接力记录。

## 当前代码状态与运行方法

```sh
uv sync --extra dev
uv run bookcast acquire "The Wealth of Nations" --list
uv run bookcast acquire "The Wealth of Nations" --edition gutenberg:3300
uv run bookcast acquire "自己的书" --file ./books/own.epub --generate
uv run pytest -q
python3 scripts/validate_project.py
```

默认获取目录 imports，音频目录 output。acquire 默认只到解析；加 --generate 才调用 AI/TTS。获取检查点自动复用，音频失败或配置变化后加 --resume。详见 SOURCES。

本次运行环境将官方镜像解析为 Fake-IP 198.18.0.37，安全校验按设计拒绝。经公开 DNS 查询确认公网 IP 69.55.231.8 后，使用 --resolve gutenberg.pglaf.org=69.55.231.8 保留域名 TLS 校验；首次 21 MB 目录另设 --download-timeout 600。IP 会变化，接手时不要盲用旧值或关闭防护。普通公网 DNS 环境无需该选项。

真实产物在本机 imports/phase3-demo/7e7a23a67db5bbc3d961bd5c/，被 Git 忽略。可复现命令、版权依据、源文件/目录/RDF 哈希见 PHASE3_DEMO；仓库没有提交书籍全文或生成产物。

## 已运行测试及结果

- `.venv/bin/python -m pytest -q`：160 passed、10 subtests passed；5 个既有 PyMuPDF SWIG 弃用警告。全部自动化测试离线。
- `.venv/bin/python -m compileall -q src tests`、`git diff --check`：通过。python3 scripts/validate_project.py 通过，具体证据见 STATE。
- 真实 demo：官方目录 → 两个候选 → 显式选择 → RDF 资格 → 下载 → 原 Parser，status=parsed、coverage=complete、67 段、2,432,512 字符。
- 真实重复获取及所有产物 SHA-256 核对通过；本地自制书 acquire --generate → Mock MP3 → --resume 通过，来源元数据保留。
- 安全场景包含大小头缺失/伪造、截断、错误 MIME、伪装 HTML、私网/保留 IP、重定向、TLS 固定地址、ZIP 路径/符号链接/异常压缩、DTD/实体、源文件复制时变化和损坏检查点。

## 未解决问题与技术债

- 当前无未解决代码测试失败或 blocker。功能提交为 9bdf53ca5aef40b710142ba332849390592f6422，本次提交尚未推送。首次测试失败来自自制 EPUB 缺少目录资源，已修正。
- Gutenberg 是目录条目匹配，不能据此保证特定印刷版；ISBN、版次、原出版年份没有可靠元数据时为空。
- 专门开放许可库和其他出版社目录尚未接入；用户 URL 的权限由用户声明，程序只校验传输与格式。
- Gutenberg 首批仅 TXT，版权来源范围为美国；其他地区条件仍需核对。67 个解析段包含前后附文，未做目录层级/内容质量验收。
- 默认 HTTPS/443、无 query/fragment/认证/代理；私网服务、签名 URL 和部分复杂 EPUB 会被拒绝。首次目录较大，慢网络需调高有界时限。
- 获取失败不支持 HTTP Range 断点续传，下载重试整个文件；解析阶段复用下载。真实模型/TTS、长章节、OCR、M4B、CI、许可证及跨平台验证仍为后续事项。
- 之前一次联网审批因 Codex 额度被拒绝，用户继续且额度重置后已获准完成；该事项现在不是 blocker。

## 下一步建议

1. 按 AGENTS 核对代码、Git 和 STATE；当前无进行中任务，下一阶段范围等待用户确认。
2. 用户确认范围后，可选择增加有明确许可依据的 Source Adapter，或推进真实 LLM/TTS 与内容质量验收。
3. 后续仍须保留身份歧义、来源证据、已有 AI 恢复和安全下载边界；当前没有开始下一阶段或 UI。

## 不要重复做的事情

- 不重建 Phase 0–2，不将取得一本公开书籍泛化成全目录验收。
- 不以元数据 CC0 代替书籍授权，不猜填版次，不静默选择多候选。
- 不关闭公网/TLS 校验来适应 Fake-IP，不绕过 DRM、付费墙或访问控制。
- 不提交 imports/output、书籍、密钥或生成音频，不删除用户原件。
- 不因来源 Adapter 或 AI Provider 切换重做已有有效成果；不追逐 STATE 自引用哈希。

## 最近 Git commit

- 接手时 HEAD 为 b6f0aa9（Phase 2 交接）；其功能提交为 2f06b0107af9470f6a8758b8a1620ac8ab8f7cbe。
- Phase 3 功能提交：`9bdf53ca5aef40b710142ba332849390592f6422`（feat: add legal book source resolver and safe acquisition），已在该提交上运行 160 项测试、文档校验和提交差异检查，全部通过。
- 最终快照自身通过 `git log -1 --oneline` 查询，遵守 D-006。
