# 任务、恢复与缓存（Phase 5）

每本书的运行状态保存在产物目录的 `manifest.json`。终端关闭、进程崩溃或重启后，用任务 ID 或目录继续；不依赖聊天、原终端、PID 存活记录或当前工作目录的配置文件。

## 常用命令

```sh
uv run bookcast generate examples/example.txt --provider auto
uv run bookcast jobs --json
uv run bookcast status JOB_ID --json
uv run bookcast resume JOB_ID
uv run bookcast retry JOB_ID
uv run bookcast doctor
```

把 `JOB_ID` 替换为 generate 或 jobs 显示的 ID。默认在 `output` 及其子目录查找；另用输出根目录时，各命令传 `--output-dir output/my-run`。也可以 `bookcast resume output/my-run/书籍目录`，此时不依赖默认查找根目录。

新任务 `job_id` 是持久化的 32 位随机十六进制标识。`book_id` 仍是源文件内容/格式摘要的前 24 位，目录仍为 `output/{book_id}/`。同一源文件在不同输出根目录创建的任务有不同 job_id。旧 book_id 也可查询；若匹配多个目录必须指定路径，禁止静默选择。复制整份任务目录会复制 job_id，同样需要用路径区分。

| 命令 | 行为 |
| --- | --- |
| `jobs` / `jobs --json` | 只读发现任务，损坏记录单独列出；不跟随嵌套符号链接 |
| `status JOB_ID` | 显示状态、进度、最近错误、活动锁、stale 和产物完整性；JSON 包含详细模型 |
| `resume JOB_ID` | 接续 PENDING、stale 或临时失败，也可校验已完成任务、修复损坏产物；有永久失败则停止 |
| `retry JOB_ID` | 修复失败原因后显式重试；仍复用有效结果，不清空历史或整书重做 |
| `retry JOB_ID --revise-segment 0002` | 主动重写指定片段和受影响下游；沿用 Phase 4 修订机制 |
| `doctor` | 检查 Python、FFmpeg、配置服务和存储中的活动/stale/损坏任务；不生成内容或修复任务 |

原 `generate 源文件 --resume` 保留兼容，属于显式重试入口，可重试永久失败。推荐恢复用新的 resume/retry 分工。语义/质量错误不会因为 retry 自动切到另一个模型，仍需修复原因或明确修订。

## 恢复所需的数据

新任务创建时记录原路径；成功导入后使用 `source/input.epub|pdf|txt`，恢复前核对原始 SHA-256。导入副本有效时，原文件移动或当前目录改变不影响恢复。副本损坏或缺失时尝试已保存原路径，内容必须仍一致；两者都不可用时停止，不消耗 AI。可用相同内容的原文件执行兼容 `generate --resume` 修复。

`acquire --generate` 还保存来源 metadata seed，解析恢复保留书名、身份与来源依据。未完成导入前仍需要可访问的原文件。用户源文件不会被覆盖或删除。

CLI 保存严格校验后的 Provider 配置及选择；只包含 `api_key_env` 的环境变量名称，不保存变量值。恢复不会悄悄读取新的工作目录下的 `bookcast.toml`。凭证需在新终端的环境中可用。需要调整时显式使用：

```sh
uv run bookcast resume JOB_ID --provider secondary
uv run bookcast resume JOB_ID --config /absolute/path/providers.toml --provider auto
uv run bookcast retry JOB_ID --config /absolute/path/fixed-providers.toml
```

`--provider` / `--tts-provider` 指的是配置实例名称；`auto` 按所选配置优先级组成链。Python API 的自定义 Provider 实例不能自动序列化，调用方需再次注入实例；CLI 快照机制不会保存任意对象或 Secret。

## 模型与状态

[models.py](../src/bookcast/models.py) 中的 Pydantic 模型是运行格式契约：

| 模型 | 主要记录 |
| --- | --- |
| Job | 标识、源哈希、模式/预算、配置快照、运行所有者、Steps、Artifacts、AI Attempts、错误和时间 |
| Step | 最小处理单元的状态、输入指纹、产物哈希、执行次数、错误和更新时间 |
| Artifact | 相对路径、大小、SHA-256、所属步骤、输入哈希、提示版本、Provider 配置摘要、实际归属、缓存键和时间 |
| Attempt | 一次 AI 调用的 provider/model、状态、输入/输出哈希、提示版本、配置摘要、错误及时间 |

Job 的 `steps` 和 `artifact_records` 以步骤名/相对路径索引，`ai_calls` 按尝试追加。一个 Step 可以有多个 Attempt；本地解析/合并等步骤没有 AI Attempt。旧导入名 Manifest、StepRecord、AIAttempt 是同一模型的别名。

`state` 是只读枚举投影，供新调用方程序化读取；旧 `status` 和 `error_kind` 是权威存储字段。读取时忽略序列化的 state 并重新计算，避免两套状态冲突。

