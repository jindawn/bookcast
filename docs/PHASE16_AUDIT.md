# Phase 16：V1 前全仓审计

审计基线为 2026-09-23 的 `main` HEAD `eb3bc6c64ebccb6e9b552a18a57e067e529c3a8e`（当时与 `origin/main` 相同、工作区干净）。审计按实际代码和离线反例进行；本次只修改确认的 Critical/High，Medium/Low 保留为 backlog。此报告的风险等级表示 **BookCast 实际调用路径** 的风险，不直接等于第三方公告的 CVSS。项目版本仍为 0.1.0/pre-1.0。

## 结论与发布门槛

- Critical：**0**。High：**3，均已用最小改动修复并加入回归测试**。Medium：**7**。Low：**2**。
- 当前**不具备可分发的 V1 release candidate 条件**：BookCast 尚无项目 LICENSE；PyMuPDF 的 AGPL/商业许可路径和 Kokoro bundle 中 `espeak-ng-data` 通知仍未决定。实验 Qwen extra 的锁定依赖另有安全公告，不能把它包装成已完成安全验收的默认发行组件。
- 已推送基线的 [GitHub Actions CI](https://github.com/jindawn/bookcast/actions/runs/35830514918) 在该 SHA 上 Python Core、Web、Static validation 三个 job 均成功；本次新提交尚未推送，不能把基线 CI 结果冒充新提交结果。
- 普通 `pytest` 仍是离线测试；本次没有调用 DeepSeek/Gemini、下载或推理 Kokoro/Qwen，也没有修改既有有效音频。

## High：已修复

| ID | 证据与反例 | 风险 | 最小修复与回归 |
| --- | --- | --- | --- |
| H-01 直接生成绕过源校验 | 原 `Pipeline.generate` 仅哈希/复制并 `parse_book`，而 `validate_source` 只在 acquisition/Web 使用。`tests/test_sources.py` 构造带 `../escape.xhtml` 的可解析 EPUB：修复前直接生成成功；100 MiB+ 稀疏 TXT 也被接收。旧 completed parse checkpoint 可在 resume 时跳过新增解析逻辑。 | 本地 CLI 主入口接受恶意容器、过大文件及未受校验的历史缓存，可能耗尽资源或绕开 DTD/路径约束。 | `src/bookcast/pipeline.py` 哈希前拒绝超 100 MiB，导入副本在解析或重用 parse 缓存前总是 `validate_source`。三项直接输入/旧任务回归覆盖。 |
| H-02 Kokoro 官方收据可伪造 | 原 `verify_model` 只比对模型目录内可编辑的 `bookcast-model.json` 与文件哈希；修改 `model.onnx` 后同步改收据即可通过，同时收据仍写官方 archive SHA。 | 未验证的本地模型目录可被标成固定官方发布资产，破坏安装与缓存来源保证。 | `src/bookcast/tts_setup.py` 增加从已验证固定 SHA 官方 archive 独立导出的文件哈希映射 fingerprint；伪造收据回归在 `tests/test_tts.py`。现有官方包/安装实测匹配 377 个文件，无重下。 |
| H-03 Qwen 未固定的可选文件 | 原 `verify_assets` 只核对 12 个必需文件，容许额外 `processor_config.json`；Transformers 在固定的 `preprocessor_config.json` 之前可能读取它，cache key 却只声明固定资产。 | 同一“官方模型”标识可加载未哈希配置，导致模型行为和缓存归属不可信。 | `src/bookcast/adapters/qwen_assets.py` 拒绝固定清单外文件/目录及符号链接；`tests/test_qwen_tts.py` 用额外 processor 文件和 symlink 回归。既有 12 个官方文件重新验哈通过，无推理。 |

## Medium：backlog（本阶段未修改）

| ID | 证据与复现场景 | 风险 | 最小修复建议 |
| --- | --- | --- | --- |
| M-01 acquisition 版权依据快照过期 | `src/bookcast/acquisition.py:53-54,100-103` 的缓存键不含 rights/evidence；相同候选与本地文件先后用 `Old permission`、`Revised permission` 两个仍 eligible 的 offer 获取，第二次返回已解析任务，`acquisition.json` 保留旧声明。`eligible=false/unknown` 当前会先拒绝。 | 来源归属与当前证据不一致，不会使已拒绝来源变成可下载。 | 把当前 offer/evidence hash 纳入缓存键或在命中时原子更新权限来源，并测试权利状态变更。 |
| M-02 PDF 解析预算仅限文件字节 | `src/bookcast/source_validation.py:30-32` 仅检查 PDF magic/字节，`src/bookcast/parsers.py:94-108` 遍历全部页面并提取文本，无页数或文本总量预算。 | 小体积但大量页/文本的 PDF 可长时间消耗本机 CPU/内存。 | 设置可配置的页数、提取字符数或运行时间上限，并给用户清晰错误与长书例外路径。 |
| M-03 Web 上传中断孤儿 | `src/bookcast/web_api.py:70-93` 上传使用原子临时文件，但 SIGKILL 前无法执行 finally；`uploads/` 没有 Core job 的 resume 清理入口。 | 反复中断上传可能累积磁盘垃圾；不是已提交 Artifact。 | 在上传目录按 BookCast 专属标记做持锁清理，不按文件年龄或模糊通配删除。 |
| M-04 多 API 进程 worker 上限竞态 | `src/bookcast/web_service.py:179-190` 的 `children` 只在当前进程内，跨两个 API 进程在子进程取得 job 锁前都可能看到空闲并各启动 worker。单任务锁仍防止同一 Job 双生成。 | 配置的“最多两个 worker”可能瞬时超额；系统负载不可预测。 | 用持久化/持锁启动预约或单个本机调度器控制全局容量。 |
| M-05 URL path 中的凭证字面值 | `src/bookcast/provider_config.py:102-112` 拒绝 userinfo/query/fragment，但接受 `https://example.invalid/v1/sk-EXAMPLE-SECRET`；`src/bookcast/composition.py:10-11` 会把此路径保存在 manifest provider_settings。 | 误把 token 放在 URL path 会永久写入本地任务记录；环境变量规范无法阻止这种用户误配。 | 更严格的 endpoint path 允许列表/secret-pattern 拒绝，或加载配置时警告并拒绝高概率凭证路径；不可声称识别所有密钥。 |
| M-06 响应已落盘、Attempt 未提交的崩溃窗口 | `src/bookcast/provider_chain.py:71-93` 先由 `invoke` 写产物，随后 `persist/observe(completed)`；若在此间 SIGKILL，只有 running Attempt，下次无法证明服务成功。已完成 Attempt、Step 未提交的相邻窗口可正确复用。 | 极窄窗口可能重复收费；没有厂商端幂等协议，当前不能承诺 exactly-once。 | 为支持幂等键的 Provider 传稳定请求 ID，或增加可严格验证输出与归属的事后恢复记录；保持“最多一次”承诺为未实现。 |
| M-07 实验 Qwen extra 的锁定依赖公告 | `uv audit --locked` 返回 14 条记录（含同一 CVE 的 GHSA/PYSEC 别名），涉及 `accelerate==1.12.0`、`setuptools==81.0.0`、`torch==2.11.0`、`transformers==4.57.3`；`uv audit --locked --no-extra qwen` 对 36 个核心/其他包返回 0。`qwen-tts==0.1.1` 上游固定了部分版本。本项目仅加载本地固定 SHA 资产，不调用公告中的任意 Hub 模型、Trainer、`torch.jit.script` 或 `save_pretrained` 路径。 | Qwen 作为可选 experimental 仍有上游安全债；未来若放宽固定模型边界，公告可能变成可达路径。 | 等待/验证兼容的上游版本并重新做本机推理及人工验收；在此之前不把 Qwen extra 打入默认/可分发 bundle。 |

## Low：backlog

| ID | 证据与复现场景 | 风险 | 最小修复建议 |
| --- | --- | --- | --- |
| L-01 Actions 使用可移动 major tag | `.github/workflows/ci.yml` 的 `checkout@v4`、`setup-python@v5`、`setup-node@v4`、`setup-uv@v6` 未 pin 完整 commit。 | 上游同一 tag 的未来内容变化会改变 CI 运行环境。 | 发布工作流评审后固定 action commit SHA，定期更新。 |
| L-02 HTTP 只尝试首个公网 IP | `src/bookcast/source_http.py:74-76` 对经严格验证的 DNS 地址仅使用排序第一项；该地址不可达而另一公网地址可用时仍失败。 | 来源可用性下降，不构成 SSRF。 | 有界地轮询已全部通过公网校验的地址，每次独立保持 TLS hostname 验证。 |

## 反例与正常路径核对

- **Source/Web**：HTTP 降级、用户信息、编码 CRLF、混合公网/loopback DNS、重定向二次校验、MIME、EPUB traversal/压缩炸弹、候选歧义均按现有边界拒绝；Web 的 Host/Origin/Fetch-Site、文件名、32 MiB、Range=206、幂等与损坏音频隐藏有自动测试。真实 Gutenberg 重新获取仍受本机代理/DNS 限制，未冒充通过。
- **LLM/Jobs**：DeepSeek 兼容层不持久化原始错误正文；reasoning/usage、schema 失败禁止切 Provider、同名配置/prompt 变更缓存失效、quota 接管和已完成 Attempt 恢复有专项测试。实际 SIGKILL、活跃锁与 stale 状态、损坏 Artifact、v1/v2 迁移已有回归。额外注入 ENOSPC 写入/rename：旧文件保持、临时文件清理；注入 FFmpeg merge 失败后显式 retry：11 条完成 AI Attempt 原样保留，随后成功 MP3，无新增 AI Attempt。
- **TTS/Export**：Kokoro unit、Gemini segment、Qwen unit 的配置/voice/style/seed、额度与 SIGKILL 缓存测试保持；WAV/PCM 有格式检查。M4B 的 `read_valid_export` 同时检查最终 M4B、MP3、metadata、plan 与每个 WAV 的哈希；变更 MP3 的现有回归可使旧导出失效。封面仅本地有界 JPEG/PNG，经 ffprobe 解码；章节取实际音频时长；Unicode/旧 Job 已有测试。云端 Gemini 必须在配置中显式 `send_text_to_cloud=true`，TTS 链不允许云端失败后回退 Mock。
- **Secrets/供应链**：修复后扫描 440 个 Git 历史 blob、120 个当前工作区文档/代码文件和 723 份本地任务文本产物，未发现活动凭证值进入 Git/manifest/log；本机忽略目录中的私有凭证文件未读取到仓库，也未删除。首次扫描在旧的、被 Git 忽略的 `.next/cache` 中命中一个环境值，静态 `web/out` 未命中；清理该生成缓存后以随机假凭证做全新构建，假凭证未进入缓存或静态目录，最终 165 份 Web 构建文件无活动凭证命中，因此没有复现当前构建泄漏。发布包仍应排除 `.next`。`npm audit` 使用官方 registry：生产和全 Web lock 均为 0 漏洞；默认镜像的 audit endpoint 返回 404，不能把它当安全结果。Kokoro 固定 archive SHA 与 377 个安装资产 fingerprint、Qwen 12 个资产哈希已在本机重新验证。项目仍没有 LICENSE；PyMuPDF/eSpeak/FFmpeg 分发许可见 [第三方清单](../THIRD_PARTY_NOTICES.md)。

## 最终验证

基线：`pytest -q` 394 passed、5 deselected、10 subtests；远端 Actions 三个 job success。修复后完整离线套件 **398 passed、5 deselected、10 subtests、7 个既有依赖 warning**；Source/HTTP/Web 专项 100 passed，Qwen/TTS 专项 13 passed。Web `typecheck`、`build`、Playwright **3 passed**；`validate_project.py`、`compileall`、`uv lock --check`、`git diff --check` 均通过。首次 Playwright 运行因沙箱禁止绑定本地 8877 端口失败，按测试权限重跑成功；第一次 `uv lock --check` 因沙箱不能访问用户 uv 缓存失败，授权只读访问缓存后通过。收费 live/large-model/manual-listening 均未启用。
