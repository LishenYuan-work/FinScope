"use client";

import { useEffect, useState } from "react";
import { CaretRight, DownloadSimple, FileText, Plus, X } from "@phosphor-icons/react";
import { authFetch, getAccessToken } from "../../lib/auth";

type PageData = { title: string; sub: string; body: string };

type Status = "pending" | "parsing" | "finance_analyzing" | "risk_analyzing" | "summary_finish" | "error";
type TaskSummary = { id: string; filename: string; status: Status; created_at?: string; exampleId?: string };
type Source = { page?: number; chunk_id?: string };
type ReportData = { company?: string; period?: string; metrics?: Record<string, unknown>; risks?: Array<{ level?: string; title?: string; evidence?: string; suggestion?: string; sources?: Source[] }>; summary?: string; sources?: Source[] };
type TaskResult = { task_id: string; status: Status; report_markdown?: string; report_json?: ReportData; error?: string };
const EXAMPLE_PDFS: Record<string, string> = { alibaba: "/examples/alibaba-fy2025-annual-report.pdf", tencent: "/examples/tencent-2025-annual-report.pdf", baidu: "/examples/baidu-2025-annual-report.pdf" };
const configuredApiBase = process.env.NEXT_PUBLIC_API_BASE_URL?.trim();
const isLocalApiBase = !configuredApiBase || /^(https?:\/\/)?(localhost|127\.0\.0\.1)(:\d+)?\/?$/i.test(configuredApiBase);
const API_BASE = process.env.VERCEL && isLocalApiBase ? "https://finscope-backend-637y.onrender.com" : configuredApiBase ?? "http://localhost:8000";
function apiBaseUrl() { return typeof window !== "undefined" ? window.__FINSCOPE_CONFIG__?.apiBase?.replace(/\/$/, "") || API_BASE : API_BASE; }
declare global { interface Window { __FINSCOPE_CONFIG__?: { apiBase?: string } } }
function metricNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "object" && value !== null) {
    const object = value as Record<string, unknown>;
    for (const key of ["数值", "金额", "本期", "当前值", "amount", "number", "current", "value"]) {
      const candidate = object[key];
      if (typeof candidate === "number" && Number.isFinite(candidate)) return candidate;
      if (typeof candidate === "string") { const match = candidate.match(/[-+]?\d[\d,]*(?:\.\d+)?/); if (match) return Number(match[0].replaceAll(",", "")); }
    }
  }
  if (typeof value === "string") { const match = value.match(/^\s*[-+]?\d[\d,]*(?:\.\d+)?/); if (match) return Number(match[0].replaceAll(",", "")); }
  return null;
}
function metricUnit(key: string, value: unknown): string {
  if (value && typeof value === "object") { const unit = (value as Record<string, unknown>).unit; if (typeof unit === "string" && unit.trim()) return unit.trim(); }
  const text = `${key} ${typeof value === "string" ? value : JSON.stringify(value ?? "")}`;
  if (/%|率|同比|环比|margin|ratio|growth/i.test(text)) return "%";
  if (/亿|万元|元|金额|收入|利润|现金流|revenue|income|profit|cash|amount/i.test(text)) return "亿元";
  return "";
}
function metricDelta(value: unknown): { text: string; down: boolean } {
  if (value && typeof value === "object") { const object = value as Record<string, unknown>; const raw = object.yoy ?? object["同比变化"] ?? object["同比"]; if (typeof raw === "string" && raw.trim()) return { text: `同比 ${raw}`, down: raw.includes("-") || raw.includes("下降") }; }
  return { text: "结构化指标", down: false };
}
function firstPage(value: unknown): number | undefined {
  if (!value || typeof value !== "object") return undefined;
  const sources = (value as Record<string, unknown>).sources;
  if (!Array.isArray(sources) || !sources[0] || typeof sources[0] !== "object") return undefined;
  const page = (sources[0] as Record<string, unknown>).page;
  return typeof page === "number" ? page : undefined;
}
function riskLevel(value?: string): "high" | "medium" | "low" {
  if (value && /高|high/i.test(value)) return "high";
  if (value && /低|low/i.test(value)) return "low";
  return "medium";
}
type Risk = { level: "high" | "medium" | "low"; title: string; desc: string; page: number };

