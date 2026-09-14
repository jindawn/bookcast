# Phase 5 实际验证

验证时间：2026-09-14T14:36:05Z；Python 3.12、macOS、本地文件系统和 FFmpeg。默认 Mock，无网络或 API key。

## 自动测试

`.venv/bin/python -m pytest -q`：204 passed、10 subtests passed；5 个既有 PyMuPDF/SWIG 弃用警告。17 项新增测试位于 [test_job_recovery.py](../tests/test_job_recovery.py) 与 [test_job_cli.py](../tests/test_job_cli.py)。

真实子进程在以下位置等待标记文件后由父进程执行 kill，而非仅抛出普通异常：

- 第七章 analysis 调用进行中：前六章复用，B 从第七章继续。
- 第七章 Attempt 已持久化成功、Step 仍 RUNNING：第七章也直接复用，从第八章继续。
- parse 进行中：源文件已导入，原路径移走后仍能解析恢复，并保留来源 seed。

每个场景都先在进程持锁时尝试并发恢复，确认拒绝且 manifest 不变；kill 后只读 status 标为 stale，不改文件；恢复完成后 owner 清空、Job 为 SUCCEEDED。测试在结束时清理自己启动的子进程。复现：

```sh
.venv/bin/python -m pytest tests/test_job_recovery.py -k sigkill -q
.venv/bin/python -m pytest tests/test_job_recovery.py tests/test_job_cli.py -q
```

其他验证涵盖 A 第七章额度耗尽、B 接管且前六章文件字节/mtime 不变；永久错误不被普通 resume 重试；同名 Provider 配置和提示版本改变使缓存失效；损坏脚本只重建必要调用；新规划将过时失败步骤标为 SKIPPED；CLI 配置快照、Job ID、旧任务原样备份、损坏任务隔离及 BrokenPipe 进度回调。Phase 2/4 既有 timeout/failover 与内容质量回归仍全部通过。

没有进行真实电脑重启、Windows、共享文件系统或真实 AI 计费测试；SIGKILL 验证的是进程被强制终止后的磁盘状态与锁释放。

## 实际 CLI demo

```sh
.venv/bin/bookcast generate examples/content-demo.txt --mode two_host --minutes 6 --output-dir output/phase5-demo
.venv/bin/bookcast jobs --output-dir output/phase5-demo --json
.venv/bin/bookcast status 68a1af3368a145c7b402440866501672 --output-dir output/phase5-demo --json
.venv/bin/bookcast resume 68a1af3368a145c7b402440866501672 --output-dir output/phase5-demo
.venv/bin/bookcast retry 68a1af3368a145c7b402440866501672 --output-dir output/phase5-demo
.venv/bin/bookcast doctor --output-dir output/phase5-demo
```

实际目录：`output/phase5-demo/5bdad5ca96f5e42cd019ff30/`（Git 忽略）。Job ID 为 `68a1af3368a145c7b402440866501672`；book_id 为 `5bdad5ca96f5e42cd019ff30`。新机器复现会产生不同随机 Job ID，需使用本机 jobs 输出。

- manifest v3、状态 SUCCEEDED；27 个完成步骤，剩余 0；3/3 章分析完成。
- 30 个 Artifact、16 次完成 AI 调用；调用包括 Mock LLM 和 Mock TTS。
- jobs/status/resume/retry/doctor 均退出 0；doctor ready=true。
- 执行以上只读/恢复命令前后，32 个文件（排除锁文件）的 SHA-256 与纳秒 mtime 全相同；AI 调用数保持 16，没有新增调用。
- ffprobe：MP3、24 kHz、单声道、15.25 秒；这是测试音调，不是真实人声。内容质量仍受 [CONTENT.md](CONTENT.md) 的限制。

manifest SHA-256：`2cc1fa72c2f355e92dcb11a640de26b642816f447d3f33e82b34f8a6708b4bcf`。MP3 SHA-256：`85bd0e272e9c8ca1375ba5fd3d0c482a791e0035eed34a9323eb8bab133f4d13`。这些哈希只描述本次产物，不要求不同机器的编码器/随机任务 ID 产生相同字节。

Git 上最终验证的功能提交见 STATE 的 last_verified_commit 和 HANDOFF；此文件不自引用其所属提交。
