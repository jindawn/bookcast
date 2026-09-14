# 给下一位 Agent 的交接

更新时间：2026-09-14T14:40:00Z；当前分支 `main`。先按 AGENTS 阅读文档并核对实际代码/Git。

## 当前目标

Phase 5：长任务可靠性、断点恢复、持久化任务模型和命令。实现、204项自动测试与实际 CLI demo 已完成；文档一致性、编译和项目校验也已通过；待创建功能提交并验证。没有开始下一阶段。

## 刚刚完成

- manifest v3 引入 Job/Step/Artifact/Attempt、六态投影、独立随机 job_id、来源 seed、配置快照和运行所有者。
- 持有内核锁才恢复 stale；最小 AI 调用和本地步骤独立落盘，调用成功到步骤成功的中断窗口直接复用结果。
- jobs/status/resume/retry/doctor，按 ID 从导入副本继续，不依赖原路径或当前目录的 TOML。永久失败需显式 retry。
- 输入/提示/Provider 配置/产物哈希缓存；另一 Provider 接管保留已完成章节，同名配置改动可使旧调用失效。
- 分阶段登记 PENDING、日志和进度；重规划的过时步骤标为 SKIPPED，诊断写入失败不影响已保存的 AI 成功。
- 新增 [JOBS.md](JOBS.md)、D-014 和 [PHASE5_VERIFICATION.md](PHASE5_VERIFICATION.md)，同步产品、架构、路线图、Provider 与内容文档。

## 关键文件与当前代码

`models.py` 定义 Job/Step/Artifact/Attempt，原 Manifest/StepRecord/AIAttempt 为别名。`pipeline.py` 管理恢复、缓存、步骤/调用与原子记录；`jobs.py` 只读发现和状态投影；`cli.py` 组合保存配置和五个任务命令。`provider_chain.py` 记录具体实例配置摘要，`storage.py` 提供只读锁探测；`content.py` 登记内容阶段任务。测试新增 `test_job_recovery.py`、`test_job_cli.py`，旧 Provider 测试仅适配 manifest v3。

新任务 schema v3、pipeline_version=2；旧 v1/v2 manifest 原样备份迁移且保留原 pipeline 版本。STATE v1、acquisition v1 未变。无新依赖、厂商 SDK、后台调度、数据库或 UI。

## 已运行的测试与结果

- `.venv/bin/python -m pytest -q`：204 passed、10 subtests passed，5 个既有 PyMuPDF/SWIG 弃用警告。
- 新增17项任务测试全部通过：三处真实 SIGKILL、活跃进程锁排除、只读 stale 查询、原文件移走、来源保留、A额度耗尽/B接管、永久错误重试、配置/提示版本/产物缓存、旧任务迁移和日志断开。
- 实际 demo：`output/phase5-demo/5bdad5ca96f5e42cd019ff30/`；Job ID `68a1af3368a145c7b402440866501672`。27完成步骤、30 Artifact、16次调用；3/3章；jobs/status/resume/retry/doctor均成功。32个文件（不含锁）的字节和mtime在恢复后不变。
- MP3 测试音调15.25秒、24kHz、单声道；不是人声验收。完整命令和哈希见验证文档。
- compileall、项目状态/链接/Git校验和 git diff --check 通过；CLI 六项回归在帮助文本调整后再次通过。实际功能提交验证待完成。

## 未解决问题与技术债

没有未解决自动测试失败。外部请求已被服务处理但本地未保存的窗口仍可能重复请求/计费；已落盘的有效调用会复用。未做真实电脑重启、Windows、共享盘或多主机并发验收。当前恢复由用户重新运行命令触发，不自动开机运行。状态查询逐个计算产物哈希，大目录性能仍可优化。

Mock 仍为规则写作/未验证语义和测试音调，真实兼容 LLM/TTS 未验收。Phase 4 的固定字符分块、精确文本去重、代表性压缩和预算限制仍在；质量报告不保证事实或版权全部合规。旧音频可能留在失败任务目录，应以状态/完整性和质量报告为准。许可证、OCR、M4B、UI 待后续授权范围。

## 下一步建议

1. 完成当前功能提交验证，更新 STATE 的真实已验证 SHA，按 D-006 创建最终交接快照。
2. 新 Agent 从 JOBS、PHASE5_VERIFICATION 和测试了解恢复契约；先核对 Git，再按用户授权选择下一阶段。
3. 后续可选择真实中文内容/语音验收，或补 Windows/重启场景；不要自行扩展本阶段。

## 不要重复做

不要重新初始化仓库、维护第二套 Agent 规则、重新下载 Phase 3 样书或重做有效章节。不要凭 PID/时长抢占锁，不删除锁文件或用户产物。禁止为修复永久错误盲目切模型；不要用无关 CWD 配置覆盖任务快照。不要把旧 pipeline 静默升级或伪造历史审计；书稿、源文件、音频与日志都留在 Git 忽略目录。不得 force push。

## 最近已存在的 Git commit

当前 HEAD：`977f4064c37f56b3958db812e9ecf21efda33740` — docs: finalize Phase 4 verification and handoff。最近已验证功能提交仍为 `d0bfde29681d063c97917cae2a7168a9765c79e2`；Phase 5 修改尚未提交。接手时先以 Git 实际状态核验。

最终快照自身的提交通过 `git log -1` 获取，按 D-006 不自引用。
