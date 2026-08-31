"use client";

import {
  ArrowClockwise,
  ArrowUpRight,
  BookOpenText,
  CaretRight,
  ChartLineUp,
  CheckCircle,
  CircleNotch,
  Clock,
  CloudArrowUp,
  Command,
  DownloadSimple,
  FileText,
  LinkSimple,
  ListChecks,
  Pulse,
  SealCheck,
  SidebarSimple,
  Sparkle,
  WarningCircle,
  X,
} from "@phosphor-icons/react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import ReactMarkdown from "react-markdown";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
const DEMO_MODE = process.env.NEXT_PUBLIC_DEMO_MODE !== "false";

type Status = "pending" | "parsing" | "finance_analyzing" | "risk_analyzing" | "summary_finish" | "error";
type AgentKey = "document_parser" | "finance_analyst" | "risk_reviewer" | "industry_summary";
type ConnectionState = "idle" | "live" | "reconnecting" | "disconnected";
type TaskSummary = { id: string; filename: string; status: Status; created_at?: string; error_message?: string };
type Source = { page?: number; chunk_id?: string };
type ReportData = {
  company?: string;
  period?: string;
  metrics?: Record<string, unknown>;
  risks?: Array<{ level?: string; title?: string; evidence?: string; suggestion?: string; sources?: Source[] }>;
  summary?: string;
  industry_comparison?: unknown;
  sources?: Source[];
};
type TaskResult = { task_id: string; status: Status; report_markdown?: string; report_json?: ReportData; error?: string };
type AgentState = { status: "idle" | "running" | "done" | "error"; label: string };

const AGENTS: Array<{ key: AgentKey; title: string; shortTitle: string; subtitle: string; activeWhen: Status }> = [
  { key: "document_parser", title: "文档解析 Agent", shortTitle: "文档解析", subtitle: "文本与财务表格清洗", activeWhen: "parsing" },
  { key: "finance_analyst", title: "财务分析师 Agent", shortTitle: "财务分析师", subtitle: "指标、同比与现金流", activeWhen: "finance_analyzing" },
  { key: "risk_reviewer", title: "风险研判 Agent", shortTitle: "风险研判", subtitle: "异常与潜在隐患识别", activeWhen: "risk_analyzing" },
  { key: "industry_summary", title: "行业对比 & 汇总", shortTitle: "报告汇总", subtitle: "生成标准化研究报告", activeWhen: "summary_finish" },
];

const EMPTY_AGENTS: Record<AgentKey, AgentState> = {
  document_parser: { status: "idle", label: "等待任务" },
  finance_analyst: { status: "idle", label: "等待解析" },
  risk_reviewer: { status: "idle", label: "等待财务分析" },
  industry_summary: { status: "idle", label: "等待风险研判" },
};

const DEMO_TASK: TaskSummary = {
  id: "demo-task-2025",
  filename: "示例年报_2025_研究演示.pdf",
  status: "summary_finish",
  created_at: "2026-08-30T09:24:00.000Z",
};

const DEMO_REPORT: ReportData = {
  company: "澜海智能制造股份有限公司",
  period: "2025 年度",
  metrics: {
    营业收入: { value: 128.6, unit: "亿元", yoy: "+18.4%" },
    归母净利润: { value: 16.8, unit: "亿元", yoy: "+22.1%" },
    毛利率: { value: 31.7, unit: "%", yoy: "+1.8pct" },
    经营现金流: { value: 21.4, unit: "亿元", yoy: "+35.2%" },
    研发费用率: { value: 8.6, unit: "%", yoy: "+0.6pct" },
    ROE: { value: 14.2, unit: "%", yoy: "+1.1pct" },
  },
  risks: [
    { level: "中", title: "应收账款增速高于收入增速", evidence: "应收账款同比增长 28.3%，高于收入增速。", suggestion: "持续跟踪账期与重点客户回款。", sources: [{ page: 68, chunk_id: "p68-c2" }] },
    { level: "低", title: "研发投入持续上升", evidence: "研发费用率升至 8.6%，短期压制利润弹性。", suggestion: "关注研发项目商业化兑现节奏。", sources: [{ page: 42, chunk_id: "p42-c1" }] },
  ],
  summary: "公司收入与利润保持双位数增长，经营现金流改善明显。增长质量整体稳健，但应收账款周转仍是后续跟踪重点。",
  industry_comparison: { position: "行业中上游", note: "毛利率和研发费用率高于行业中位数" },
  sources: [{ page: 12, chunk_id: "p12-c1" }, { page: 42, chunk_id: "p42-c1" }, { page: 68, chunk_id: "p68-c2" }],
};

