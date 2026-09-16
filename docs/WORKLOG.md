# 开发工作日志

本文件只追加重要、可验证的开发事实。已写入的历史条目不重写；纠错另加条目。时间使用 UTC，后续条目包含任务、结果、测试与关联提交（若当时已存在）。

## 2026-09-12T21:18:00Z — Phase 0 初始化开始

- 检查工作目录为空，没有已有应用代码、AGENTS.md 或 Git 历史。
- 使用 `git init -b main` 成功创建本地仓库，未配置或操作远程仓库。
- 开始创建统一 Agent 入口、产品与架构规划、交接快照、版本化状态契约及离线校验工具。
- 测试和首个提交尚未完成；具体最终结果在后续条目追加。

## 2026-09-13T02:18:17Z — Phase 0 骨架与校验完成，待基础提交

- 创建全部要求的入口与 docs 文件，补充 CONTEXT 术语表、STATE.schema.json、.gitignore、离线校验脚本及回归测试。
- `python3 scripts/validate_project.py` 通过；`python3 -m unittest discover -s tests -v` 的 20 项测试全部通过。
- Git 忽略抽查：6 个私有路径被忽略、3 个项目与示例路径可跟踪。
- 完成跨文档独立审阅，修正“状态契约变化”措辞并让 GenerationJob 定义覆盖导入阶段。
- 尚无应用入口或业务功能；运行栈和开源许可证待后续决定。当前正在创建首次基础提交。

## 2026-09-13T02:19:26Z — Phase 0 基础提交验证与交接完成

- 已创建基础提交 `60a55951301bed56aa00aa9d8e55a30746bc2fc3`（`chore: initialize BookCast agent-ready project skeleton`），共 15 个文件。
- 在该提交上运行离线项目校验、20 项回归测试和 `git diff --check`，全部通过；提交后工作区干净。
- 更新 STATE 为 Phase 0 completed，记录实际已验证基础提交，清空进行中任务并列出 Phase 1 下一步。
- 更新 HANDOFF、README 和 ROADMAP；记录未选技术栈、未定许可证、未建 CI 及校验器支持范围，不将未实现业务标成已测试能力。
- 本次未实现任何业务管线，未配置远程或发布；交接快照单独提交，其自身哈希按 D-006 通过 Git 查询。

## 2026-09-13T02:38:43Z — Phase 1 MVP 实现与验证

- 采用 Python 3.12+、Typer、Pydantic、EbookLib、BeautifulSoup、PyMuPDF 和 FFmpeg；`uv sync --extra dev` 成功并生成 `uv.lock`。
- 实现 `bookcast generate` / `status`，支持本地 TXT、EPUB、PDF；默认 Mock LLM/TTS 无需 API key，输出章节摘要、双人脚本、测试音调 WAV 和 MP3。
- 实现每步原子落盘、输入/Provider 指纹、SHA-256、进程锁和 `--resume`；损坏产物能被状态命令识别并恢复。
- 新增自制 `examples/example.txt` 和 Phase 1 测试；当前 `uv run pytest -q` 为 30 passed，compileall、Phase 0 校验和 CLI TXT 端到端冒烟均通过。
- 一次带临时目录清理的冒烟命令被自动审查因工具额度限制拦截，未执行删除；随后使用新临时目录完成相同验证。
- 当前没有开始 Phase 2；互联网找书、真实 Provider、OCR、M4B、CI 和许可证仍是后续事项。

## 2026-09-13T02:40:00Z — Phase 1 功能验证完成，提交受阻

- `uv run pytest -q`：30 passed；`python3.12 -m compileall -q src tests`、`python3 scripts/validate_project.py`、`git diff --check` 均通过。
- `bookcast generate examples/example.txt` 和 `bookcast status <job> --json` 端到端冒烟通过，生成 MP3 且完整性为 `ok`。
- 功能文件已暂存，但自动审查因当前 Codex 工具额度限制拒绝 `git commit`；普通模式也无法写入受保护的 `.git`，因此 `origin/main` 尚未包含 Phase 1。
- STATE/HANDOFF 已明确该 blocker；恢复 Git 提交权限后创建功能提交、推送并再固定最终交接提交。未开始 Phase 2。

## 2026-09-13T02:48:17Z — Phase 1 功能提交与交接快照完成

