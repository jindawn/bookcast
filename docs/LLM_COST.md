# LLM 成本审计与有界内容计划

## 已完成真实任务的只读审计

审计 `data/web/jobs/3a591bfdaac14dbbb254ae8b9e138e85/output/b288c0f91d3e46f32eb95916/manifest.json` 中的服务端 usage 和完成步骤；不读取或输出正文、原始响应、密钥，也没有调用 API。该任务包含失败重试与定向修复，因此实际账单不能当作 clean run。

| Stage | 成功唯一任务 | 实际 LLM attempts | 平均输入 | 平均输出 | 实际输出 | 缓存输入 | reasoning 输出 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| analysis | 96 | 115 | 6,418 | 1,593 | 183,204 | 87,168 | 0 |
| chapter synthesis | 191 | 192 | 926 | 2,609 | 500,945 | 56,064 | 450,017 |
| book synthesis | 26 | 26 | 1,395 | 7,214 | 187,573 | 7,552 | 165,100 |
| dialogue | 24 | 48 | 1,935 | 3,291 | 157,973 | 39,168 | 136,598 |
| consistency | 24 | 48 | 1,887 | 3,600 | 172,790 | 32,256 | 163,989 |

共 361 个唯一内容 LLM 步骤、429 次实际 LLM attempt；这些已记录 attempt 合计输入 1,135,571、输出 1,202,485 token，其中 reasoning 915,704 已包含在输出中。用户报告的当日总账 3,465,437 token 涵盖更广的调试过程，不能用此 manifest 单独复算。旧 `target_duration` 直到全部 72 个 XHTML 单元的 96 次块分析、191 次章节综合和 26 次全书综合之后才生效。规划选择 24 个 segment，各需 dialogue 与 consistency；quality 是本地规则与既有 semantic review 的汇总，targeted repair 仅失败时追加调用，TTS 不属于 LLM。

## 新任务的离线投影

EPUB 来源过滤后，同一源为 69 个 XHTML 内容单元、93 个分析 chunk。分析继续保留完整来源证据；章节综合每批最多 12 个主题，旧分析结果投影约 75 次（旧 72 单元下计算为 78 次，实际取决于过滤后主题分布）。时长在全书综合前选择按书序分散的候选章节；20 分钟最多 32 个候选，对应约 11 次四叉综合。规划最多 16 个 segment，dialogue 和 consistency 各最多 16 次。合计约 210～220 次请求，比旧任务 361 个唯一步骤约少 39%～42%。这是结构性请求数投影，不是实际新账单。

章节综合关闭 reasoning 并限制输出 4096 token；全书综合保留 high reasoning 与 16384 上限；dialogue/consistency 保留 low reasoning、各设 12000 上限。分析维持既有 16384 上限以保留同配置旧任务的证据检查点。用户显式配置更低 max_tokens 时取更低值。模型达到输出上限会明确失败，不能靠无限重试扩张。旧任务的分析缓存继续复用；受新批次/策略影响的综合与其后计划将按指纹重算。

`usage/llm_usage.json` 是由 Attempt journal 汇总的只含计数/Provider/模型/Stage 的视图，含成功、失败、修订请求和复用计数。Provider 不返回 usage 时保留 unknown，不猜数字。价格未配置时 estimated_cost 为 null；内部汇总接口可传每百万输入、缓存输入和输出 token 价格，才计算估值。Mock smoke 命令：

```sh
.venv/bin/python scripts/llm_cost_smoke.py
```

它在临时目录显式使用 Mock LLM/TTS，真实 API 调用数为 0。最终质量仍须在用户决定付费新运行后审听；离线投影不能证明主观质量或总 token 必然低于某个账单比例。

## RC Stage 5：生产成本摘要

新 Job 在创建时把无凭证的 Provider、model、pricing 配置及 `Asia/Shanghai` / `cn-workday-peak-v1` 计价策略冻结为 `manifest.cost_snapshot`；后续修改 `bookcast.toml` 或恢复时调整当前 Provider 选择，不会重定价历史 Attempt。每次终态 LLM/TTS Attempt 后，Runner 从该 Job 的 journal 更新 usage 视图，并原子写入 `usage/cost_summary.json`。摘要写入失败只产生不含异常正文的 `cost_summary_diagnostic` 事件，不中断主生成任务。

摘要以 `(provider, model)` 分组。只有输入、缓存输入和输出 token 都由 Provider 报告，且快照中有对应 model 价格时，该 Attempt 的金额才是 `actual`。字段缺失为 `partial`，缺少创建时快照或价格为 `unavailable`；已知金额仅累加能完整计价的 Attempt，TTS 金额仍未知。旧 Job 没有完整创建快照时，不使用今天的配置补价。

`bookcast cost <output>` 持 Job 锁读取 manifest、创建快照和本 Job usage 视图，以 journal 中的 Attempt 重新生成摘要；不接受 `--config`。`job_status` 和 Web DTO 只暴露通过 `job_id`、`output_id`、源文件 SHA、快照、选择及 journal 指纹验证的摘要；身份不符或源文件变化时返回空摘要。此金额是记录内已知部分，不能代替 Provider 账单。