const DEMO_RESULT: TaskResult = {
  task_id: DEMO_TASK.id,
  status: "summary_finish",
  report_json: DEMO_REPORT,
  report_markdown: `# 澜海智能制造股份有限公司 2025 年度投研分析报告\n\n## 核心结论\n\n${DEMO_REPORT.summary}\n\n## 财务表现\n\n- 营业收入 128.6 亿元，同比增长 18.4%。\n- 归母净利润 16.8 亿元，同比增长 22.1%。\n- 毛利率 31.7%，同比提升 1.8pct。\n\n## 风险与跟踪\n\n- **中**：应收账款增速高于收入增速，来源：第 68 页 / p68-c2。\n- **低**：研发费用率升至 8.6%，来源：第 42 页 / p42-c1。\n\n## 行业位置\n\n公司处于行业中上游，毛利率和研发费用率高于行业中位数。\n\n## 来源片段\n\n- 第 12 页 / p12-c1\n- 第 42 页 / p42-c1\n- 第 68 页 / p68-c2`,
};

const DEMO_AGENTS: Record<AgentKey, AgentState> = {
  document_parser: { status: "done", label: "已完成" },
  finance_analyst: { status: "done", label: "已完成" },
  risk_reviewer: { status: "done", label: "已完成" },
  industry_summary: { status: "done", label: "已完成" },
};

type OfficialReport = {
  company: string;
  ticker: string;
  period: string;
  format: string;
  description: string;
  sourceLabel: string;
  sourceUrl: string;
  reportUrl: string;
  localPath: string;
  accent: string;
};

// Official links are reference-only. The app still analyzes PDFs selected by the user.
const OFFICIAL_REPORTS: OfficialReport[] = [
  {
    company: "阿里巴巴集团",
    ticker: "BABA / 9988.HK",
    period: "FY 2025",
    format: "英文 PDF",
    description: "截至 2025 年 3 月 31 日的财年年报。",
    sourceLabel: "Alibaba Group Investor Relations",
    sourceUrl: "https://www.alibabagroup.com/en-US/ir-financial-reports-financial-results",
    reportUrl: "https://data.alibabagroup.com/ecms-files/1514443390/dbeaef58-eac8-4c00-9fdf-e361a5841078/Alibaba%20Group%20Holding%20Limited%20Fiscal%20Year%202025%20Annual%20Report.pdf",
    localPath: "/examples/alibaba-fy2025-annual-report.pdf",
    accent: "orange",
  },
  {
    company: "腾讯控股",
    ticker: "0700.HK",
    period: "2025 Annual Report",
    format: "英文 PDF",
    description: "腾讯投资者关系站发布的 2025 年度报告。",
    sourceLabel: "Tencent Investor Relations",
    sourceUrl: "https://www.tencent.com/investors/financial-reports/",
    reportUrl: "https://static.www.tencent.com/uploads/2025/08/26/d1d2fb988f5e910e254b3abb41dadb0f.pdf",
    localPath: "/examples/tencent-2025-annual-report.pdf",
    accent: "blue",
  },
  {
    company: "百度集团",
    ticker: "BIDU",
    period: "FY 2025",
    format: "官方 PDF",
    description: "百度 2025 财年的年度报告披露文件。",
    sourceLabel: "SEC EDGAR official filing",
    sourceUrl: "https://ir.baidu.com/financial-information/sec-filings",
    reportUrl: "https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0317/2026031701611_c.pdf",
    localPath: "/examples/baidu-2025-annual-report.pdf",
    accent: "violet",
  },
];

function formatDate(value?: string) {
  if (!value) return "刚刚";
  return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

function metricNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "object" && value !== null) {
    const objectValue = value as Record<string, unknown>;
    for (const key of ["value", "current", "amount", "number"]) {
      const candidate = objectValue[key];
      if (typeof candidate === "number" && Number.isFinite(candidate)) return candidate;
      if (typeof candidate === "string" && candidate.trim() && Number.isFinite(Number(candidate.replaceAll(",", "")))) return Number(candidate.replaceAll(",", ""));
    }
  }
  if (typeof value === "string" && value.trim() && Number.isFinite(Number(value.replaceAll(",", "")))) return Number(value.replaceAll(",", ""));
  return null;
}

function metricLabel(value: unknown) {
  const numeric = metricNumber(value);
  if (typeof value === "object" && value !== null) {
    const item = value as Record<string, unknown>;
    const unit = typeof item.unit === "string" ? ` ${item.unit}` : "";
    return `${numeric === null ? "暂无" : numeric.toLocaleString("zh-CN")}${unit}`;
  }
  return numeric === null ? "暂无" : numeric.toLocaleString("zh-CN");
}