| state | 含义与后续 |
| --- | --- |
| PENDING | 已登记，尚未执行 |
| RUNNING | 已落盘开始状态，当前执行或中断遗留 |
| SUCCEEDED | 输出已持久化并记录哈希；对应兼容 status=completed |
| FAILED_RETRYABLE | interrupted、timeout、临时不可用、额度或限流；可 resume |
| FAILED_PERMANENT | 输入、schema、认证、业务/质量等需修复的失败；显式 retry |
| SKIPPED | 重新规划后不再需要的历史 Step，记录 skip_reason，保留文件与尝试 |

缓存命中在进度事件中显示 SKIPPED，原成功 Step 仍保持 SUCCEEDED；正常完成任务的重复恢复不会只为了记一次命中而改写文件。Job 聚合状态使用前五种，Attempt 不使用 SKIPPED。`status --json` 另有 effective_state、stale、active 和 integrity；后者区分“历史成功但文件已损坏”，不改写持久状态。

## 崩溃与并发

每次状态变化用临时文件、fsync、原子替换和父目录同步持久化 manifest；每个成功产物也原子写入并记录哈希。AI Attempt 先 PENDING 再 RUNNING，输出完整写入后 SUCCEEDED。若在 Attempt 成功后、Step 成功前被杀死，恢复直接采用该 Attempt 的有效产物。

同一任务始终由 OS 内核文件锁保护。第二进程无法取得锁时立即拒绝；只有成功持锁后，才将遗留 RUNNING Step 及 pending/running Attempt 标为 interrupted/FAILED_RETRYABLE，并接续最小未完成任务。所有者中的主机、PID、会话时间只作诊断，不用 PID 或超时抢占活动任务。`.lock` 文件存在不代表任务活动，不要手工删除锁文件。

status/doctor 的活动状态是一次只读观察，可能随并发进程变化；真实恢复始终重新获得内核锁。初始 manifest 原子写入前崩溃所留下的 `.manifest.json.*.tmp` 可保留并重建任务，其余无 manifest 的非空目录仍拒绝覆盖。

在服务已经处理请求、但本地还没有持久化成功记录时，恢复可能重发这个最小任务。没有端点幂等键协议时无法保证外部调用恰好一次或免重复计费；已经持久化且有效的结果不因此重做。

## 缓存判定

1. Step 输入指纹覆盖内容依赖和版本；AI Attempt 同时记录提示版本及输入哈希。
2. Artifact cache_key 由 input_hash、prompt_version、provider_config_hash 组成；每次复用校验文件 SHA-256。文件损坏、缺失或输入/提示改变时重建受影响步骤。
3. 具体 Provider 摘要来自 name、model、cache_key 的哈希。同名实例仍在所选链中而配置改变时，该实例的旧调用失效；不将配置变化伪装成纯重启。
4. 显式选择另一实例或移除旧实例时，保留输入和产物有效的已完成结果及原始归属，防止 A 第七章额度耗尽后 B 重做前六章。未完成任务使用当前链。
5. 下游按实际输入变化失效；上游重新运行但输出字节相同，不强迫重写所有下游。整本换模型时使用新输出目录。

Artifact 索引描述当前文件，Attempt 保留历史摘要；旧文件被重写后不保证历史尝试仍可回放。SKIPPED 步骤文件保留但不作为当前计划完整性的必要条件。配置环境变量值不进入摘要，不保存密钥；改正凭证后可显式 retry。

## 可观测性与边界

进度输出到 stderr，JSON 结果保留在 stdout。显示书籍、阶段、章节分析完成数、当前 Provider、已完成/剩余已知任务和最近错误。解析/规划尚未提供完整清单时会明确显示“后续任务数待规划”；综合树的节点可继续按需登记，不把已知数量当作进度承诺。

`logs/events.jsonl` 追加阶段、调用、缓存命中、stale 恢复和结果事件，使用安全错误分类，不记录服务错误正文或凭证。最近错误可保留已经成功接管的失败，需结合当前状态判断。日志是诊断信息，manifest 才是恢复依据；日志/终端写入失败不会把已保存成功的 AI 调用改成失败。纯完成任务恢复连日志和 manifest 的字节/mtime 都不改变。

manifest v1/v2 首次恢复前分别原样备份为 `manifest.v1.json` / `manifest.v2.json`，升为 v3 并补充可核验的产物索引。保留旧 pipeline_version，不重做旧内容或伪造缺失的 Provider 摘要、时间和调用历史。没有快照的非默认旧配置要求显式 --config。仓库 STATE v1 和获取 acquisition v1 与运行 manifest v3 是独立契约。

当前是串行本机执行，没有后台 worker、定时重试或开机自动启动。已在 macOS 用真实 SIGKILL 验证三个中断窗口；没有实际重启电脑、Windows 或网络共享盘验收。大量产物的只读状态查询仍逐个计算哈希，性能优化待后续范围。证据见 [PHASE5_VERIFICATION.md](PHASE5_VERIFICATION.md)。
