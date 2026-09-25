"use client";

import { useEffect, useRef, useState } from "react";

type Mode = "summary" | "deep_read" | "two_host";
type Candidate = {
  id: string;
  identity: {
    title: string;
    authors: string[];
    language: string | null;
    edition: string | null;
    publication_year: number | null;
  };
};
type Job = {
  id: string;
  title: string;
  mode: Mode;
  minutes: number;
  state: string;
  active: boolean;
  created_at: string;
  directory: string;
  core_job_id: string | null;
  error: string | null;
  warnings: string[];
  audio_url: string | null;
  m4b_url: string | null;
  audio_kind?: string;
  audio_seconds?: number | null;
  task_providers?: { llm: string[]; tts: string[] };
  can_resume: boolean;
  can_retry: boolean;
  progress: {
    stage: string;
    provider: string | null;
    completed: number;
    remaining: number;
    total_final: boolean;
    chapters_completed: number;
    chapters_total: number;
  } | null;
};
type Provider = {
  provider: string;
  model: string;
  kind: string;
  availability: string;
  last_error: string | null;
  capabilities: { mock?: boolean };
};
type Onboarding = {
  selected: Record<"llm" | "tts", {
    name: string; model: string; mode: string; experimental: boolean;
    available: boolean; real: boolean;
  }>;
  real_voice: boolean;
  ready: boolean;
  missing_steps: string[];
  privacy: string;
};
const modes = [
  {
    id: "summary" as Mode,
    name: "摘要",
    mark: "01",
    detail: "抓住全书核心观点",
  },
  {
    id: "deep_read" as Mode,
    name: "精读",
    mark: "02",
    detail: "走进论证与细节",
  },
  {
    id: "two_host" as Mode,
    name: "双人播客",
    mark: "03",
    detail: "在讲解与追问间理解",
  },
];
const states: Record<string, string> = {
  PENDING: "等待开始",
  RUNNING: "正在生成",
  SUCCEEDED: "已完成",
  FAILED_RETRYABLE: "可恢复",
  FAILED_PERMANENT: "需要处理",
};

function stageLabel(stage: string) {
  const prefix = stage.split(/[:/]/)[0];
  return (
    (
      {
        completed: "生成完成",
        parse: "解析书籍",
        analysis: "章节分析",
        synthesis: "内容综合",
        book_synthesis: "全书综合",
        plan: "节目规划",
        script: "对话写作",
        consistency: "一致性检查",
        tts: "合成音频",
        merge: "合并音频",
        quality: "质量检查",
      } as Record<string, string>
    )[prefix] || "处理书籍"
  );
}

async function api<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok)
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : "操作未成功，请检查服务并重试。",
    );
  return data;
}

function Wave({ small = false }: { small?: boolean }) {
  return (
    <div className={`wave ${small ? "small" : ""}`} aria-hidden="true">
      {[
        16, 32, 48, 25, 65, 85, 52, 34, 72, 100, 63, 40, 80, 52, 26, 46, 63, 30,
        16,
      ].map((height, i) => (
        <i key={i} style={{ height: `${height}%` }} />
      ))}
    </div>
  );
}