function StatusChip({ status }: { status: Status }) {
  const config: Record<Status, { label: string; className: string }> = {
    pending: { label: "排队中", className: "status-neutral" },
    parsing: { label: "文档解析", className: "status-mint" },
    finance_analyzing: { label: "财务分析", className: "status-mint" },
    risk_analyzing: { label: "风险研判", className: "status-amber" },
    summary_finish: { label: "已完成", className: "status-mint" },
    error: { label: "异常", className: "status-red" },
  };
  return <span className={`status-chip ${config[status].className}`}><span className="status-dot" />{config[status].label}</span>;
}

function ConnectionBadge({ state }: { state: ConnectionState }) {
  const config: Record<ConnectionState, { label: string; className: string }> = {
    idle: { label: "IDLE", className: "connection-idle" },
    live: { label: "LIVE", className: "connection-live" },
    reconnecting: { label: "RECONNECTING", className: "connection-reconnecting" },
    disconnected: { label: "DISCONNECTED", className: "connection-disconnected" },
  };
  return <span className={`connection-badge ${config[state].className}`}><span className="connection-dot" />{config[state].label}</span>;
}

function ResearchSidebar({ tasks, selectedId, open, onSelect, onNew, onClose }: { tasks: TaskSummary[]; selectedId?: string; open: boolean; onSelect: (task: TaskSummary) => void; onNew: () => void; onClose: () => void }) {
  return <>
    {open && <button aria-label="关闭历史任务栏" className="sidebar-overlay no-print" onClick={onClose} />}
    <aside className={`research-sidebar no-print ${open ? "is-open" : ""}`}>
      <div className="sidebar-head"><div className="brand-lockup"><div className="brand-mark"><ChartLineUp size={18} weight="bold" /></div><div><div className="brand-name">FinScope</div><div className="brand-caption">AI research studio</div></div></div><button aria-label="关闭侧边栏" className="sidebar-close" onClick={onClose}><X size={17} /></button></div>
      <button className="new-analysis-button" onClick={onNew}><Sparkle size={17} weight="fill" /> 新建分析 <span className="shortcut"><Command size={12} /> K</span></button>
      <div className="sidebar-section-label">工作台</div>
      <div className="sidebar-nav"><div className="sidebar-nav-item is-active"><ListChecks size={17} />当前分析<span className="nav-count">{selectedId ? "1" : "0"}</span></div><div className="sidebar-nav-item"><BookOpenText size={17} />研究档案<span className="nav-count">{tasks.length}</span></div></div>
      <div className="sidebar-section-label history-label">历史任务</div>
      <div className="task-list">{tasks.length === 0 ? <div className="sidebar-empty"><FileText size={18} /><span>上传第一份年报，历史分析会显示在这里。</span></div> : tasks.map((task) => <button key={task.id} onClick={() => { onSelect(task); onClose(); }} className={`task-item ${task.id === selectedId ? "is-selected" : ""}`}><div className="task-item-top"><FileText size={16} /><span>{task.filename}</span><CaretRight size={14} className="task-arrow" /></div><div className="task-item-meta"><span>{formatDate(task.created_at)}</span><StatusChip status={task.status} /></div></button>)}</div>
      <div className="sidebar-footer"><div className="secure-line"><SealCheck size={15} weight="fill" />数据仅在已配置后端处理</div><div className="sidebar-version">FinScope Console · 01</div></div>
    </aside>
  </>;
}