const PAGE_DATA: Record<number, PageData> = {
  42: {
    title: "合并现金流量表",
    sub: "Nova Retail Group · FY2025 · 第 42 页",
    body: "人民币 21,236 百万元，较上年同期增长 12.6%，主要由于营运资金管理改善及会员预收款增加。",
  },
  57: {
    title: "销售费用与流量成本",
    sub: "Nova Retail Group · FY2025 · 第 57 页",
    body: "销售费用率为 17.4%，较上年上升 1.8 个百分点，主要由于线上流量成本及新客获取投入增加。",
  },
  63: {
    title: "存货与区域库存",
    sub: "Nova Retail Group · FY2025 · 第 63 页",
    body: "存货周转天数为 86 天，较上年上升 9 天；华北与华东区域库存周转明显慢于华南。",
  },
  88: {
    title: "金融工具与汇率风险",
    sub: "Nova Retail Group · FY2025 · 第 88 页",
    body: "海外收入占比提升至 14%，美元及欧元敞口未完全对冲，报告期内确认汇兑损失人民币 6.2 亿元。",
  },
};

const KPIS: Array<{ label: string; value: string; unit: string; delta: string; page: number; down?: boolean }> = [
  { label: "营业收入", value: "1,284", unit: "亿元", delta: "同比 +8.4%", page: 42 },
  { label: "毛利率", value: "38.6", unit: "%", delta: "同比 +1.2pp", page: 57 },
  { label: "经营现金流", value: "212", unit: "亿元", delta: "同比 +12.6%", page: 42 },
  { label: "净负债率", value: "18.4", unit: "%", delta: "同比 -3.1pp", down: true, page: 63 },
];

const RISKS: Risk[] = [
  { level: "high", title: "存货周转天数上升 9 天，区域库存分化", desc: "华北与华东库存周转明显慢于华南，若需求走弱可能带来跌价准备压力。", page: 63 },
  { level: "medium", title: "线上流量成本上升，销售费用率 +1.8pp", desc: "会员复购部分抵消，但新客获取成本仍在上行，利润弹性受限。", page: 57 },
  { level: "low", title: "海外业务汇率敞口扩大，汇兑损失 6.2 亿元", desc: "海外收入占比提升至 14%，美元/欧元敞口未完全对冲。", page: 88 },
];

const REPORT_LINES = [
  { n: "01", text: "收入结构与增长质量：海外与会员贡献提升", page: 42 },
  { n: "02", text: "盈利能力：毛利率改善被销售费用侵蚀", page: 57 },
  { n: "03", text: "现金流：经营现金流增长快于净利润", page: 42 },
  { n: "04", text: "风险：库存、流量成本与汇率敞口", page: 63 },
];

const EVIDENCE = [
  { tone: "", title: "经营现金流增长 12.6%", desc: "现金流增速快于收入，现金转换率 1.12。", meta: "p.42 · chunk_2184", page: 42 },
  { tone: "", title: "会员预收款增加", desc: "合同负债较上年末增加 18.2 亿元。", meta: "p.42 · chunk_2185", page: 42 },
  { tone: "warn", title: "销售费用率上升 1.8pp", desc: "流量成本与新客获取投入增加。", meta: "p.57 · chunk_3021", page: 57 },
];