- 创建 Phase 1 功能提交 `9e6e5aaa0db1a61320be9c28815caaf7c4b3f5cc`（`feat: add local ebook mock podcast pipeline`），包含 20 个文件：EPUB/PDF/TXT 解析、Mock Provider、流水线、存储、音频合并、CLI、测试及设计文档。
- 在该提交上运行离线项目校验、30 项 pytest 测试和 `git diff --check`，全部通过。
- 清理 STATE 中的 blocker 与 in_progress，更新 last_verified_commit 为该功能提交，标记 Phase 1 completed。
- 更新 HANDOFF 与 WORKLOG，准备提交交接快照并推送到 GitHub `origin/main`。

## 2026-09-13T13:06:34Z — Phase 2 Provider 与恢复实现，完整测试通过

- 核对 AGENTS、架构、交接和实际 Git：main 为 4bc8123，Phase 1 功能提交 9e6e5aa 已存在，原路线图的提交阻塞描述过期。
- 实现 Provider API、Registry、TOML 配置、Mock/兼容 HTTP/local LLM、受控 failover、manifest v2 审计和逐任务恢复；无新增依赖。
- 新增 48 项 Provider 场景测试，覆盖第七章切换、三级链、真实子进程强制退出、调用完成窗口、TTS 和 v1 迁移。
- 新测试首次 5 项失败源于时间断言只接受 Z，修正为校验 UTC ISO 8601；当前全量 78 passed、10 subtests passed，5 个既有 PyMuPDF 弃用警告。
- compileall 与 git diff --check 通过；同步 README、产品/架构/路线图、新增 PROVIDERS 指南和 D-009/D-010。
- 未连接真实 AI 服务，未实现真实语音、找书、OCR、M4B，未开始后续阶段；功能提交与最终快照待创建。

- 2026-09-13T13:07:04Z：文档状态校验通过；三级 Mock CLI 冒烟生成 MP3，doctor ready，status integrity=ok，resume 不修改 manifest。

## 2026-09-13T13:08:53Z — Phase 2 功能提交验证与最终交接

- 创建功能提交 `2f06b0107af9470f6a8758b8a1620ac8ab8f7cbe`（feat: add provider registry and resumable failover），共 23 个文件。
- 在该提交上运行完整测试：78 passed、10 subtests passed；项目校验、compileall、git show --check 全部通过。
- 普通 Git 写操作被文件系统沙箱阻止，使用获准的 Git 写权限后完成提交；没有遗留权限 blocker。
- STATE 标记 phase-2 completed、清空 in_progress，last_verified_commit 固定为已验证功能提交；HANDOFF/ROADMAP 同步。
- 本次未推送到远程、未调用真实 AI 服务、未开始后续阶段。最终交接快照按 D-006 单独提交。

## 2026-09-13T23:24:01Z — Phase 3 Source Resolver、真实公开书籍 demo 与测试

- 按 AGENTS 核对架构/状态/代码/Git，接手时 main=b6f0aa9 且工作区干净；本次仅实现 Phase 3。
- 实现 BookIdentity、候选选择、Source Registry、官方 CSV/RDF 与用户来源、有界 HTTPS/容器校验、获取检查点和 Pipeline 元数据桥接。无新增依赖或 UI。
- Gutendex API 实测 403，改用官方允许的机器可读目录与镜像。一次联网审批因 Codex 额度拒绝；用户继续且重置时间经过后，重新获准运行。
- 本地 DNS 返回 198.18.0.37，默认公网校验拒绝；核验真实 A 记录后以显式 --resolve 固定公网 IP，仍校验证书。首次目录超过 120 秒，按 600 秒有界配置成功取得 21205940 字节目录。
- 实际国富论查询返回 3300 和 38194 两个候选；选择 3300，核验书籍层美国公有领域声明、下载 2468951 字节 TXT，解析 67 段。源 SHA-256 为 e91d52dca43fd0fd69baf7776b00a5d8e74b69675b6b608ca8a0378aaf3ae58d。
- 真实重复获取检查通过：获取记录、源文件及解析产物字节/mtime 均不变。证据记录在 docs/PHASE3_DEMO.md；书籍和缓存未纳入 Git。
- 新增 79 项场景，全量 157 passed、10 subtests passed；5 个既有 PyMuPDF 警告。首次一项 EPUB fixture 缺少目录资源，补齐后通过。compileall 与 git diff --check 通过。
- 文档、领域词汇和 D-011/D-012 已同步；功能提交与最终交接快照待创建。