function UploadPanel({ file, busy, onFile, onUpload, onClear }: { file: File | null; busy: boolean; onFile: (file: File) => void; onUpload: () => void; onClear: () => void }) {
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const accept = (candidate?: File) => { if (candidate && (candidate.type === "application/pdf" || candidate.name.toLowerCase().endsWith(".pdf"))) onFile(candidate); };
  return <section className={`upload-panel ${dragging ? "is-dragging" : ""}`} onDragOver={(event) => { event.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={(event) => { event.preventDefault(); setDragging(false); accept(event.dataTransfer.files[0]); }}>
    <div className="upload-panel-top"><div><div className="eyebrow"><CloudArrowUp size={14} weight="bold" /> DOCUMENT INTAKE</div><h2>把一份年报，变成一份研究判断。</h2><p>上传上市公司 PDF 年报，四个专职 Agent 将提取指标、识别风险并生成可溯源投研报告。</p></div><div className="upload-stat"><span className="stat-number">01</span><span className="stat-label">single PDF<br />per analysis</span></div></div>
    <div className="upload-dropzone" role="button" tabIndex={0} onKeyDown={(event) => { if ((event.key === "Enter" || event.key === " ") && !file) { event.preventDefault(); inputRef.current?.click(); } }} onClick={() => !file && inputRef.current?.click()}><input ref={inputRef} type="file" accept="application/pdf,.pdf" className="hidden-input" onChange={(event) => accept(event.target.files?.[0])} />{file ? <div className="selected-file"><div className="file-symbol"><FileText size={23} weight="bold" /></div><div className="selected-file-info"><strong>{file.name}</strong><span>{(file.size / 1024 / 1024).toFixed(2)} MB · PDF document</span></div><button aria-label="移除文件" className="clear-file" onClick={(event) => { event.stopPropagation(); onClear(); }}><X size={18} /></button></div> : <div className="dropzone-empty"><div className="upload-orbit"><CloudArrowUp size={25} weight="light" /></div><div><strong>拖拽年报至此</strong><span>或点击从本地选择 PDF</span></div><div className="dropzone-hint">最大 50 MB · 仅处理用户上传文件</div></div>}</div>
    <div className="upload-panel-bottom"><div className="privacy-note"><SealCheck size={15} weight="fill" /> 上传内容通过已配置的 FastAPI 接口处理</div>{file && <button disabled={busy} className="gradient-button" onClick={onUpload}>{busy ? <CircleNotch size={17} className="spin" /> : <ArrowUpRight size={17} />} {busy ? "创建任务中" : "开始分析"}</button>}</div>
  </section>;
}

function OfficialReportsPanel({ onLoadExample, loadingPath }: { onLoadExample: (report: OfficialReport) => void; loadingPath: string }) {
  return <section className="official-reports-panel no-print" aria-labelledby="official-reports-title">
    <div className="section-heading official-reports-heading">
      <div>
        <div className="eyebrow muted-eyebrow"><BookOpenText size={14} weight="bold" /> OFFICIAL REFERENCES</div>
        <h2 id="official-reports-title">2025 年报示例</h2>
        <p>从官方来源下载后拖入上方上传区，平台只处理你主动选择的本地 PDF。</p>
      </div>
      <span className="official-reports-note">3 SOURCES</span>
    </div>
    <div className="official-reports-grid">
      {OFFICIAL_REPORTS.map((report) => <article key={report.company} className={`official-report-card accent-${report.accent}`}>
        <div className="official-report-card-top"><div className="official-report-logo">{report.company.slice(0, 1)}</div><span className="official-report-format">{report.format}</span></div>
        <div className="official-report-company">{report.company}</div>
        <div className="official-report-ticker">{report.ticker}</div>
        <div className="official-report-period">{report.period}</div>
        <p>{report.description}</p>
        <div className="official-report-actions">
          <button type="button" disabled={loadingPath === report.localPath} onClick={() => onLoadExample(report)} className="official-report-link">{loadingPath === report.localPath ? <CircleNotch size={15} className="spin" /> : <FileText size={15} />} {loadingPath === report.localPath ? "载入中" : "直接载入示例"} <ArrowUpRight size={14} /></button>
          <a href={report.reportUrl} target="_blank" rel="noreferrer" className="official-source-link">官方原件</a>
          <a href={report.sourceUrl} target="_blank" rel="noreferrer" className="official-source-link">来源页</a>
        </div>
        <div className="official-report-source"><LinkSimple size={12} /> {report.sourceLabel}</div>
      </article>)}
    </div>
  </section>;
}

function AgentPipeline({ currentStatus, agents }: { currentStatus: Status; agents: Record<AgentKey, AgentState> }) {
  return <section className="pipeline-section"><div className="section-heading"><div><div className="eyebrow muted-eyebrow"><Pulse size={14} weight="bold" /> ORCHESTRATION</div><h2>Agent 执行流水线</h2><p>每个角色独立处理，结果实时写入当前任务。</p></div><StatusChip status={currentStatus} /></div><div className="pipeline-grid">{AGENTS.map((agent, index) => { const state = agents[agent.key]; const running = state.status === "running"; const done = state.status === "done"; const errored = state.status === "error"; return <div key={agent.key} className={`agent-card ${running ? "is-running" : ""} ${done ? "is-done" : ""} ${errored ? "is-error" : ""}`}><div className="agent-card-head"><div className={`agent-icon ${running ? "icon-running" : done ? "icon-done" : errored ? "icon-error" : ""}`}>{done ? <CheckCircle size={20} weight="fill" /> : errored ? <WarningCircle size={20} weight="fill" /> : running ? <CircleNotch size={20} className="spin" /> : <Clock size={19} />}</div><span className="agent-index">0{index + 1}</span></div><div className="agent-title">{agent.title}</div><div className="agent-subtitle">{agent.subtitle}</div><div className={`agent-state ${running ? "state-running" : done ? "state-done" : errored ? "state-error" : ""}`}>{state.label}</div>{index < AGENTS.length - 1 && <div className="pipeline-connector"><span /></div>}</div>; })}</div></section>;
}

function OutputStream({ outputs, connection }: { outputs: Record<string, string[]>; connection: ConnectionState }) {
  const entries = AGENTS.flatMap((agent) => (outputs[agent.key] ?? []).map((text) => ({ ...agent, text })));
  return <section className="stream-panel"><div className="stream-head"><div><div className="stream-title"><span className="terminal-mark">›_</span> Agent 输出流</div><div className="stream-subtitle">实时事件 · 当前任务上下文</div></div><ConnectionBadge state={connection} /></div><div className="stream-body" aria-live="polite" aria-busy={connection === "live" || connection === "reconnecting"}>{entries.length === 0 ? <div className="stream-empty"><span className="stream-cursor" />等待 Agent 输出...</div> : <div className="stream-entries">{entries.map((entry, index) => <div key={`${entry.key}-${index}`} className="stream-entry"><div className="stream-entry-head"><span>{entry.shortTitle}</span><span className="stream-entry-index">#{String(index + 1).padStart(2, "0")}</span></div><div className="stream-entry-text">{entry.text}</div></div>)}</div>}</div></section>;
}

function Snapshot({ result, status }: { result?: TaskResult; status: Status }) {
  return <section className="snapshot-panel"><div className="snapshot-head"><div><div className="eyebrow muted-eyebrow"><LinkSimple size={14} weight="bold" /> TRACEABILITY</div><h2>任务快照</h2></div><ArrowUpRight size={19} className="snapshot-arrow" /></div><div className="snapshot-grid"><div><span>任务状态</span><strong>{status === "summary_finish" ? "报告已生成" : status === "error" ? "处理异常" : "Agent 协同中"}</strong></div><div><span>来源片段</span><strong>{result?.report_json?.sources?.length ?? "--"}<small> refs</small></strong></div><div><span>公司</span><strong className="truncate">{result?.report_json?.company ?? "--"}</strong></div><div><span>报告期间</span><strong>{result?.report_json?.period ?? "--"}</strong></div></div><div className="snapshot-foot"><SealCheck size={15} weight="fill" />关键结论保留财报页码来源</div></section>;
}

function MetricCards({ report }: { report?: ReportData }) {
  const entries = Object.entries(report?.metrics ?? {}).slice(0, 6);
  return <section className="metrics-section"><div className="section-heading metrics-heading"><div><div className="eyebrow muted-eyebrow"><ChartLineUp size={14} weight="bold" /> KEY METRICS</div><h2>核心财务指标</h2><p>从结构化 JSON 提取，可直接用于图表渲染。</p></div><span className="metric-count">{String(entries.length).padStart(2, "0")} / 06</span></div><div className="metrics-grid">{entries.length === 0 ? <div className="empty-metric">任务完成后，核心指标会显示在这里。</div> : entries.map(([key, value], index) => { const objectValue = typeof value === "object" && value !== null ? value as Record<string, unknown> : null; const yoy = typeof objectValue?.yoy === "string" ? objectValue.yoy : undefined; return <div key={key} className={`metric-card ${index === 0 ? "metric-primary" : ""}`}><div className="metric-card-top"><span>{key}</span><span className="metric-index">0{index + 1}</span></div><div className="metric-value">{metricLabel(value)}</div>{yoy ? <div className="metric-change"><ArrowUpRight size={14} />同比 {yoy}</div> : <div className="metric-source">年报结构化字段</div>}</div>; })}</div></section>;
}

function ChartsPanel({ report }: { report?: ReportData }) {
  const chartData = useMemo(() => Object.entries(report?.metrics ?? {}).map(([name, value]) => ({ name: name.length > 7 ? `${name.slice(0, 7)}…` : name, value: metricNumber(value) })).filter((item): item is { name: string; value: number } => item.value !== null), [report]);
  return <section className="charts-panel"><div className="section-heading"><div><div className="eyebrow muted-eyebrow"><ChartLineUp size={14} weight="bold" /> VISUAL ANALYSIS</div><h2>指标分布与曲线</h2><p>基于后端返回的 metrics 自动生成，缺失值不会被补造。</p></div><div className="chart-legend"><span><i className="legend-bar" />指标值</span><span><i className="legend-line" />趋势线</span></div></div>{chartData.length < 2 ? <div className="chart-empty"><ChartLineUp size={24} /><span>至少需要两个可识别的数值指标才能绘制图表。</span></div> : <div className="charts-grid"><div className="chart-wrap"><div className="chart-label">指标分布</div><ResponsiveContainer width="100%" height={245}><BarChart data={chartData} margin={{ top: 12, right: 10, left: -18, bottom: 0 }}><defs><linearGradient id="metricBarGradient" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#4da3ff" /><stop offset="100%" stopColor="#6fd7b2" /></linearGradient></defs><CartesianGrid stroke="#e7edf0" vertical={false} /><XAxis dataKey="name" tick={{ fontSize: 10, fill: "#7b8790" }} axisLine={false} tickLine={false} /><YAxis tick={{ fontSize: 10, fill: "#7b8790" }} axisLine={false} tickLine={false} /><Tooltip cursor={{ fill: "#f4f7f8" }} contentStyle={{ border: "1px solid #e0e7eb", borderRadius: 12, fontSize: 12, boxShadow: "0 12px 34px rgba(22, 40, 50, .08)" }} /><Bar dataKey="value" fill="url(#metricBarGradient)" radius={[6, 6, 2, 2]} /></BarChart></ResponsiveContainer></div><div className="chart-wrap"><div className="chart-label">数值曲线</div><ResponsiveContainer width="100%" height={245}><LineChart data={chartData} margin={{ top: 12, right: 10, left: -18, bottom: 0 }}><CartesianGrid stroke="#e7edf0" vertical={false} /><XAxis dataKey="name" tick={{ fontSize: 10, fill: "#7b8790" }} axisLine={false} tickLine={false} /><YAxis tick={{ fontSize: 10, fill: "#7b8790" }} axisLine={false} tickLine={false} /><Tooltip cursor={{ stroke: "#b9d7d2", strokeDasharray: "4 4" }} contentStyle={{ border: "1px solid #e0e7eb", borderRadius: 12, fontSize: 12, boxShadow: "0 12px 34px rgba(22, 40, 50, .08)" }} /><Line type="monotone" dataKey="value" stroke="#15242c" strokeWidth={2.5} dot={{ r: 4, fill: "#54bba8", stroke: "#ffffff", strokeWidth: 2 }} activeDot={{ r: 6, fill: "#4da3ff", stroke: "#ffffff", strokeWidth: 2 }} /></LineChart></ResponsiveContainer></div></div>}</section>;
}

function RiskInsights({ report }: { report?: ReportData }) {
  const risks = report?.risks ?? [];
  return <section className="risk-panel"><div className="section-heading"><div><div className="eyebrow muted-eyebrow"><WarningCircle size={14} weight="bold" /> RISK SIGNALS</div><h2>风险与跟踪事项</h2><p>来自风险研判 Agent 的结构化结论，保留原文页码。</p></div><span className="risk-count">{String(risks.length).padStart(2, "0")} signals</span></div>{risks.length === 0 ? <div className="risk-empty"><WarningCircle size={22} /><span>任务完成后，风险研判结果会显示在这里。</span></div> : <div className="risk-list">{risks.map((risk, index) => <div className="risk-row" key={`${risk.title}-${index}`}><div className={`risk-level risk-${(risk.level ?? "中").toLowerCase()}`}>{risk.level ?? "中"}</div><div className="risk-copy"><strong>{risk.title ?? "待确认事项"}</strong><p>{risk.evidence ?? "暂无证据摘要"}</p>{risk.suggestion && <span className="risk-suggestion">跟踪：{risk.suggestion}</span>}</div><div className="risk-source">{risk.sources?.[0]?.page ? <><span>来源页码</span><strong>第 {risk.sources[0].page} 页</strong><small>{risk.sources[0].chunk_id}</small></> : <span>未提供来源</span>}</div></div>)}</div>}</section>;
}

function ReportPreview({ result }: { result?: TaskResult }) {
  return <section className="report-panel print-report"><div className="report-head no-print"><div><div className="eyebrow muted-eyebrow"><BookOpenText size={14} weight="bold" /> DELIVERABLE</div><h2>投研报告预览</h2><p>Markdown · 含关键结论来源页码</p></div><button className="outline-button" onClick={() => window.print()}><DownloadSimple size={17} /> 导出 PDF</button></div><article className="report-content">{result?.report_markdown ? <ReactMarkdown components={{ h1: ({ children }) => <h1>{children}</h1>, h2: ({ children }) => <h2>{children}</h2>, h3: ({ children }) => <h3>{children}</h3>, p: ({ children }) => <p>{children}</p>, ul: ({ children }) => <ul>{children}</ul>, li: ({ children }) => <li>{children}</li>, blockquote: ({ children }) => <blockquote>{children}</blockquote>, code: ({ children }) => <code>{children}</code> }}>{result.report_markdown}</ReactMarkdown> : <div className="report-empty"><BookOpenText size={25} /><span>完成分析后，Markdown 报告会显示在这里。</span></div>}</article></section>;
}

export default function HomePage() {
  const [tasks, setTasks] = useState<TaskSummary[]>(DEMO_MODE ? [DEMO_TASK] : []);
  const [selected, setSelected] = useState<TaskSummary | null>(DEMO_MODE ? DEMO_TASK : null);
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<TaskResult | undefined>(DEMO_MODE ? DEMO_RESULT : undefined);
  const [status, setStatus] = useState<Status>(DEMO_MODE ? "summary_finish" : "pending");
  const [agents, setAgents] = useState<Record<AgentKey, AgentState>>(DEMO_MODE ? DEMO_AGENTS : EMPTY_AGENTS);
  const [outputs, setOutputs] = useState<Record<string, string[]>>({});
  const [connection, setConnection] = useState<ConnectionState>("idle");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [exampleLoadingPath, setExampleLoadingPath] = useState("");
  const eventSource = useRef<EventSource | null>(null);
  const activeTaskRef = useRef<string | null>(null);
  const subscribeRef = useRef<(taskId: string, retry?: number) => void>(() => undefined);

  const closeStream = useCallback(() => { eventSource.current?.close(); eventSource.current = null; setConnection("idle"); }, []);
  const resetPipeline = useCallback(() => { setAgents(EMPTY_AGENTS); setOutputs({}); setError(""); setConnection("idle"); }, []);

  const applyStatus = useCallback((next: Status) => {
    setStatus(next);
    setAgents(() => {
      const order: Status[] = ["parsing", "finance_analyzing", "risk_analyzing", "summary_finish"];
      const activeIndex = order.indexOf(next);
      const nextAgents = { ...EMPTY_AGENTS };
      AGENTS.forEach((agent) => { const agentIndex = order.indexOf(agent.activeWhen); const errored = next === "error" && agentIndex === Math.max(activeIndex, 0); nextAgents[agent.key] = { status: errored ? "error" : next === "summary_finish" || agentIndex < activeIndex ? "done" : agentIndex === activeIndex ? "running" : "idle", label: errored ? "处理异常" : next === "summary_finish" || agentIndex < activeIndex ? "已完成" : agentIndex === activeIndex ? "运行中" : agentIndex > activeIndex ? "等待前置 Agent" : "等待任务" }; });
      return nextAgents;
    });
  }, []);

  const loadTasks = useCallback(async () => {
    try {
      const response = await fetch(`${API_BASE}/tasks`);
      if (!response.ok) throw new Error("历史任务加载失败");
      const remoteTasks = (await response.json()) as TaskSummary[];
      if (remoteTasks.length > 0 || !DEMO_MODE) setTasks(remoteTasks);
    } catch (cause) {
      if (!DEMO_MODE) setError(cause instanceof Error ? cause.message : "历史任务加载失败");
    }
  }, []);

  const loadResult = useCallback(async (taskId: string) => {
    const response = await fetch(`${API_BASE}/tasks/${taskId}`); if (!response.ok) throw new Error("任务结果加载失败"); const data = (await response.json()) as TaskResult; setResult(data); applyStatus(data.status); if (data.error) setError(data.error); return data;
  }, [applyStatus]);

  const subscribe = useCallback((taskId: string, retry = 0) => {
    closeStream(); activeTaskRef.current = taskId; setConnection(retry > 0 ? "reconnecting" : "live");
    const stream = new EventSource(`${API_BASE}/stream/${taskId}`); eventSource.current = stream;
    const parse = (event: MessageEvent) => { try { return JSON.parse(event.data) as Record<string, unknown>; } catch { return {}; } };
    stream.addEventListener("status", (event) => { const payload = parse(event as MessageEvent); if (typeof payload.status === "string") applyStatus(payload.status as Status); if (payload.error) setError(String(payload.error)); if (payload.status === "summary_finish" || payload.status === "error") { void loadResult(taskId); setConnection("idle"); stream.close(); } });
    stream.addEventListener("agent", (event) => { const payload = parse(event as MessageEvent); const key = payload.agent as string; const text = typeof payload.output === "string" ? payload.output : JSON.stringify(payload.output, null, 2); if (!key) return; setOutputs((current) => ({ ...current, [key]: [...(current[key] ?? []), text] })); });
    stream.addEventListener("warning", (event) => { const payload = parse(event as MessageEvent); if (payload.message) setError(String(payload.message)); });
    stream.onerror = () => { stream.close(); if (activeTaskRef.current !== taskId) return; if (retry < 1) { setConnection("reconnecting"); setTimeout(() => subscribeRef.current(taskId, retry + 1), 1200); } else { setConnection("disconnected"); setError("实时连接已断开，请点击右上角刷新任务状态重试"); } };
  }, [applyStatus, closeStream, loadResult]);

  useEffect(() => { subscribeRef.current = subscribe; }, [subscribe]);
  useEffect(() => { void loadTasks(); return closeStream; }, [closeStream, loadTasks]);

  const chooseTask = async (task: TaskSummary) => {
    closeStream(); setSelected(task); setFile(null); resetPipeline();
    if (task.id === DEMO_TASK.id) { setResult(DEMO_RESULT); setStatus("summary_finish"); setAgents(DEMO_AGENTS); return; }
    try { const loaded = await loadResult(task.id); if (loaded.status !== "summary_finish" && loaded.status !== "error") subscribe(task.id); } catch (cause) { setError(cause instanceof Error ? cause.message : "任务加载失败"); }
  };
  const newAnalysis = () => { closeStream(); setSelected(null); setResult(undefined); setFile(null); setStatus("pending"); resetPipeline(); };
  const loadExample = async (report: OfficialReport) => {
    setExampleLoadingPath(report.localPath);
    setError("");
    try {
      const response = await fetch(report.localPath);
      if (!response.ok) throw new Error("示例文件加载失败，请重新启动前端服务");
      const blob = await response.blob();
      setFile(new File([blob], `${report.company}_${report.period.replaceAll(" ", "_")}.pdf`, { type: "application/pdf" }));
      closeStream();
      setSelected(null);
      setResult(undefined);
      setStatus("pending");
      resetPipeline();
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "示例文件加载失败");
    } finally {
      setExampleLoadingPath("");
    }
  };
  const refresh = async () => { if (!selected) return; if (selected.id === DEMO_TASK.id) { setResult(DEMO_RESULT); setStatus("summary_finish"); setAgents(DEMO_AGENTS); return; } try { await loadResult(selected.id); setError(""); } catch (cause) { setError(cause instanceof Error ? cause.message : "任务刷新失败"); } };
  const upload = async () => { if (!file) return; setBusy(true); setError(""); const body = new FormData(); body.append("file", file); try { const response = await fetch(`${API_BASE}/tasks`, { method: "POST", body }); if (!response.ok) throw new Error((await response.text()) || "上传失败"); const created = (await response.json()) as { task_id: string; status: Status }; const task: TaskSummary = { id: created.task_id, filename: file.name, status: created.status, created_at: new Date().toISOString() }; setTasks((current) => [task, ...current]); setSelected(task); setResult(undefined); resetPipeline(); setStatus("pending"); subscribe(created.task_id); } catch (cause) { setError(cause instanceof Error ? cause.message : "上传失败"); } finally { setBusy(false); } };

  const headline = selected ? selected.filename : "从年报开始，建立你的研究判断";
  return <main className="app-shell"><ResearchSidebar tasks={tasks} selectedId={selected?.id} open={sidebarOpen} onSelect={chooseTask} onNew={newAnalysis} onClose={() => setSidebarOpen(false)} /><div className="main-column"><header className="topbar no-print"><div className="topbar-inner"><button aria-label="打开历史任务" className="mobile-menu-button" onClick={() => setSidebarOpen(true)}><SidebarSimple size={20} /></button><div className="topbar-context"><div className="topbar-kicker"><span className="kicker-line" /> AI 财报多 Agent 投研分析平台 {DEMO_MODE && <span className="demo-badge">DEMO DATA</span>}</div><h1>{headline}</h1></div><div className="topbar-actions">{selected && <><StatusChip status={status} /><button aria-label="刷新任务" className="icon-button" onClick={() => void refresh()}><ArrowClockwise size={18} /></button></>}</div></div></header><div className="page-content">{error && <div className="error-banner no-print"><WarningCircle size={18} weight="fill" /><span>{error}</span><button aria-label="关闭错误提示" onClick={() => setError("")}><X size={15} /></button></div>}{!selected && <UploadPanel file={file} busy={busy} onFile={setFile} onUpload={upload} onClear={() => setFile(null)} />}{selected && <AgentPipeline currentStatus={status} agents={agents} />}{(!selected || selected.id === DEMO_TASK.id) && <OfficialReportsPanel onLoadExample={loadExample} loadingPath={exampleLoadingPath} />}<div className="stream-grid"><OutputStream outputs={outputs} connection={connection} /><Snapshot result={result} status={status} /></div><MetricCards report={result?.report_json} /><ChartsPanel report={result?.report_json} /><RiskInsights report={result?.report_json} /><ReportPreview result={result} /></div></div></main>;
}
