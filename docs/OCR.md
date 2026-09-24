# 可选本地 OCR 与文档提取（Phase 17）

BookCast 默认沿用原生 TXT/EPUB/PDF 文本解析。扫描 PDF 或图片型 EPUB 只有在用户显式执行 `bookcast generate 文件.pdf --ocr auto`（或 EPUB）时才调用本地 OCR；不发送图片到云端，也不改变 LLM/TTS Provider。`bookcast inspect-document 文件.pdf` 先按页报告 `text`、`image`、`mixed`、`blank`，不调用 OCR。文本 PDF 不会因为启用 `--ocr auto` 而逐页重识别。`--ocr auto` 当前只支持安装了 Swift 的 macOS，使用系统 Apple Vision；其他平台继续使用原生解析，不能启用此选项。

```sh
uv run bookcast inspect-document "/path/扫描件.pdf"
uv run bookcast generate "/path/扫描件.pdf" --ocr auto --mode two_host
uv run bookcast status JOB_ID
uv run bookcast resume JOB_ID
```

`inspect-document` 和显式 OCR 的 PDF/EPUB 提取在独立子进程读取源文件，父进程仅接收受限 JSON，不沿用 LLM/TTS 凭证环境。PDF 最多 100 MiB、2500 页、2000 万提取字符；单次渲染最多 1600 万像素，混合页最多 8 个待识别图像区域；EPUB OCR 图片最多 16 MiB/1600 万像素。只解析本地嵌入图片，不访问书内 URL、不执行 PDF 附件或脚本。损坏文件、资源超限和 OCR 子进程错误不会把原始解析错误正文写入 Job。已有来源容器校验仍在入管线前执行。此隔离不等同于完整 OS 沙箱；本机系统框架和 PyMuPDF 仍需及时更新。

OCR 结果进入原有 `NormalizedBook` 的章节文本，后续 Content/LLM/TTS/Export 保持同一条 Pipeline。每个 `source_blocks` 保留源文件 SHA-256、PDF 物理页码或 EPUB spine 位置、资源定位、归一化区域、`method=ocr` 与 Vision 报告的置信度。原生 PDF 文本块也有页码和区域，EPUB HTML 文本保留资源定位。低于 0.45 的 OCR 块会给出警告，覆盖状态标为 partial；该阈值是复核提示，并非校准后的准确率结论。书稿消费者仍可从原页/图像核对，不应把 OCR 输出当作作者原文的可靠转录。

OCR 设置和适配器实现版本参与 parse Step 指纹。已完成、哈希有效的 parse 及下游步骤在 resume 时复用；同一 Job 不能从原生模式切换到 OCR 或反向切换，需新输出目录。OCR 失败时不会偷偷换成 Mock 或把缺失扫描页报告为完整。当前恢复粒度是整份文档的 parse Step：如果在 OCR 解析过程中强杀且 parse 尚未提交，恢复会重新识别该文档；大型扫描书的逐页 OCR 持久缓存仍属后续可靠性改进，不承诺零重复 OCR 计算。

## 本机候选评估

| 候选 | 许可/安装 | macOS Apple Silicon 与语言 | 布局/限制 | 决定 |
| --- | --- | --- | --- | --- |
| Apple Vision `VNRecognizeTextRequest` | 系统框架；无需第三方模型下载，框架本身不是开源组件 | 本机实际报告 `zh-Hans`、`zh-Hant`、`en-US` 可用；自制中英图片和 90° PDF 实测识别成功 | 返回候选置信度与区域；复杂多栏、表格、脚注尚无系统性质量验收；运行 Swift 工具链 | macOS 显式可选适配器 |
| Tesseract 5 | 引擎 Apache-2.0；中文语言数据需单独安装与核许可 | 官方支持 macOS，CPU 可运行；本机未安装，也未做中文实测 | 需部署语言包、版面和旋转处理；不能据宣传推断优于 Vision | 暂缓生产接入 |
| PyMuPDF 内建 `get_textpage_ocr` | 使用现有 PyMuPDF 与 Tesseract；许可见发布门槛 | 需要额外 Tesseract 资源 | 官方说明不支持 clip，partial OCR 的顺序有局限；在主进程内执行不利于此阶段隔离 | 未采用 |

本阶段没有引入外部模型权重、网络下载或 OCR 专用第二条 Pipeline。复杂多栏阅读顺序、矢量描边文字、手写字、图片质量差及跨平台 OCR 仍可能造成遗漏或错序；用户应检查 warning 和原页。Apple Vision 识别质量只在有限自制 fixture 上通过技术验收，未声称普适准确率。

默认 `pytest` 用假 OCR 适配器覆盖分类、中文输出映射、空白/混合/旋转页、图片 EPUB、坏 PDF 和恢复。macOS 的真实系统识别用 `BOOKCAST_RUN_OCR_LIVE=1 uv run pytest tests/test_ocr.py -k real_chinese -q` 显式运行，不下载模型，也不调用云 API；Swift 首次编译可能写用户缓存。

依据：[Apple Vision 文本识别接口](https://developer.apple.com/documentation/vision/vnrecognizetextrequest)、[Apple Vision 识别语言](https://developer.apple.com/documentation/vision/vnrecognizetextrequest/recognitionlanguages)、[Tesseract 官方仓库与许可](https://github.com/tesseract-ocr/tesseract)、[Tesseract 官方安装说明](https://tesseract-ocr.github.io/tessdoc/Installation.html)、[PyMuPDF OCR 文档](https://pymupdf.readthedocs.io/en/latest/recipes-ocr.html)。