- 2026-09-13T23:26:37Z：补充 NaN/Infinity/越界超时回归，最终全量 160 passed、10 subtests passed；compileall、文档校验、git diff --check 通过。

## 2026-09-13T23:28:15Z — Phase 3 功能提交验证与最终交接

- 创建功能提交 `9bdf53ca5aef40b710142ba332849390592f6422`（feat: add legal book source resolver and safe acquisition），包含 21 个文件；没有提交书籍、目录缓存或生成产物。
- 在该提交上运行全量测试：160 passed、10 subtests passed；项目校验和 git show --check 通过。
- STATE 标记 phase-3 completed，清空 in_progress，last_verified_commit 固定为已验证功能提交；HANDOFF/ROADMAP 同步。
- 本次未推送远程，未增加 UI、未调用真实 AI/TTS；最终快照按 D-006 单独提交。

## 2026-09-14T04:12:39Z — Phase 4 分层内容与质量验证

- 检查 main 干净工作区、Phase 3 提交及相关实现；基线160项测试通过。
- 新增九类有证据偏移的分块分析、章节/全书有界综合、全局预算规划和三种写作模式。
- 加入逐段一致性调用、本地质量指标、TTS前阻断、片段修订及依赖恢复；保留旧任务路径和Provider审计。
- 187项全量测试和10个子测试通过；含长章/18章长书、阶段接管、块中断、错误证据、质量阻断、修订传播和缓存刷新。编译及项目校验通过。
- 自制中文demo生成3片段、1285字符；覆盖100%、重复4.93%、Host B27.47%；真实MP3测试音调15.25秒。恢复未新增调用，全部文件字节/mtime不变。
- README、产品、架构、路线图、D-013、内容指南、demo、STATE及HANDOFF已同步；待功能提交验证与最终交接。

## 2026-09-14T09:17:07Z — Phase 4 提交验证与交接

- 创建功能提交 `d0bfde29681d063c97917cae2a7168a9765c79e2`（23个文件），仅包含本阶段代码、测试、自制样本和文档。
- 对该提交重新运行全量测试：187 passed、10 subtests passed；编译、项目校验、提交空白检查全部通过。
- STATE 标为 Phase 4 completed，清理 in_progress，记录实际已验证提交；HANDOFF 与 ROADMAP 同步。没有开始下一阶段或推送远程。

## 2026-09-14T14:40:00Z — Phase 5 持久化任务与恢复验证

- 接手 main=977f406，工作区初始干净，Phase 4 基线187项测试通过；按 AGENTS 核对状态、决策与代码。
- 增加 Job/Step/Artifact/Attempt、manifest v3、六态、运行所有者和内核锁恢复；保留旧版本备份与原内容流程。
- 实现任务列表/状态/恢复/重试/诊断、来源和Provider配置快照、具体实例配置缓存与安全进度日志。
- 只读审阅发现的原文件依赖、配置误用、永久失败重试和stale区分问题已落实；并行实现Agent因额度不可用，后续实现及测试由主Agent完成。
- 204项全量测试、10个子测试通过；17项新增场景含三个真实SIGKILL窗口、并发锁、额度接管、永久错误、损坏修复和迁移。
- 自制demo完成27步骤、30产物记录、16次调用；五个任务命令通过，恢复后32文件字节/mtime和调用数不变。证据见 PHASE5_VERIFICATION。
- JOBS、D-014、架构、产品、内容、Provider、来源和路线图已同步；正在完成提交前检查与最终交接。

- 2026-09-14T14:40:57Z：最终编译、项目校验、空白检查与CLI六项回归通过；人工对照文档一致，demo源文件、日志、manifest和MP3均被Git忽略。准备功能提交。

## 2026-09-14T14:42:38Z — Phase 5 功能提交验证与最终交接

- 创建功能提交 `c05af03be05d50995d90940112a902c101f29135`，24个文件；没有提交书籍、生成音频或日志。
- 在该提交运行完整测试：204 passed、10 subtests passed；5个既有PyMuPDF/SWIG弃用警告。编译、项目状态校验和 git show --check 全部通过。
- STATE 标为 phase-5 completed，清空 in_progress，记录已验证功能提交；HANDOFF、ROADMAP同步，最终快照按 D-006 单独提交。
- 本阶段未推送远程，也未开始下一阶段。

