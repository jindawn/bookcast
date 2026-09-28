# 《传习录》Web Job EPUB 前置页与 schema_error 离线调查

调查日期：2026-09-28。功能 checkpoint `d058d6aa5b306c8f7b7e3347d312d6e583f90a7a`。**未调用真实 API，未 generate/retry 原 Job，未改生产路由。** 原始上传和失败 Job 保留在本地 `data/web/`，未纳入 Git。

## 同一输入与失败点

上传 `data/web/uploads/f7c1e5eb07cc417c98f4374fd1445fea/傳習錄.epub` 的 SHA-256 为 `582ba8e06af7386d187d548be2535b385a0b551ccdce5707e4aebafaed206a39`，与 Job `8c6c566ad0db4619987489160ec878f8` 的 `source_sha256` 相同。失败 manifest 位于 `data/web/jobs/8be6ca16cc3a4954b58adb1e9adf324d/output/3140fa05caf511dfbe087b29/manifest.json`。

原解析的六个 spine 文档都没有 EPUB `properties`，且原 `structural_rule` 均返回 `None`，因而全部进入 chapter list：

| spine index / idref / href | DOM 与内容判据 | 原 chapter |
| --- | --- | --- |
| 1 / `title` / `title.xhtml` | 标题 + 导出日期/来源声明，22 字，无正文段落 | 0001，误收录 |
| 2 / `c0_chuan_xi_lu` / `c0_chuan_xi_lu.xhtml` | 10 个链接、其中 3 个指向卷文档，无正文段落，48 字 | 0002，误收录 |
| 3 / `c1_chuan_xi_lu_juan_shang` / 同名 `.xhtml` | 正文段落 383，28,765 字 | 0003，保留 |
| 4 / `c2_chuan_xi_lu_juan_zhong` / 同名 `.xhtml` | 正文段落 176，36,464 字 | 0004，保留 |
| 5 / `c3_chuan_xi_lu_juan_xia` / 同名 `.xhtml` | 正文段落 396，27,013 字 | 0005，保留 |
| 6 / `about` / `about.xhtml` | 数字版本来源/许可说明，1,105 字 | 0006，误收录 |

另有非 spine 的 `nav.xhtml`，已有 `EpubNav` 规则正确过滤。所有六个 spine item 均为 `linear=yes`；仅凭 spine/linear 不能判断正文。

真正失败的 Step/Attempt 是 `analysis:0001:0001`，Qwen / `qwen3.7-flash`，永久 `schema_error`。对应 physical request **HTTP 200**、`succeeded`，有 input 860/output 145 token，未发生物理重试。Attempt/events 的安全校验信息为 `ValidationError`、字段 `core_ideas`、原因 `too_short`、`finish_reason=stop`。期望 schema 是 `EvidenceAnalysis`，其中 `core_ideas` 为最少 1 项的 `EvidenceFinding` 列表。原始模型正文未保存，不能还原完整响应 JSON，也不能将这次失败笼统归为网络或模型偶发。

**结论边界：** parser 误把非正文标题页排为第 0001 章，直接使本次失败的分析请求落在该 22 字页面，属于已证实的上游输入错误；HTTP 200 后 `core_ideas` 长度校验失败是直接错误。不能仅凭现有产物证明 Qwen 在修正后的正文 0001 上一定成功，需后续单独授权的真实 Job 才能验证；本轮没有重试。

## 最小通用修复与离线回归

`source_sanitation.structural_rule` 继续优先使用 EPUB `nav` / `cover-image` properties 和现有资源名规则；新增三个组合语义：有标题且仅为导出来源说明的标题页、无正文段落而包含多个卷文档链接的密集索引页、明确写明数字版本来源/许可的说明页。**没有按书名、22/48 字或“所有短章”过滤。** `SOURCE_FILTER_VERSION` 升至 v2，使未来显式恢复/重新解析可识别规则变化。原失败 Job 的 manifest/产物未修改。

`tests/test_phase1.py` 离线构造并读取真正的 EPUB 容器，重现标题页→链接索引→正文→数字版本说明的 spine 结构；确认三类非正文被过滤，同时保留有段落的短正文和无 `<p>` 的短诗。对原始上传 EPUB **只读**重新调用 parser，得到 3 个正文 chapter（28,765 / 36,464 / 27,013 字），过滤 `title.xhtml`、`c0_chuan_xi_lu.xhtml`、`about.xhtml` 与 `nav.xhtml`。

验证：Phase 1 targeted **12 passed**；完整离线 pytest **764 passed、1 skipped、7 deselected、0 failed、10 subtests passed**；`compileall`、项目校验、`git diff --check` 均通过。没有模型请求、音频、Job 恢复或 push。
