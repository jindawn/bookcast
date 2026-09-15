# 书名识别与合法来源获取

Phase 3 的入口是 `bookcast acquire`。默认流程为书名 → 目录候选 → 明确版本 → 书籍版权依据 → 安全获取 → 原有本地 Parser。默认不调用 AI、不生成音频，不开发 UI，也不抓取普通搜索网页。

Phase 7 增加 [Web 客户端](WEB.md)，其书名检索与获取复用本指南的 Core。候选必须手动选择，UI 不接受任意下载 URL 或 DNS 覆盖；网络环境需要显式 --resolve 时，先通过 CLI 安全获取，再从页面上传本地结果。

## 运行

```sh
uv run bookcast acquire "The Wealth of Nations" --list
uv run bookcast acquire "The Wealth of Nations" --edition gutenberg:3300
uv run bookcast acquire "The Wealth of Nations" --edition gutenberg:3300 --generate
```

`--list` 只取得目录候选，不下载书籍。多个候选或超过 50 项时，不自动挑最热门版本；用 `--author`、`--language` 缩小结果，或用 `--edition` 选择本次列表中的 ID。无匹配结果时提示用户提供自己的文件/URL。不完整标题按大小写无关的词项匹配；当前没有拼写纠错、语义搜索或跨语种翻译。

获取产物默认在 `imports/{acquisition_id}/`，包含 `source/input.txt`（或 epub/pdf）、`acquisition.json`、`metadata.json` 和 `chapters/`。ID 由候选身份和来源生成，文件名由程序固定，不使用远端文件名或书名构造路径。

重复相同获取命令会校验并复用下载与解析检查点。`--generate` 将取得的本地文件、身份和来源记录注入原有 Pipeline，音频输出到 `output/{book_id}/`；`--pipeline-output-dir` 可另选目录。音频阶段失败或切换 AI 配置时加 `--resume`；LLM/TTS 的 `--provider`、`--tts-provider`、`--config` 与 generate 命令一致。默认仍是 Mock 音调。

## 身份和版权依据