## 2026-09-14T15:00:02Z — Phase 6 可选 Skill 与核心边界验证

- 接手 HEAD=bc8fd9f，工作区干净并与origin/main一致；Phase 5已由前一轮推送。重新运行基线204项测试和10个子测试通过。
- 按skill-creator指导创建skills/bookcast/SKILL.md，仅包含Agent指令和公共CLI示例，没有解析、下载、Pipeline或音频实现脚本。
- 明确源书版本/语言、中文内容、三种模式/分钟预算、Mock人声限制、版权规则、状态、有限恢复、永久错误和产物反馈。
- 新增19项测试，实际校验11条示例命令；三种模式生成和无新增调用恢复通过，版本歧义/版权资格/永久失败仍由Core拦截。独立无Skill应用目录可生成、查询和恢复。
- 全量223项测试、10个子测试通过；5个既有PyMuPDF/SWIG弃用警告。Skill格式、编译、项目校验和空白检查通过。
- Core源码、pyproject.toml及uv.lock与c05af03完全一致；没有新增运行依赖、持久格式、Provider或宿主全局配置。
- README/产品/架构/路线图/STATE/HANDOFF已同步，待功能提交验证及最终交接快照。自然语言跨Agent行为与真实人声不在本次自动测试证明范围。

## 2026-09-14T15:02:26Z — Phase 6 提交验证与交接

- 创建功能提交 `0175f44db8a913f5a477c88fec2b60c3629fb1e8`，包含Skill、测试与文档10个文件。
- 在此提交重跑全量测试：223 passed、10 subtests passed；Skill格式、编译、项目校验、Core无差异和提交空白检查全部通过。
- STATE标为phase-6 completed、清空in_progress并记录实际已验证SHA；HANDOFF与ROADMAP同步。最终交接快照按D-006单独提交。
- 本阶段未推送远程；未自动安装Skill到宿主或开始后续阶段。

## 2026-09-15T04:12:21Z — Phase 7 本地 Web 工作区验证

- 接手892eb61，与origin/main同步；Phase 6基线223项测试及10个子测试通过。
- 增加Next.js静态页面、FastAPI Application API、持久提交/幂等键、独立持锁worker与serve命令。CLI配置组合提取共用；原解析、来源、Provider、分层内容、缓存、质量、音频算法及持久格式未改。
- 上传/候选选择、三模式/预算、任务/Provider状态、历史、音频播放与恢复可用。13项API测试覆盖真实Core调用、SIGKILL、quota/timeout/schema、缓存、容量、文件边界与Range。
- 3项Playwright通过：真实上传到播放、刷新历史、quota恢复首章不重复、候选交互；桌面和移动截图已查看。初次定位器与Next无障碍alert重名，修复后重跑通过。
- 最终236项pytest、10个子测试通过；7个依赖弃用警告。Next构建、TypeScript、编译、项目与差异校验通过。
- 同步README/产品/架构/来源/内容/任务/Provider/路线图；新增WEB指南、D-015和Tauri评估。没有账户、支付、云同步或桌面包，真实音频仍为Mock音调。
- 正在创建可交接功能提交；最后按D-006验证实际提交并单独保存完成快照。本阶段未push。

## 2026-09-15T04:14:17Z — Phase 7 提交验证与演示

- 创建功能提交82c49da946607a66a5ab1f5cbd59da4d27d81db7，33个文件。
- 该提交上的236项测试和10个子测试通过；3项Playwright、Next静态构建、TypeScript、编译、项目校验及提交差异检查全部通过。
- 启动本地8765预览，使用纯Mock示例配置。通过Application API导入自制content-demo并完成3章、27步骤、16调用；184077字节MP3保存在忽略的data/web，Web ID为4590ab11918d4751b00da9841dcbdc3f。
- STATE标记phase-7 completed，清空in_progress，记录实际验证SHA；HANDOFF/ROADMAP同步。按D-006另存最终快照，未执行push或开始后续阶段。

## 2026-09-15T09:15:23Z — Phase 8 本地中文人声

