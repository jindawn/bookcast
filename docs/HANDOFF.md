# 给下一位 Agent 的交接

更新时间：2026-09-14T15:02:26Z；当前分支 `main`。先按 AGENTS 阅读文档并核对实际代码/Git。

## 当前目标

Phase 6：封装可选 BookCast Skill，仅理解意图并调用现有 Core。Skill、19项专门测试、223项全量回归、文档和功能提交验证均已完成；Core未改动。

## 刚刚完成

- 新增 [BookCast Skill](../skills/bookcast/SKILL.md)，声明使用条件、参数、模式、预算、版权、状态、错误、resume 与示例。
- 明确中文输出与原书语言、预算与播放时长、Mock与人声的区别；不能把《国富论》示例静默固定为某个英文版本。
- 只调用公共 CLI，不导入私有模块/厂商 SDK，不自行解析、下载、生成脚本、拼音频或编辑任务记录。
- 测试直接执行 Skill 中命令，确认三种模式经过原 Pipeline，Core 保留版本选择、版权资格和永久失败保护。
- 单独复制应用包到没有 skills/ 的目录，实际执行 CLI 生成、查询、恢复；检查产物和调用记录未因恢复改写。
- README、产品、架构和路线图已同步可选入口；使用 skill-creator 的格式验证与边界指导，没有新增不必要的脚本或宿主专属配置。

## 关键文件与当前代码

本阶段只新增 `skills/bookcast/SKILL.md`、`tests/test_skill.py` 并更新项目文档。`src/bookcast`、`pyproject.toml`、`uv.lock` 相对 Phase 5 功能提交 c05af03 没有差异。Skill 可单独给 Agent 加载，未自动安装或修改全局配置；无需 Skill 时仍直接使用 CLI。

Core 继续使用 manifest v3、pipeline_version=2（旧任务保留原版本）、STATE v1、acquisition v1。没有新增依赖、Provider、SDK、UI 或持久格式。任务恢复细节见 [JOBS.md](JOBS.md)，分层内容见 [CONTENT.md](CONTENT.md)。

## 已运行的测试与结果

- Phase 5 基线 `.venv/bin/python -m pytest -q`：204 passed、10 subtests passed，5个既有PyMuPDF/SWIG弃用警告。
- `tests/test_skill.py`：19 passed，包含11条文档命令参数、三模式实际生成/恢复、两种来源资格、永久失败和无Skill运行；生成均为自制样本与Mock。
- skill-creator 的 quick_validate.py：Skill is valid；`python3 scripts/validate_project.py` 通过。
- `git diff --exit-code c05af03 -- src/bookcast pyproject.toml uv.lock` 通过：没有重写核心能力。
- 对实际功能提交0175f44重新运行全量回归：223 passed、10 subtests passed；Skill格式、编译、项目校验、Core无差异与git show --check均通过。Phase 5实际demo证据保留在 [PHASE5_VERIFICATION.md](PHASE5_VERIFICATION.md)。

## 未解决问题与技术债

没有未解决自动测试失败。Skill 测试验证命令可执行及Core边界，不等同于每个Coding Agent在自然语言对话中都遵守指令；未做跨宿主实际加载和真实模型行为验收。Skill格式验证工具来自本机skill-creator，普通项目测试仍只需pytest，不新增PyYAML运行依赖。

Core限制继续有效：外部请求已被服务处理但本地未保存的窗口仍可能重复请求/计费；已落盘的有效调用会复用。未做真实电脑重启、Windows、共享盘或多主机并发验收。恢复需重新执行命令，状态查询逐个计算哈希，大目录性能待优化。

Mock 仍为规则写作/未验证语义和测试音调，真实兼容 LLM/TTS 未验收。Phase 4 的固定字符分块、精确文本去重、代表性压缩和预算限制仍在；质量报告不保证事实或版权全部合规。旧音频可能留在失败任务目录，应以状态/完整性和质量报告为准。许可证、OCR、M4B、UI 待后续授权范围。

## 下一步建议

1. 核对STATE中已验证SHA与实际Git，阅读Skill、test_skill和JOBS；该阶段已完成，无需重新封装核心逻辑。
2. 按用户授权选择后续工作；可在目标Agent中做真实对话演练，或验收真实中文内容/TTS。
3. 若CLI参数或结果契约变化，同步Skill示例并运行test_skill；不要在Skill中修复Core缺陷。

## 不要重复做

不要在Skill复制解析/下载/Pipeline/FFmpeg或直接写manifest，不新增第二份任务状态。不要把中文输出映射为acquire --language zh，不把Mock音调/预算当人声/实际时长，不静默选版本。无需重新初始化、下载Phase 3样书、重做有效章节、删除锁或盲目切模型；源文件/音频/日志不提交，不force push。

## 最近已存在的 Git commit

最近已验证功能提交：`0175f44db8a913f5a477c88fec2b60c3629fb1e8` — feat: package BookCast skill over existing CLI（10个文件）。在此提交上的223项测试、10个子测试、Skill格式、编译、项目/提交校验全部通过。Phase 6接手HEAD为bc8fd9f且与origin/main一致；本阶段仅本地提交，远端状态以实际Git核对。

最终快照自身的提交通过 `git log -1` 获取，按 D-006 不自引用。