export default function Home() {
  const [tab, setTab] = useState<"file" | "title">("file");
  const [file, setFile] = useState<File | null>(null);
  const [upload, setUpload] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [search, setSearch] = useState<{
    search_id: string;
    candidates: Candidate[];
    warnings: string[];
    complete: boolean;
  } | null>(null);
  const [edition, setEdition] = useState("");
  const [mode, setMode] = useState<Mode>("two_host");
  const [minutes, setMinutes] = useState(40);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [onboarding, setOnboarding] = useState<Onboarding | null>(null);
  const [error, setError] = useState("");
  const [connection, setConnection] = useState("");
  const [busy, setBusy] = useState("");
  const [providerBusy, setProviderBusy] = useState(false);
  const [historyErrors, setHistoryErrors] = useState<string[]>([]);
  const submission = useRef<{ payload: string; key: string } | null>(null);
  const current = selected ? jobs.find((job) => job.id === selected) : jobs[0];
  const selectedUnavailable = selected !== null && !current;

  async function refresh() {
    const data = await api<{ jobs: Job[]; errors: string[] }>("/api/jobs");
    setJobs(data.jobs);
    setHistoryErrors(data.errors);
    setConnection("");
  }
  async function checkProviders() {
    setProviderBusy(true);
    try {
      const status = await api<{ providers: Provider[]; onboarding: Onboarding }>("/api/providers");
      setProviders(status.providers);
      setOnboarding(status.onboarding);
    } catch (e) {
      setError(String((e as Error).message));
    } finally {
      setProviderBusy(false);
    }
  }
  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const data = await api<{ jobs: Job[]; errors: string[] }>("/api/jobs");
        if (!stopped) {
          setJobs(data.jobs);
          setHistoryErrors(data.errors);
          setConnection("");
        }
      } catch {
        if (!stopped)
          setConnection(
            "暂时无法连接本地服务。任务记录仍保存在本地，连接恢复后会自动刷新。",
          );
      }
      if (!stopped) timer = setTimeout(poll, 1500);
    }
    void poll();
    void checkProviders();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, []);

  async function findBook() {
    setBusy("search");
    setError("");
    setSearch(null);
    setEdition("");
    try {
      setSearch(
        await api(
          `/api/books/search?title=${encodeURIComponent(title.trim())}`,
        ),
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy("");
    }
  }
  async function generate() {
    setBusy("generate");
    setError("");
    try {
      let uploadId = upload;
      if (tab === "file" && file && !uploadId) {
        const result = await api<{ upload_id: string }>(
          `/api/uploads?filename=${encodeURIComponent(file.name)}`,
          {
            method: "POST",
            headers: {
              "Content-Type": file.type || "application/octet-stream",
            },
            body: file,
          },
        );
        uploadId = result.upload_id;
        setUpload(uploadId);
      }
      const payload = JSON.stringify({
        ...(tab === "file"
          ? { upload_id: uploadId }
          : { search_id: search?.search_id, edition }),
        mode,
        minutes,
      });
      if (submission.current?.payload !== payload)
        submission.current = {
          payload,
          key: crypto.randomUUID().replaceAll("-", ""),
        };
      const job = await api<Job>("/api/jobs", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": submission.current.key,
        },
        body: payload,
      });
      setSelected(job.id);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy("");
    }
  }
  async function recover(job: Job, action: "resume" | "retry") {
    setBusy(action);
    setError("");
    try {
      await api(`/api/jobs/${job.id}/${action}`, { method: "POST" });
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy("");
    }
  }

  return (
    <div className="shell">
      <aside className="sidebar">
        <a href="/" className="brand" aria-label="BookCast 首页">
          <span className="brand-icon">
            b<span>c</span>
          </span>
          <strong>BookCast</strong>
        </a>
        <p className="local">
          <span /> 本地工作空间
        </p>
        <nav>
          <a className="nav-active" href="#create">
            ＋ 创建播客
          </a>
          <a href="#library">
            ▤ 我的声音书架 <small>{jobs.length}</small>
          </a>
        </nav>
        <div className="sidebar-note">
          <Wave small />
          <p>
            让一本好书，
            <br />
            成为一段好对话。
          </p>
          <span>你的书籍与产物保存在本机。</span>
        </div>
        <div className="sidebar-bottom">
          BOOKCAST · LOCAL FIRST
          <br />
          <span>开源 / 可恢复 / 自由选择模型</span>
        </div>
      </aside>
      <main>
        <header className="topbar">
          <span>
            声音工作室 <b>/</b> 创建播客
          </span>
          <span className="local">
            <span /> 本地工作空间
          </span>
        </header>
        <section className="intro" id="create">
          <div>
            <p className="eyebrow">FROM PAGES TO CONVERSATIONS</p>
            <h1>
              让阅读，有回声<span>。</span>
            </h1>
            <p>从一本书出发，听见观点之间的对话。</p>
          </div>
          <Wave />
        </section>
        {onboarding && (
          <div className={`notice ${onboarding.ready && onboarding.real_voice ? "" : "error"}`} role="status">
            <strong>当前配置：</strong> LLM {onboarding.selected.llm.name}（{onboarding.selected.llm.mode}） ·
            TTS {onboarding.selected.tts.name}（{onboarding.selected.tts.mode}
            {onboarding.selected.tts.experimental ? "，实验性" : ""}；
            {onboarding.real_voice ? "真实人声" : "测试音调"}）
            {onboarding.missing_steps.map((step) => <p key={step}>✗ {step}</p>)}
            {!onboarding.real_voice && <p>当前可试用完整流程。要生成真实语音，请在本机终端运行 bookcast setup，并按 README 的 5 分钟指南配置。</p>}
            {(onboarding.selected.llm.mode === "云端" || onboarding.selected.tts.mode === "云端") &&
              <p>{onboarding.privacy}</p>}
          </div>
        )}
        {connection && (
          <div role="status" className="notice error">
            {connection}
          </div>
        )}
        {error && (
          <div role="alert" className="notice error">
            {error}
            <button onClick={() => setError("")} aria-label="关闭错误提示">
              ×
            </button>
          </div>
        )}
        <div className="workspace">
          <section className="card creation" aria-labelledby="create-heading">
            <div className="section-heading">
              <h2 id="create-heading">创建你的下一期</h2>
              <span className="tag">中文内容</span>
            </div>
            <label className="step-label">
              <b>1</b> 选择一本书
            </label>
            <div className="tabs" role="tablist" aria-label="书籍来源">
              <button
                role="tab"
                aria-selected={tab === "file"}
                onClick={() => setTab("file")}
              >
                上传文件
              </button>
              <button
                role="tab"
                aria-selected={tab === "title"}
                onClick={() => setTab("title")}
              >
                搜索书名
              </button>
            </div>
            {tab === "file" ? (
              <label className={`upload ${file ? "has-file" : ""}`}>
                <span className="upload-icon">↑</span>
                <strong>{file?.name || "选择你的电子书"}</strong>
                <span>
                  {file
                    ? `${(file.size / 1024).toFixed(1)} KB · 点击更换`
                    : "EPUB、PDF 或 TXT · 最大 32 MiB"}
                </span>
                <input
                  aria-label="上传电子书"
                  type="file"
                  accept=".epub,.pdf,.txt"
                  onChange={(e) => {
                    setFile(e.target.files?.[0] ?? null);
                    setUpload(null);
                    submission.current = null;
                  }}
                />
              </label>
            ) : (
              <div className="book-search">
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    void findBook();
                  }}
                >
                  <input
                    aria-label="书名"
                    placeholder="例如 The Wealth of Nations"
                    value={title}
                    onChange={(e) => {
                      setTitle(e.target.value);
                      setSearch(null);
                      setEdition("");
                    }}
                    maxLength={300}
                  />
                  <button disabled={!!busy || !title.trim()}>
                    {busy === "search" ? "检索中…" : "查找版本"}
                  </button>
                </form>
                <p className="hint">
                  仅检索 Project Gutenberg
                  合法公开目录；中文译名未必匹配，请尝试原书名。首次检索需要下载目录。
                </p>
                {search && (
                  <div
                    className="candidates"
                    role="radiogroup"
                    aria-label="选择书籍版本"
                  >
                    {!search.candidates.length && (
                      <p>没有匹配版本，请细化书名或上传自己的文件。</p>
                    )}
                    {search.candidates.map((c) => (
                      <label key={c.id}>
                        <input
                          type="radio"
                          name="edition"
                          value={c.id}
                          checked={edition === c.id}
                          onChange={() => setEdition(c.id)}
                        />
                        <span>
                          <strong>{c.identity.title}</strong>
                          <small>
                            {c.identity.authors.join(" · ") || "作者未知"} ·{" "}
                            {c.identity.language || "语言未知"} · {c.id}
                          </small>
                          <small>
                            {c.identity.edition || "版次未知"} /{" "}
                            {c.identity.publication_year || "出版年未知"}
                          </small>
                        </span>
                      </label>
                    ))}
                    {!search.complete && <p>候选未展示完整，请细化书名。</p>}
                  </div>
                )}
                <p className="hint">
                  来源声明以美国公有领域为依据；请核对所在地适用条件。下载前仍由
                  Core 验证版权与版本。
                </p>
              </div>
            )}
            <p className="hint">
              请使用自己有权处理的书籍。扫描版 PDF 暂不支持 OCR。
            </p>
            <label className="step-label">
              <b>2</b> 你想怎样听？
            </label>
            <div className="modes" role="radiogroup" aria-label="生成模式">
              {modes.map((item) => (
                <button
                  key={item.id}
                  role="radio"
                  aria-checked={mode === item.id}
                  className={mode === item.id ? "chosen" : ""}
                  onClick={() => setMode(item.id)}
                >
                  <span>
                    {item.mark}
                    <i>{mode === item.id ? "●" : "○"}</i>
                  </span>
                  <strong>{item.name}</strong>
                  <small>{item.detail}</small>
                </button>
              ))}
            </div>

            <label className="step-label">
              <b>3</b> 语音引擎
            </label>
            <div className="modes" role="radiogroup" aria-label="语音引擎">
              <button
                role="radio"
                aria-checked={ttsEngine === "auto"}
                className={ttsEngine === "auto" ? "chosen" : ""}
                onClick={() => setTtsEngine("auto")}
              >
                <span><i>{ttsEngine === "auto" ? "●" : "○"}</i></span>
                <strong>默认</strong>
                <small>使用 bookcast.toml 优先级</small>
              </button>
              <button
                role="radio"
                aria-checked={ttsEngine === "gemini"}
                className={ttsEngine === "gemini" ? "chosen" : ""}
                onClick={() => setTtsEngine("gemini")}
              >
                <span><i>{ttsEngine === "gemini" ? "●" : "○"}</i></span>
                <strong>Gemini — 高质量云端</strong>
                <small>模型：Gemini 3.8 Flash TTS · 主持人：Kore · 嘉宾：Puck</small>
              </button>
              <button
                role="radio"
                aria-checked={ttsEngine === "kokoro"}
                className={ttsEngine === "kokoro" ? "chosen" : ""}
                onClick={() => setTtsEngine("kokoro")}
              >
                <span><i>{ttsEngine === "kokoro" ? "●" : "○"}</i></span>
                <strong>Kokoro — 本地免费</strong>
                <small>完全离线的本地人声合成引擎</small>
              </button>
            </div>
            
            <div className="duration">
              <label className="step-label" htmlFor="minutes">
                <b>4</b> 目标时长
              </label>

              <div>
                <input
                  id="minutes"
                  type="number"
                  min={1}
                  max={120}
                  value={minutes}
                  onChange={(e) => setMinutes(Number(e.target.value))}
                />
                <span>分钟</span>
              </div>
            </div>
            <p className="hint">
              这是脚本内容预算，实际播放时长由脚本长度、音色和语速决定。
            </p>
            <button
              className="primary generate"
              disabled={
                !!busy ||
                minutes < 1 ||
                minutes > 120 ||
                !Number.isInteger(minutes) ||
                (tab === "file" ? !file : !edition)
              }
              onClick={generate}
            >
              {busy === "generate" ? "正在提交…" : "生成播客"} <span>↗</span>
            </button>
            <p className="footnote">每一步自动保存，随时可以回来继续。</p>
          </section>
          <div className="right-column">
            <section className="card now" aria-labelledby="now-heading">
              <div className="section-heading">
                <h2 id="now-heading">
                  {current ? "任务动态" : "等候一段好对话"}
                </h2>
                <span
                  className={`status ${current?.state === "SUCCEEDED" ? "success" : ""}`}
                >
                  {current
                    ? states[current.state] || current.state
                    : "准备就绪"}
                </span>
              </div>
              {current ? (
                <>
                  <div className="episode-art">
                    <Wave />
                    <span>
                      {modes.find((m) => m.id === current.mode)?.name}
                    </span>
                  </div>
                  <h3>{current.title}</h3>
                  <p className="muted">
                    目标 {current.minutes} 分钟 ·{" "}
                    {modes.find((m) => m.id === current.mode)?.name}
                  </p>
                  {current.task_providers && (
                    <p className="muted">
                      本任务配置：LLM {current.task_providers.llm.join(" → ") || "未知"} · TTS {current.task_providers.tts.join(" → ") || "未知"}
                    </p>
                  )}
                  {current.progress ? (
                    <div className="progress">
                      <div>
                        <strong>{stageLabel(current.progress.stage)}</strong>
                        <span>{current.progress.completed} 步完成</span>
                      </div>
                      <progress
                        aria-label="任务进度"
                        value={current.progress.completed}
                        max={Math.max(
                          1,
                          current.progress.completed +
                            current.progress.remaining,
                        )}
                      />
                      <p>
                        章节 {current.progress.chapters_completed}/
                        {current.progress.chapters_total} · 剩余{" "}
                        {current.progress.remaining} 步
                        {!current.progress.total_final && "（规划中）"}
                      </p>
                      <p>最近调用：{current.progress.provider || "等待调用"}</p>
                    </div>
                  ) : (
                    <p className="hint">
                      正在准备输入或获取书籍；中间结果会自动保存。
                    </p>
                  )}
                  {current.error && (
                    <p className="notice error">{current.error}</p>
                  )}
                  {current.can_resume && (
                    <button
                      className="primary"
                      disabled={!!busy}
                      onClick={() => recover(current, "resume")}
                    >
                      从断点恢复
                    </button>
                  )}
                  {current.can_retry && (
                    <div className="retry">
                      <p>
                        请先修复错误。重试沿用任务的配置快照；若需更换 Provider
                        参数，请通过 CLI retry 和 --config 指定新配置。
                      </p>
                      <button
                        disabled={!!busy}
                        onClick={() => recover(current, "retry")}
                      >
                        已修复，重试任务
                      </button>
                    </div>
                  )}
                  {current.audio_url && (
                    <div className="player">
                      <p>{current.audio_kind === "speech" ? "合成人声" : current.audio_kind === "mock" ? "Mock 测试音调（非人声）" : "请核对音频来源记录"}
                        {current.audio_seconds != null && ` · ${Math.round(current.audio_seconds)} 秒`}</p>
                      <audio
                        aria-label="播客音频"
                        controls
                        preload="metadata"
                        src={current.audio_url}
                      />
                      <a href={current.audio_url} download="podcast.mp3">
                        下载 MP3 ↓
                      </a>
                      {current.m4b_url && (
                        <a href={current.m4b_url} download="podcast.m4b">
                          下载 M4B ↓
                        </a>
                      )}
                    </div>
                  )}
                  {current.cost_summary && (
                    <div className="cost-summary" style={{ marginTop: '1.5rem', padding: '1rem', background: 'var(--bg-card)', borderRadius: '8px', border: '1px solid var(--border)' }}>
                      <h4 style={{ margin: '0 0 0.5rem' }}>本期生成成本</h4>
                      
                      {Object.keys(current.cost_summary.llm.providers).map(prov => (
                        <div key={prov} style={{ display: 'flex', justifyContent: 'space-between', color: prov === current.cost_summary.llm.current_provider ? 'inherit' : 'var(--muted)' }}>
                          <span>{prov} {prov === current.cost_summary.llm.current_provider ? '' : '(历史)'}</span>
                          <span>{current.cost_summary.llm.providers[prov].cost.status === 'unavailable' ? '未统计' : `¥${current.cost_summary.llm.providers[prov].cost.amount.toFixed(2)}`}</span>
                        </div>
                      ))}
                      
                      {Object.keys(current.cost_summary.tts.providers).map(prov => (
                        <div key={prov} style={{ display: 'flex', justifyContent: 'space-between', color: prov === current.cost_summary.tts.current_provider ? 'inherit' : 'var(--muted)' }}>
                          <span>{prov} {prov === current.cost_summary.tts.current_provider ? '' : '(历史)'}</span>
                          <span>{current.cost_summary.tts.providers[prov].cost.status === 'unavailable' ? '未统计' : `¥${current.cost_summary.tts.providers[prov].cost.amount.toFixed(2)}`}</span>
                        </div>
                      ))}
                      
                      <hr style={{ margin: '0.5rem 0', borderColor: 'var(--border)' }} />
                      <div style={{ display: 'flex', justifyContent: 'space-between', fontWeight: 'bold' }}>
                        <span>已知成本</span>
                        <span>¥{current.cost_summary.total.known_amount.toFixed(2)}</span>
                      </div>
                      
                      <details style={{ marginTop: '1rem' }}>
                        <summary style={{ cursor: 'pointer', color: 'var(--primary)' }}>查看明细</summary>
                        
                        <div style={{ marginTop: '1rem', fontSize: '0.9em' }}>
                          <strong>LLM Token</strong>
                          {Object.keys(current.cost_summary.llm.providers).map(prov => (
                             <div key={prov} style={{ marginBottom: '0.5rem', color: prov === current.cost_summary.llm.current_provider ? 'inherit' : 'var(--muted)' }}>
                               <div>{prov} {prov === current.cost_summary.llm.current_provider ? '' : '(历史)'}</div>
                               <div style={{ display: 'flex', justifyContent: 'space-between' }}><span>缓存输入</span><span>{current.cost_summary.llm.providers[prov].usage.cached_input_tokens}</span></div>
                               <div style={{ display: 'flex', justifyContent: 'space-between' }}><span>未缓存输入</span><span>{current.cost_summary.llm.providers[prov].usage.uncached_input_tokens}</span></div>
                               <div style={{ display: 'flex', justifyContent: 'space-between' }}><span>输出</span><span>{current.cost_summary.llm.providers[prov].usage.output_tokens}</span></div>
                               <div style={{ display: 'flex', justifyContent: 'space-between' }}><span>请求数</span><span>{current.cost_summary.llm.providers[prov].requests}</span></div>
                             </div>
                          ))}
                          
                          <strong style={{ display: 'block', marginTop: '0.5rem' }}>TTS</strong>
                          {Object.keys(current.cost_summary.tts.providers).map(prov => (
                             <div key={prov} style={{ marginBottom: '0.5rem', color: prov === current.cost_summary.tts.current_provider ? 'inherit' : 'var(--muted)' }}>
                               <div>{prov} {prov === current.cost_summary.tts.current_provider ? '' : '(历史)'}</div>
                               <div style={{ display: 'flex', justifyContent: 'space-between' }}><span>请求数</span><span>{current.cost_summary.tts.providers[prov].request_count}</span></div>
                               <div style={{ display: 'flex', justifyContent: 'space-between' }}><span>重试</span><span>{current.cost_summary.tts.providers[prov].retry_count}</span></div>
                               <div style={{ display: 'flex', justifyContent: 'space-between' }}><span>音频时长</span><span>{Math.round(current.cost_summary.tts.providers[prov].audio_duration_seconds)}s</span></div>
                             </div>
                          ))}
                        </div>
                      </details>
                      
                      {current.cost_summary.diagnostics?.map((msg, idx) => (
                        <p key={idx} className="notice error" style={{ marginTop: '0.5rem', marginBottom: 0 }}>{msg}</p>
                      ))}
                    </div>
                  )}}
                  <details>
                    <summary>产物与任务信息</summary>
                    <p>Core ID：{current.core_job_id || "尚未创建"}</p>
                    <code>{current.directory}</code>
                    <p>可通过 CLI 使用此目录查看或恢复。</p>
                  </details>
                  {current.warnings.length > 0 && (
                    <details>
                      <summary>
                        内容与解析提示（{current.warnings.length}）
                      </summary>
                      <ul>
                        {current.warnings.map((w) => (
                          <li key={w}>{w}</li>
                        ))}
                      </ul>
                    </details>
                  )}
                </>
              ) : (
                <div className="empty">
                  <div className="empty-book">▥</div>
                  <h3>一本书，无限种听法</h3>
                  <p>
                    选择书籍和模式后，
                    <br />
                    在这里查看生成进度与音频。
                  </p>
                  <span>无需 API Key，也能体验完整流程</span>
                </div>
              )}
            </section>
            <section className="card providers">
              <div className="section-heading">
                <h2>Provider 状态</h2>
                <button
                  className="text-button"
                  disabled={providerBusy}
                  onClick={checkProviders}
                >
                  {providerBusy ? "检查中…" : "重新检查"}
                </button>
              </div>
              {providers.map((p) => (
                <div className="provider" key={`${p.kind}-${p.provider}`}>
                  <span
                    className={`dot ${p.availability === "available" ? "green" : ""}`}
                  />
                  <div>
                    <strong>{p.provider}</strong>
                    <small>
                      {p.kind.toUpperCase()} · {p.model}
                    </small>
                    {p.last_error && (
                      <small className="error-text">{p.last_error}</small>
                    )}
                  </div>
                  <span className="tag">
                    {p.availability === "available"
                      ? "可用"
                      : p.availability === "unavailable"
                        ? "不可用"
                        : "待验证"}
                  </span>
                </div>
              ))}
              <p className="mock-note">使用本机终端运行 bookcast setup 选择方案、bookcast doctor --human 检查；网页不会收集或保存 API Key。</p>
            </section>
          </div>
        </div>
        <section className="library" id="library">
          <div className="section-heading">
            <div>
              <p className="eyebrow">YOUR LISTENING LIBRARY</p>
              <h2>
                我的声音书架 <span>{jobs.length}</span>
              </h2>
            </div>
            <span className="muted">保存在本地，随时继续</span>
          </div>
          {historyErrors.map((e, i) => (
            <p role="alert" key={i} className="notice error">
              {e}
            </p>
          ))}
          {selectedUnavailable && (
            <p role="alert" className="notice error">
              当前选中的任务暂时无法读取，请检查任务记录并刷新页面；不会显示其他任务的音频。
            </p>
          )}
          {!jobs.length ? (
            <div className="library-empty">你的第一期播客，将从这里开始。</div>
          ) : (
            <div className="job-list">
              {jobs.map((job) => (
                <button
                  className={`job-card ${current?.id === job.id ? "selected" : ""}`}
                  key={job.id}
                  onClick={() => {
                    setSelected(job.id);
                    document
                      .getElementById("now-heading")
                      ?.scrollIntoView({ behavior: "smooth", block: "center" });
                  }}
                >
                  <span className="job-cover">
                    {job.title.slice(0, 1).toUpperCase()}
                  </span>
                  <div>
                    <strong>{job.title}</strong>
                    <small>
                      {modes.find((m) => m.id === job.mode)?.name} · 目标{" "}
                      {job.minutes} 分钟
                    </small>
                    <span
                      className={`status ${job.state === "SUCCEEDED" ? "success" : ""}`}
                    >
                      {states[job.state] || job.state}
                    </span>
                  </div>
                  <span className="arrow">↗</span>
                </button>
              ))}
            </div>
          )}
        </section>
        <footer>
          每一个声音，都有书可循。<span>BookCast / 本地声音工作室</span>
        </footer>
      </main>
    </div>
  );
}