- 按用户免费开源、中文友好优先要求接入Kokoro多语言/sherpa-onnx CPU；依赖可选，无API Key。实测并显式锁定上游遗漏的sherpa-onnx-core动态库依赖。
- 实现tts setup的固定官方包、大小/SHA、安全展开和收据核验；生成阶段离线。中文双音色、语速/线程可配置，不覆盖已有配置或模型。
- Core新增中立语音单元能力，以现有Step/Attempt保存每次调用、逐句WAV/音色记录和片段汇总；旧Mock有效音频保留，CLI/Web显示实际类型/时长。
- 25项TTS和14项Web专项通过；两个真实SIGKILL窗口、额度/超时接管、永久错误停止、配置失效、单句损坏修复、旧Mock保护和Web来源显示均验证。
- 工作区全量262 passed、10 subtests passed；3项Playwright、Next构建、TypeScript、编译、项目校验与Git忽略规则通过。模型及产物不进入Git。
- 真实三章中文示例完成24次Kokoro合成，51步骤/37调用；MP3为276.429秒、3,318,093字节。resume检查83文件字节/mtime不变，无新增调用；内容仍为Mock且needs_review，未声称主观听感/真实LLM验收。
- 更新TTS指南、D-016、术语、Skill、README及架构/产品/Provider/任务等文档；准备创建功能提交，之后按D-006实际验证提交并保存最终快照。本阶段未push。

## 2026-09-15T09:19:31Z — Phase 8 提交验证与完成交接

- 创建功能提交54c05adcc61d0023acfaaabf0dbd2648df2944a6，共34个文件。
- 在该提交上运行完整pytest：262 passed、10 subtests passed；3项Playwright、Next构建、TypeScript、compileall、项目校验和提交差异检查通过。
- 该提交实际合成2.22秒云希中文样音；原276.429秒demo再次resume，83个文件字节/mtime与调用数不变。固定安装器通过官方重定向的1字节网络探测成功。
- STATE设为phase-8 completed，last_verified_commit记录54c05ad完整SHA；清空in_progress，更新HANDOFF/ROADMAP并准备D-006最终快照。没有推送远端；没有启用付费服务。

## 2026-09-15T14:25:01Z — Phase 9 离线实现与真实验收边界

- 接手核对HEAD与origin/main同为4137d85；历史Phase 7/8已经推送，修正文档中过时的未push描述。关键基线86项通过。
- 编码前查官方DeepSeek发布说明、Chat Completions、thinking、usage和错误协议；使用deepseek-flash，复用CompatibleLLMProvider，无SDK/Core重写。
- 实现严格生成选项、中央候选任务策略、实际配置缓存、可选响应模型/usage、HTTP402与截断分类、无密钥配置示例和CLI配置检查。
- 新51项专项离线通过；组合专项99 passed，完整313 passed/10子测试，1项联网测试默认跳过。project validator、compileall和diff检查通过。
- 实际官方models无效凭证探测返回401并正确分类；未持久化错误正文。没有有效DEEPSEEK_API_KEY，尚未调用收费生成，没有真实usage或DeepSeek+Kokoro音频验收。
- PHASE9_REAL_LLM明确列出未验收项及执行步骤，reasoning策略尚待真实质量实验。准备创建功能提交并按D-006验证；不push、不开始Phase 10。

## 2026-09-15T14:27:26Z — Phase 9 功能提交验证与阻塞交接

- 功能提交ff3c48ac960e8435889dcc303665626e4438f186共24个文件，未推送。
- 在该提交上验证313 passed、10 subtests passed、1联网测试跳过（44.35秒）；7个既有警告，project validator、compileall和提交差异检查通过。
- STATE.last_verified_commit记录该实际验证SHA，in_progress清空、task_status=blocked；唯一阻塞为真实生成验收缺少有效DEEPSEEK_API_KEY。Phase 9尚未完成，未生成真实DeepSeek脚本或MP3、无真实usage计数。
- 保存D-006交接快照；不重复跑已通过功能测试，不开始Phase 10，等待用户安全配置凭证后继续剩余验收。

## 2026-09-15T14:41:34Z — 已提供凭证文件但系统拒绝访问

- 用户授权读取Downloads中的bctest.txt；普通及提权执行均返回PermissionError/Operation not permitted，没有读取或输出密钥内容，未发起新API调用。
- 已请用户在自己的终端复制到data/deepseek-key.txt；git check-ignore确认目标被忽略。不是自动审批拒绝，不绕过系统访问限制。
- 接手62b849f，代码未改；Provider专项99 passed、5个既有警告（14.49秒）。更新STATE/HANDOFF/验收记录的真实阻塞，Phase 9仍未完成，不push。