export default function WorkspacePage() {
  const [page, setPage] = useState(42);
  const [unit, setUnit] = useState<"amount" | "ratio">("amount");
  const [tab, setTab] = useState<"pdf" | "chain">("pdf");
  const [modal, setModal] = useState(false);
  const [toast, setToast] = useState("");
  const [live, setLive] = useState<TaskResult | undefined>();
  const [liveTask, setLiveTask] = useState<TaskSummary | null>(null);
  const [pdfUrl, setPdfUrl] = useState("");
  const [status, setStatus] = useState<Status>("summary_finish");

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setModal(true);
      }
      if (event.key === "Escape") setModal(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    const token = getAccessToken();
    if (!token) return;
    void (async () => {
      try {
        const response = await authFetch(`${apiBaseUrl()}/tasks`);
        if (!response.ok) return;
        const list = (await response.json()) as TaskSummary[];
        if (!list[0]) return;
        setLiveTask(list[0]);
        const detail = await authFetch(`${apiBaseUrl()}/tasks/${list[0].id}`);
        if (!detail.ok) return;
        const data = (await detail.json()) as TaskResult;
        setLive(data);
        setStatus(data.status);
        if (list[0].exampleId && EXAMPLE_PDFS[list[0].exampleId]) setPdfUrl(EXAMPLE_PDFS[list[0].exampleId]);
        const sourcePage = data.report_json?.sources?.find((source) => typeof source.page === "number")?.page;
        if (sourcePage) setPage(sourcePage);
      } catch {
        /* keep demo view when API is not configured */
      }
    })();
  }, []);

  function locate(nextPage: number) {
    setPage(nextPage);
    setToast(`已定位到第 ${nextPage} 页`);
    window.setTimeout(() => setToast(""), 1800);
  }

  const current = PAGE_DATA[page] ?? PAGE_DATA[42];
  const report = live?.report_json;
  const company = report?.company || "Nova 零售集团";
  const summary = report?.summary || "收入增长 8.4%，主要由海外与会员业务贡献，国内同店增速仅 2.1%。毛利率改善 1.2 个百分点，但销售费用率上升 1.8 个百分点，利润弹性受限。经营现金流增长 12.6%，现金转换率 1.12，现金流质量好于利润表表现。";
  const displayKpis = report?.metrics && Object.keys(report.metrics).length > 0
    ? Object.entries(report.metrics).slice(0, 4).map(([key, value]) => {
        const numeric = metricNumber(value);
        const delta = metricDelta(value);
        return { label: key, value: numeric === null ? "—" : numeric.toLocaleString("zh-CN"), unit: metricUnit(key, value), delta: delta.text, down: delta.down, page: firstPage(value) ?? 42 };
      })
    : KPIS;
  const displayRisks = report?.risks && report.risks.length > 0
    ? report.risks.map((risk) => ({ level: riskLevel(risk.level), title: risk.title || "待确认风险", desc: risk.evidence || risk.suggestion || "暂无证据摘要", page: risk.sources?.[0]?.page ?? 42 }))
    : RISKS;
  const totalPages = pdfUrl ? "" : "128";

  return (
    <div className="fs2-app">
      <aside className="fs2-sidebar">
        <div className="fs2-brand">
          <div className="fs2-brand-mark"><FileText size={14} weight="bold" /></div>
          <div><div className="fs2-brand-name">FinScope</div><div className="fs2-brand-sub">RESEARCH DESK</div></div>
        </div>
        <button className="fs2-new-btn" onClick={() => setModal(true)}><Plus size={12} weight="bold" /> 新建分析 <span className="fs2-kbd">⌘ K</span></button>
        <div className="fs2-side-label">工作台</div>
        <div className="fs2-side-nav">
          <button className="fs2-side-item active"><FileText size={13} /> 当前分析 <span className="fs2-count">1</span></button>
          <button className="fs2-side-item" onClick={() => setToast("研究档案视图将在下一版接入")}><CaretRight size={13} /> 研究档案 <span className="fs2-count">12</span></button>
        </div>
        <div className="fs2-side-section"><span>最近</span><span>FY2025</span></div>
        <div className="fs2-task-list">
          <button className="fs2-task active"><strong>{liveTask?.filename || "Nova 零售集团 · FY2025"}</strong><span><i>{status === "summary_finish" ? "已完成" : status === "error" ? "异常" : "进行中"}</i><i>{live?.report_json?.period || "02:41"}</i></span></button>
          <button className="fs2-task" onClick={() => setToast("当前为 Nova 零售演示任务")}><strong>Horizon 制造 · FY2024</strong><span><i>已完成</i><i>03:08</i></span></button>
          <button className="fs2-task" onClick={() => setToast("当前为 Nova 零售演示任务")}><strong>Atlas 物流 · FY2024</strong><span><i>异常</i><i>01:12</i></span></button>
        </div>
        <div className="fs2-side-foot">
          <div className="fs2-demo">DEMO DATA</div>
          本页为高保真原型，财务数据为虚构演示数据，不对应任何真实公司披露。
        </div>
      </aside>

      <header className="fs2-topbar">
        <div className="fs2-crumbs"><span>工作台</span><b>/</b><span>当前分析</span><b>/</b><strong>{liveTask?.filename || "Nova 零售集团 · FY2025"}</strong></div>
        <div className="fs2-top-actions">
          <span className="fs2-status"><i /> {status === "summary_finish" ? "已完成" : status === "error" ? "异常" : "运行中"} · {live?.report_json?.period || "FY2025"}</span>
          <button className="fs2-btn" onClick={() => window.print()}><DownloadSimple size={12} /> 导出笔记</button>
          <button className="fs2-btn primary" onClick={() => setModal(true)}><Plus size={12} weight="bold" /> 新建分析</button>
          <div className="fs2-avatar">LS</div>
        </div>
      </header>

      <main className="fs2-main">
        <div className="fs2-content">
          <section className="fs2-report-head">
            <div>
              <div className="fs2-eyebrow">EQUITY RESEARCH / {live?.report_json?.period || "FY2025"}</div>
              <h1>{company}</h1>
              <p className="fs2-sub">收入增长放缓，但现金流质量改善</p>
              <div className="fs2-meta">
                <span className="fs2-pill fs2-demo-pill">{live ? "LIVE REPORT" : "演示数据"}</span>
                <span>{live?.report_json?.period || "FY2025"} 年报</span><span>·</span><span>4 Agents</span><span>·</span><span>{liveTask?.filename || "生成于 2026-10-27 14:32"}</span>
              </div>
            </div>
            <div className="fs2-head-right">
              <span className="fs2-stamp">REPORT / 01</span>
              <button className="fs2-export" onClick={() => window.print()}>导出研究笔记 ↗</button>
            </div>
          </section>

          <section className="fs2-agent-strip" aria-label="Agent 执行流水线">
            {[
              ["01", "文档解析", "00:38"],
              ["02", "财务分析", "00:52"],
              ["03", "风险研判", "00:41"],
              ["04", "报告汇总", "00:30"],
            ].map(([idx, name, time], index) => (
              <div className={`fs2-agent ${index === 3 ? "active" : ""}`} key={idx}>
                <span className="fs2-idx">{idx}</span><span className="fs2-name">{name}</span>
                <span className="fs2-time"><i>完成</i><i>{time}</i></span>
                <span className="fs2-bar"><i /></span>
              </div>
            ))}
          </section>

          <section className="fs2-thesis">
            <h2>研究摘要</h2>
            <p>{summary}<button className="fs2-citation" onClick={() => locate(42)}>p.42 ↗</button></p>
          </section>

          <section className="fs2-kpis" aria-label="核心指标">
            {displayKpis.map((kpi, index) => (
              <button className="fs2-kpi" onClick={() => locate(kpi.page)} key={kpi.label}>
                <span className="fs2-k-label">{kpi.label} <em>0{index + 1}</em></span>
                <span className="fs2-k-value">{kpi.value}<small>{kpi.unit}</small></span>
                <span className={`fs2-k-delta ${kpi.down ? "down" : ""}`}>{kpi.delta}</span>
              </button>
            ))}
          </section>

          <section className="fs2-panel">
            <div className="fs2-panel-head">
              <div><h3>指标视图</h3><p>只展示同一单位下的指标，避免金额与百分比混轴。</p></div>
              <div className="fs2-unit-toggle" role="group" aria-label="指标单位切换">
                <button className={unit === "amount" ? "active" : ""} onClick={() => setUnit("amount")}>金额</button>
                <button className={unit === "ratio" ? "active" : ""} onClick={() => setUnit("ratio")}>比率</button>
              </div>
            </div>
            <div className="fs2-chart" style={{ display: unit === "amount" ? "grid" : "none" }}>
              <div className="fs2-chart-wrap">
                <div className="fs2-chart-label"><span>营业收入 / 亿元</span><span>FY2021—FY2025</span></div>
                <svg viewBox="0 0 360 160" role="img" aria-label="营业收入柱状图">
                  <line className="fs2-axis" x1="30" y1="132" x2="350" y2="132" />
                  <line className="fs2-grid" x1="30" y1="96" x2="350" y2="96" /><line className="fs2-grid" x1="30" y1="60" x2="350" y2="60" /><line className="fs2-grid" x1="30" y1="24" x2="350" y2="24" />
                  <text className="fs2-tick" x="4" y="135">0</text><text className="fs2-tick" x="4" y="99">500</text><text className="fs2-tick" x="4" y="63">1000</text><text className="fs2-tick" x="4" y="27">1500</text>
                  {[["54", "92", "40", "21"], ["116", "81", "51", "22"], ["178", "68", "64", "23"], ["240", "56", "76", "24"], ["302", "40", "92", "25"]].map(([x, y, h, label]) => (
                    <g key={label}><rect className="fs2-bar" x={x} y={y} width="34" height={h} rx="1" /><text className="fs2-tick" x={Number(x) + 7} y="146">{label}</text></g>
                  ))}
                  <text className="fs2-value" x="292" y="31">1,284</text>
                </svg>
                <div className="fs2-legend"><span><i />营业收入</span><span>单位：亿元</span></div>
              </div>
              <div className="fs2-chart-wrap">
                <div className="fs2-chart-label"><span>经营现金流 / 亿元</span><span>FY2021—FY2025</span></div>
                <svg viewBox="0 0 360 160" role="img" aria-label="经营现金流柱状图">
                  <line className="fs2-axis" x1="30" y1="132" x2="350" y2="132" />
                  <line className="fs2-grid" x1="30" y1="96" x2="350" y2="96" /><line className="fs2-grid" x1="30" y1="60" x2="350" y2="60" /><line className="fs2-grid" x1="30" y1="24" x2="350" y2="24" />
                  <text className="fs2-tick" x="4" y="135">0</text><text className="fs2-tick" x="4" y="99">80</text><text className="fs2-tick" x="4" y="63">160</text><text className="fs2-tick" x="4" y="27">240</text>
                  {[["54", "103", "29", "21"], ["116", "92", "40", "22"], ["178", "83", "49", "23"], ["240", "70", "62", "24"], ["302", "57", "75", "25"]].map(([x, y, h, label]) => (
                    <g key={label}><rect className="fs2-bar" x={x} y={y} width="34" height={h} rx="1" /><text className="fs2-tick" x={Number(x) + 7} y="146">{label}</text></g>
                  ))}
                  <text className="fs2-value" x="292" y="48">212</text>
                </svg>
                <div className="fs2-legend"><span><i />经营现金流</span><span>单位：亿元</span></div>
              </div>
            </div>
            <div className="fs2-chart" style={{ display: unit === "ratio" ? "grid" : "none" }}>
              <div className="fs2-chart-wrap">
                <div className="fs2-chart-label"><span>毛利率 / %</span><span>FY2021—FY2025</span></div>
                <svg viewBox="0 0 360 160" role="img" aria-label="毛利率折线图">
                  <line className="fs2-axis" x1="30" y1="132" x2="350" y2="132" />
                  <line className="fs2-grid" x1="30" y1="96" x2="350" y2="96" /><line className="fs2-grid" x1="30" y1="60" x2="350" y2="60" /><line className="fs2-grid" x1="30" y1="24" x2="350" y2="24" />
                  <text className="fs2-tick" x="4" y="135">30</text><text className="fs2-tick" x="4" y="99">34</text><text className="fs2-tick" x="4" y="63">38</text><text className="fs2-tick" x="4" y="27">42</text>
                  <polyline className="fs2-line" points="70,92 132,84 194,76 256,62 318,48" />
                  {[[70, 92], [132, 84], [194, 76], [256, 62], [318, 48]].map(([cx, cy]) => <circle className="fs2-dot" cx={cx} cy={cy} r="2.8" key={`${cx}-${cy}`} />)}
                  <text className="fs2-value" x="302" y="39">38.6%</text>
                </svg>
                <div className="fs2-legend"><span><i className="line" />毛利率</span><span>单位：%</span></div>
              </div>
              <div className="fs2-chart-wrap">
                <div className="fs2-chart-label"><span>销售费用率 / %</span><span>FY2021—FY2025</span></div>
                <svg viewBox="0 0 360 160" role="img" aria-label="销售费用率折线图">
                  <line className="fs2-axis" x1="30" y1="132" x2="350" y2="132" />
                  <line className="fs2-grid" x1="30" y1="96" x2="350" y2="96" /><line className="fs2-grid" x1="30" y1="60" x2="350" y2="60" /><line className="fs2-grid" x1="30" y1="24" x2="350" y2="24" />
                  <text className="fs2-tick" x="4" y="135">8</text><text className="fs2-tick" x="4" y="99">12</text><text className="fs2-tick" x="4" y="63">16</text><text className="fs2-tick" x="4" y="27">20</text>
                  <polyline className="fs2-line" points="70,94 132,88 194,78 256,64 318,54" />
                  {[[70, 94], [132, 88], [194, 78], [256, 64], [318, 54]].map(([cx, cy]) => <circle className="fs2-dot" cx={cx} cy={cy} r="2.8" key={`${cx}-${cy}`} />)}
                  <text className="fs2-value" x="300" y="45">17.4%</text>
                </svg>
                <div className="fs2-legend"><span><i className="line" />销售费用率</span><span>单位：%</span></div>
              </div>
            </div>
          </section>

          <section className="fs2-panel">
            <div className="fs2-panel-head"><div><h3>风险与跟踪</h3><p>每条风险保留原文页码，点击右侧可直接定位。</p></div><span className="fs2-mono">03 SIGNALS</span></div>
            <div className="fs2-risk-list">
              {displayRisks.map((risk) => (
                <div className={`fs2-risk ${risk.level}`} key={risk.title} onClick={() => locate(risk.page)}>
                  <span className="fs2-sev" />
                  <div><div className="fs2-r-title">{risk.title}</div><div className="fs2-r-desc">{risk.desc}</div></div>
                  <span className="fs2-r-page">p.{risk.page} ↗</span>
                </div>
              ))}
            </div>
          </section>

          <section className="fs2-panel">
            <div className="fs2-panel-head"><div><h3>报告目录</h3><p>Markdown 报告与原文页码双向绑定。</p></div><span className="fs2-mono">05 SECTIONS</span></div>
            <div className="fs2-report-lines">
              {REPORT_LINES.map((line) => (
                <div className="fs2-report-line" key={line.n}><span className="fs2-n">{line.n}</span><span>{line.text}</span><button className="fs2-citation" onClick={() => locate(line.page)}>p.{line.page}</button></div>
              ))}
            </div>
          </section>
        </div>
      </main>

      <aside className="fs2-evidence">
        <div className="fs2-evidence-head">
          <div className="fs2-eh-row"><h2>原文与证据</h2><span className="fs2-pageno">PAGE {page}{totalPages ? ` / ${totalPages}` : ""}</span></div>
          <div className="fs2-tabs"><button className={`fs2-tab ${tab === "pdf" ? "active" : ""}`} onClick={() => setTab("pdf")}>原文</button><button className={`fs2-tab ${tab === "chain" ? "active" : ""}`} onClick={() => setTab("chain")}>证据链</button></div>
        </div>
        {tab === "pdf" ? <div>
          <div className="fs2-pdf-wrap">
            <div className="fs2-pdf-toolbar"><span>FY2025 ANNUAL REPORT</span><span className="fs2-zoom">− 100% +</span></div>
            {pdfUrl && <iframe className="fs2-pdf-frame" key={`${pdfUrl}-${page}`} src={`${pdfUrl}#page=${page}`} title={`年报第 ${page} 页`} />}
            <div className="fs2-pdf-page" style={{ display: pdfUrl ? "none" : "block" }}><div className="fs2-watermark">DEMO</div><div className="fs2-doc-kicker">CONSOLIDATED STATEMENT OF CASH FLOWS</div><h3>{current.title}</h3><div className="fs2-doc-sub">{current.sub}</div><p>截至 2025 年 12 月 31 日止年度，集团经营活动产生的现金流量净额为：</p><p className="fs2-highlight">{current.body}</p><p>投资活动产生的现金流量净额为人民币 (8,420) 百万元，主要用于仓储自动化、数字化系统及区域配送中心建设。</p><p>筹资活动产生的现金流量净额为人民币 (3,180) 百万元，主要包括股息支付及租赁负债偿还。</p><p>于 2025 年 12 月 31 日，集团现金及现金等价物余额为人民币 18,640 百万元。</p><div className="fs2-page-foot"><span>NOVA RETAIL GROUP</span><span>{page}</span></div></div>
          </div>
          <div className="fs2-evidence-list"><div className="fs2-eh"><span>本页被引用</span><span>03 CLAIMS</span></div>{EVIDENCE.map((item) => <div className={`fs2-ev-item ${item.tone}`} key={item.title} onClick={() => locate(item.page)}><span className="fs2-ev-dot" /><div className="fs2-ev-copy"><strong>{item.title}</strong><span>{item.desc}</span><em>{item.meta}</em></div></div>)}</div>
        </div> : <div className="fs2-evidence-list" style={{ paddingTop: 18 }}>
          <div className="fs2-eh"><span>证据链</span><span>按结论聚合</span></div>
          <div className="fs2-ev-item" onClick={() => locate(42)}><span className="fs2-ev-dot" /><div className="fs2-ev-copy"><strong>现金流质量改善</strong><span>经营现金流 212 亿元，同比增长 12.6%；现金转换率 1.12。</span><em>支撑结论 01 / 03</em></div></div>
          <div className="fs2-ev-item bad" onClick={() => locate(63)}><span className="fs2-ev-dot" /><div className="fs2-ev-copy"><strong>库存周转放缓</strong><span>存货周转天数上升 9 天，区域库存分化。</span><em>支撑结论 04</em></div></div>
          <div className="fs2-ev-item warn" onClick={() => locate(57)}><span className="fs2-ev-dot" /><div className="fs2-ev-copy"><strong>销售费用侵蚀利润</strong><span>销售费用率 +1.8pp，部分抵消毛利率改善。</span><em>支撑结论 02</em></div></div>
          <div className="fs2-ev-item" onClick={() => locate(88)}><span className="fs2-ev-dot" /><div className="fs2-ev-copy"><strong>汇率敞口扩大</strong><span>海外收入占比 14%，汇兑损失 6.2 亿元。</span><em>支撑结论 04</em></div></div>
        </div>}
      </aside>

      <div className={`fs2-toast ${toast ? "show" : ""}`}>{toast}</div>
      <div className={`fs2-modal ${modal ? "show" : ""}`} onClick={() => setModal(false)}>
        <div className="fs2-modal-card" onClick={(event) => event.stopPropagation()}>
          <div className="fs2-modal-head"><h3>新建分析</h3><button className="fs2-modal-close" onClick={() => setModal(false)} aria-label="关闭"><X size={16} /></button></div>
          <p>上传一份上市公司年报 PDF，四个 Agent 会依次完成解析、财务分析、风险研判和报告汇总。</p>
          <div className="fs2-drop">拖拽 PDF 到这里，或点击选择文件<br /><span className="fs2-mono">PDF · MAX 50 MB</span></div>
          <div className="fs2-modal-actions"><button className="fs2-btn" onClick={() => setModal(false)}>取消</button><button className="fs2-btn primary" onClick={() => { setModal(false); setToast("原型：上传入口将在正式版接入"); }}>开始分析</button></div>
        </div>
      </div>
    </div>
  );
}