`BookIdentity` 包含 title、authors、language、isbn、edition、publication_year。Gutenberg 目录一般不提供印刷版本的 ISBN、版次和原始出版年份，这些字段保存 null；`catalog_release_date` 单独保存电子目录发行日期。不同目录 ID 即使同名也不合并。目录身份不能保证精确对应某个印刷版，详见 [官方元数据说明](https://www.gutenberg.org/ebooks/offline_catalogs.html)。

内置 `GutenbergSourceProvider` 使用官方机器可读 CSV 搜索，并从所选 ID 的 RDF 读取书籍层 `dcterms:rights`、身份和可用 TXT 格式。仅接受书籍声明 `Public domain in the USA.`；未知或仍有版权的条目停止。RDF 顶层的 CC0 许可属于元数据，不能授权下载书籍。

来源记录包含版权声明、证据 URL、RDF SHA-256 和 jurisdiction=US。该判断限定在来源明确声明的美国公有领域，不表示全球通用结论；CLI 会展示地区范围。RDF 原文按内容哈希保存在目录缓存中，不覆盖同一本书旧的证据快照。

书籍只从 Project Gutenberg [官方镜像清单](https://www.gutenberg.org/MIRRORS.ALL) 中的 `https://gutenberg.pglaf.org` 获取。首个适配器仅支持镜像生成的 UTF-8 TXT，并检查文本头部 eBook 编号与候选相符。采用机器可读目录和镜像符合其 [自动访问说明](https://www.gutenberg.org/policy/robot_access.html)，不抓取主站网页搜索结果。

## 用户来源

```sh
uv run bookcast acquire "我的书" --file ./books/my-book.epub
uv run bookcast acquire "授权资料" --url https://publisher.example/book.pdf --format pdf --rights-confirmed
```

第二行的域名是示例占位符。`--url` 必须是用户有权获取的直接文件链接，明确指定格式并确认使用权；没有确认时不会请求文件。网站公开可访问、来自官方网站或用户勾选确认都不构成程序独立核实的版权结论。记录 `metadata_origin=user` 和 `rights_category=user_provided`，不伪装成已验证的开放许可。

本地文件通过格式与大小校验后复制，原件不修改；导入过程中内容发生变化会停止。URL 与本地文件不能同时指定。未知书籍可走这一入口，不会转向未经授权的下载站。

`SourceOffer` 契约预留 public_domain、open_license、official、user_provided、unknown；现有适配器为 Gutenberg 与用户来源。专门的开放许可库、出版社官方资源目录等适配器尚未实现。

## 下载与容器安全

- 只用 HTTPS/443 GET，不发送登录凭证、Cookie，不使用环境代理。URL 不接受内嵌账号、query、fragment、反斜杠或控制字符；暂不支持签名 URL、登录态下载和非标准端口。
- DNS 返回地址必须全部为公网地址；连接固定到验证后的 IP，同时保持原域名 SNI 与证书校验。每次重定向重新检查，最多三次；拒绝私网、loopback、link-local 等地址。
- 书籍默认上限 32 MiB，可用 `--max-size-mb` 调整到 1–100 MiB；CSV 目录单独限制 64 MiB，RDF 限制 1 MiB。既检查 Content-Length，也检查实际读取量；缺少长度头仍受限。拒绝空文件、截断响应和 HTTP 压缩响应。
- EPUB/PDF/TXT 需要对应 MIME，不能以 application/octet-stream 冒充书籍。官方 CSV/RDF 的 octet-stream 只在元数据请求中接受，并额外验证结构。
- TXT 检查 UTF-8/带 BOM UTF-16、二进制控制字符及伪装 HTML；PDF 检查签名并交给原 Parser；EPUB 检查容器标识、路径、加密标志、符号链接和实体声明，最多 5,000 条目、64 MiB 总展开大小、200 倍压缩比。不解压执行内容，原解析器只提取文本。
- 先写临时文件并验证，通过后原子替换。下载失败不发布半文件；有记录的损坏产物可重建，非空无记录目录及路径中的符号链接会拒绝覆盖。

连接与读操作默认 20 秒超时，读取期间检查默认 120 秒总时限；`--download-timeout` 可设 1–900 秒。慢网络首次获取较大的目录可用 600 秒。总时限在读取块之间检查，正在进行的网络操作仍由连接/读超时约束。

如果系统 DNS 返回 VPN/Fake-IP 的 `198.18.*` 等保留地址，默认会拒绝。可以先独立核验真实公网 IP，再显式指定 `--resolve 主机名=公网IP`；选项不接受私网或保留地址，不关闭 TLS 校验，也不会用于其他重定向主机。地址可能改变，不要把演示中的 IP 当成永久配置。

## 缓存、恢复与扩展

CSV 保存在 `imports/.catalog/gutenberg/`（使用 --output-dir 时相对于该目录），初次下载约 21 MB；后续校验哈希后本地搜索。`--refresh-catalog` 显式更新目录，不自动降级为过期或未经验证的下载内容。每次选择版本后仍重新读取 RDF 核验书籍版权；没有网络时可使用已有本地文件入口。

获取记录的独立 v1 契约定义在 [source_api.py](../src/bookcast/source_api.py)，状态为 pending/downloading/downloaded/parsing/parsed/failed。成功下载后立即记录 URL/MIME/大小/SHA-256，解析成功记录每个 JSON 的哈希。解析中断不重复下载，损坏章节重建解析结果；下载中断重试整个文件，不支持 HTTP Range 续传。

`BookSourceProvider.search()` 返回独立候选与结果是否完整，`sources()` 返回带使用依据的来源候选。通过 `SourceRegistry.register()` 接入工厂，不修改 AI Pipeline。后续适配器应明确地区/许可条件，不能将任意错误、未知版权或缺失身份静默视为成功。

运行 manifest v2 仍用于音频流水线，仓库 `docs/STATE.json` 仍用于 Agent 接力；二者都不是 acquisition.json。可复现的真实演示证据见 [PHASE3_DEMO.md](PHASE3_DEMO.md)。

Phase 5 的音频 Job 会持久化导入副本、来源 metadata seed 和无密钥 Provider 配置。获取之后的音频中断可直接使用 `bookcast resume JOB_ID`，无需重新联网获取；永久失败修复后用 `retry`。获取任务本身仍使用 acquisition v1，恢复命令和边界见 [JOBS.md](JOBS.md)。