## 2026-09-15T23:39:53Z — Phase 9真实内容与本地音频验收

- 用户复制凭证到忽略目录后可安全加载为环境变量；实际DeepSeek/Kokoro健康检查通过。期间自动审批服务曾因额度拒绝一次网络请求，恢复会话后获批执行；未绕过权限或输出Key。
- 真实模型四次字符偏移业务失败保留在测试01。预提供偏移仍不稳定，改为content-analysis-v3证据ID选择，由Core精确查表还原既有RichAnalysis；非法ID仍永久失败，不新增厂商Adapter或改变质量门禁。
- 测试02首个v3分析曾schema_error，人工显式诊断重试成功；已完成任务联网pytest复验1 passed，禁止网络的resume无新增调用。未保存原始响应，未推断具体坏字段。
- 正式Job 7229d03765db4d1c860c7bd18d62b178的13次DeepSeek调用首次成功。Kokoro首版251.343秒，0.8语速重合成后320.283秒/3844557字节；调速禁网且LLM记录完全不变，最终MP3 SHA见验收记录。
- 正式任务完成后禁止HTTP/TTS恢复，81文件SHA/mtime及59条调用不变。临时副本全局disabled仍复用分析、首个章节综合失效；请求前停止，原任务不变。
- 三章覆盖、1182字符、B占44.33%；文本审读确认追问/假设反例，保留2项source归属警告、needs_review。没有真人试听，不声称内容优秀。
- 三任务共48次LLM尝试（43成功/5失败），input56285/output79397；正式样例input16422/output24445。未报告的reasoning计数保留null，无费用估算。207文件秘密字面值扫描无匹配。
- 专项142 passed；完整318 passed/10子测试/1联网默认跳过；project validator、compileall、diff检查通过。更新README/架构/Provider/内容/决策/路线图/交接，不push、不开始Phase 10。

## 2026-09-16T06:11:34Z — Phase 9提交验证与最终交接

- 创建功能提交17fc1f1f9bac7f31aa1f9ab4c539fa625811421a，21个文件，包含证据选择修复与真实验收记录。
- 提交上完整318 passed、10 subtests passed、1联网测试默认跳过，7个既有警告（42.66秒）；project validator、compileall和提交差异检查通过。
- 实际已完成音频再次禁HTTP/TTS恢复：81文件集合/SHA/mtime及59次历史调用不变；配置变更副本验证通过，未新增收费调用。
- STATE记录实际验证SHA并完成交接，in_progress和blockers为空；保留真实schema不稳定、两处来源归属及未试听限制。按D-006提交最终文档快照，不push、不开始Phase 10。

## 2026-09-16T12:17:19Z — Phase 10双模式TTS与真实A/B

- 接手HEAD/origin/main同为91b4bcb，Phase 9已推送；135项关键基线通过。核验Google官方TTS/模型/免费层/计费/数据条款；新增显式云端配置和Gemini REST Adapter，无SDK依赖、无Core厂商绑定，Kokoro Adapter未修改。
- SpeechSegment按主题最多600字符/24发言；每段独立Step/Attempt，音色/style/model/契约参与缓存。永久权限/schema错误不切换，云端链禁止Mock及跨语音模式混链。
- Phase 9真实脚本独立重渲染，未新增LLM调用，原manifest复验不变。Kokoro23单元成功、320.267208秒；Gemini3段成功、251.440秒，模型gemini-3.1-flash-tts-preview、Kore/Puck。第三段一次schema失败经受控显式重试完成，前两段未重做；用量含失败input1428/output12018。
- 真实模型查询和无效Key鉴权分类通过；两份MP3完整解码/非静音通过。禁Provider恢复两份任务83/43文件SHA/mtime不变；实际API免费/付费层级和人工试听尚无反馈，不编造验收。
- Gemini专项48通过；完整367通过/10子测试/2联网默认跳过，49.78秒；显式真实任务测试1通过。validator/compileall/diff通过，196文件秘密字面值扫描无匹配。新增第6段quota接管、真实SIGKILL、单段修复和A/B只调用Core的测试。
- 更新D-019、术语、运行/架构/Provider/TTS/Job/产品/路线图、PHASE10_TTS_AB及交接状态。技术收尾完成，STATE仅保留待用户反馈的验收阻塞；不push、不开始大型本地TTS阶段。
