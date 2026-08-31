"""AI financial-report multi-agent backend.

The module is intentionally self-contained for the first reviewable slice. It
uses Supabase/Postgres for durable task state and LangGraph for orchestration.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Literal, Optional, TypedDict
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from supabase import Client, create_client

try:  # LangGraph/LangChain are imported lazily in environments that omit them.
    from langchain_core.output_parsers import BaseOutputParser
    from langchain_core.tools import BaseTool
    from langchain_openai import OpenAIEmbeddings
    from langchain_openai import ChatOpenAI
    from langgraph.graph import END, START, StateGraph
except ImportError:  # pragma: no cover - gives a clear startup error below.
    BaseTool = object  # type: ignore[misc,assignment]
    BaseOutputParser = object  # type: ignore[misc,assignment]
    ChatOpenAI = None  # type: ignore[assignment]
    OpenAIEmbeddings = None  # type: ignore[assignment]
    StateGraph = None  # type: ignore[assignment]

load_dotenv()

TaskStatus = Literal[
    "pending",
    "parsing",
    "finance_analyzing",
    "risk_analyzing",
    "summary_finish",
    "error",
]


class AgentState(TypedDict, total=False):
    task_id: str
    file_path: str
    status: TaskStatus
    document_text: str
    tables: List[Dict[str, Any]]
    chunks: List[Dict[str, Any]]
    finance: Dict[str, Any]
    risks: Dict[str, Any]
    report_markdown: str
    report_json: Dict[str, Any]
    error: Optional[str]


class JsonReportParser(BaseOutputParser[Dict[str, Any]]):
    """Parse strict JSON model output, including fenced JSON, with validation."""

    required_fields: List[str] = Field(default_factory=list)

    def parse(self, text: str) -> Dict[str, Any]:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.I | re.S).strip()
        try:
            value = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise ValueError(f"模型输出不是有效JSON: {exc.msg}") from exc
        if not isinstance(value, dict):
            raise ValueError("模型输出必须是JSON对象")
        missing = set(self.required_fields).difference(value)
        if missing:
            raise ValueError(f"JSON缺少字段: {', '.join(sorted(missing))}")
        if "metrics" in value and not isinstance(value["metrics"], dict):
            raise ValueError("metrics必须为对象")
        if "risks" in value and not isinstance(value["risks"], list):
            raise ValueError("risks必须为数组")
        return value

    @property
    def _type(self) -> str:
        return "strict_json_report_parser"


class PDFParseTool(BaseTool):
    """Extract text and simple table-like rows from an uploaded PDF."""

    name: str = "parse_uploaded_pdf"
    description: str = "仅解析用户上传的PDF文件，不执行网络下载。"

    def _run(self, file_path: str) -> Dict[str, Any]:
        try:
            import pdfplumber
            with pdfplumber.open(file_path) as pdf:
                pages, tables, chunks = [], [], []
                for page_number, page in enumerate(pdf.pages, start=1):
                    page_text = page.extract_text() or ""
                    pages.append(page_text)
                    for index in range(0, len(page_text), 1800):
                        chunk_text = page_text[index : index + 1800].strip()
                        if chunk_text:
                            chunks.append({"chunk_id": f"p{page_number}-c{index // 1800 + 1}", "page": page_number, "text": chunk_text})
                    for table in page.extract_tables() or []:
                        tables.append({"page": page_number, "rows": table})
            text = "\n\n".join(pages).strip()
            if not text and not tables:
                raise ValueError("PDF未提取到文本或表格")
            return {"text": text, "tables": tables, "chunks": chunks}
        except Exception as exc:  # wrapped so graph can persist an error state
            raise RuntimeError(f"PDF解析失败: {exc}") from exc

    async def _arun(self, file_path: str, **kwargs: Any) -> Dict[str, Any]:
        return await asyncio.to_thread(self._run, file_path)


class RagRetrieveInput(BaseModel):
    task_id: str = Field(description="分析任务 UUID")
    query: str = Field(min_length=1, description="需要检索的财报主题或问题")
    top_k: int = Field(default=8, ge=1, le=20, description="返回片段数量")


class RagRetrieveTool(BaseTool):
    """Retrieve page-addressable annual-report chunks from Supabase pgvector."""

    name: str = "retrieve_report_chunks"
    description: str = (
        "从当前任务对应的用户上传财报中检索相关原文片段。返回 page、chunk_id、content；"
        "不得访问网络或下载文件。"
    )
    args_schema: type[BaseModel] = RagRetrieveInput
    store: Any = Field(exclude=True)
    embeddings: Any = Field(exclude=True)
    fallback_chunks: Dict[str, List[Dict[str, Any]]] = Field(default_factory=dict, exclude=True)

    def _run(self, task_id: str, query: str, top_k: int = 8) -> List[Dict[str, Any]]:
        vector = self.embeddings.embed_query(query)
        return self.store.search_chunks(task_id, vector, top_k)

    async def _arun(self, task_id: str, query: str, top_k: int = 8, **kwargs: Any) -> List[Dict[str, Any]]:
        try:
            vector = await self.embeddings.aembed_query(query)
            matches = await asyncio.to_thread(self.store.search_chunks, task_id, vector, top_k)
            if matches:
                return matches
        except Exception:
            # A task can still be analyzed from its page chunks if pgvector is temporarily unavailable.
            pass
        return self.fallback_chunks.get(task_id, [])[:top_k]


class TaskCreateResponse(BaseModel):
    task_id: str
    status: TaskStatus


class TaskResult(BaseModel):
    task_id: str
    status: TaskStatus
    report_markdown: Optional[str] = None
    report_json: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class SupabaseStore:
    def __init__(self) -> None:
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_SERVICE_KEY")
        if not url or not key:
            raise RuntimeError("SUPABASE_URL 与 SUPABASE_SERVICE_KEY 必须配置")
        self.client: Client = create_client(url, key)

    def create_task(self, filename: str, storage_path: str) -> str:
        task_id = str(uuid4())
        self.client.table("analysis_tasks").insert(
            {"id": task_id, "filename": filename, "storage_path": storage_path, "status": "pending"}
        ).execute()
        return task_id

    def update_task(self, task_id: str, status: TaskStatus, error: Optional[str] = None) -> None:
        payload: Dict[str, Any] = {"status": status, "updated_at": datetime.now(timezone.utc).isoformat()}
        if error:
            payload["error_message"] = error
        self.client.table("analysis_tasks").update(payload).eq("id", task_id).execute()

    def save_report(self, task_id: str, markdown: str, report_json: Dict[str, Any]) -> None:
        self.client.table("analysis_reports").upsert(
            {"task_id": task_id, "markdown": markdown, "report_json": report_json}
        ).execute()

    def save_chunks(self, task_id: str, chunks: List[Dict[str, Any]], vectors: List[List[float]]) -> None:
        rows = [
            {"task_id": task_id, "chunk_id": chunk["chunk_id"], "page": chunk["page"], "content": chunk["text"], "embedding": vector}
            for chunk, vector in zip(chunks, vectors)
        ]
        if rows:
            self.client.table("document_chunks").upsert(rows, on_conflict="task_id,chunk_id").execute()

    def search_chunks(self, task_id: str, vector: List[float], limit: int = 8) -> List[Dict[str, Any]]:
        response = self.client.rpc(
            "match_document_chunks",
            {"query_embedding": vector, "match_task_id": task_id, "match_count": limit},
        ).execute()
        return response.data or []

    def get_task(self, task_id: str) -> Dict[str, Any]:
        try:
            task = self.client.table("analysis_tasks").select("*").eq("id", task_id).single().execute().data
            report = self.client.table("analysis_reports").select("markdown,report_json").eq("task_id", task_id).maybe_single().execute().data
        except Exception as exc:
            # Supabase's single() raises PGRST116 when no row exists.
            if getattr(exc, "code", None) == "PGRST116" or "0 rows" in str(exc).lower():
                raise KeyError(task_id) from exc
            raise
        if not task:
            raise KeyError(task_id)
        return {**task, "report_markdown": (report or {}).get("markdown"), "report_json": (report or {}).get("report_json")}

    def list_tasks(self, limit: int = 50) -> List[Dict[str, Any]]:
        return self.client.table("analysis_tasks").select("*").order("created_at", desc=True).limit(limit).execute().data or []


class ReportAgents:
    def __init__(self, store: SupabaseStore, emit):
        self.store, self.emit = store, emit
        if ChatOpenAI is None:
            raise RuntimeError("LangChain依赖未安装")
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY 必须配置")
        self.llm = ChatOpenAI(model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"), temperature=0, api_key=api_key)
        self.embeddings = OpenAIEmbeddings(model=os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"), api_key=api_key)
        self.finance_parser = JsonReportParser(required_fields=["metrics", "notes", "sources"])
        self.risk_parser = JsonReportParser(required_fields=["risks", "anomalies", "sources"])
        self.report_parser = JsonReportParser(required_fields=["company", "period", "metrics", "risks", "summary", "industry_comparison", "sources"])
        self.pdf_tool = PDFParseTool()
        self.rag_tool = RagRetrieveTool(store=store, embeddings=self.embeddings)

    async def _invoke_json(self, prompt: str, parser: JsonReportParser, retries: int = 2) -> Dict[str, Any]:
        last: Exception | None = None
        for attempt in range(retries + 1):
            try:
                result = await self.llm.ainvoke(prompt)
                content = result.content if hasattr(result, "content") else str(result)
                return parser.parse(content)
            except Exception as exc:
                last = exc
                if attempt < retries:
                    await asyncio.sleep(2**attempt)
        raise RuntimeError(f"大模型或格式解析失败: {last}") from last

    async def _retrieve(self, state: AgentState, query: str) -> List[Dict[str, Any]]:
        matches = await self.rag_tool.ainvoke({"task_id": state["task_id"], "query": query, "top_k": 8})
        if not matches:
            await self.emit(state["task_id"], "warning", {"message": "未检索到相关财报片段"})
        return matches

    @staticmethod
    def _evidence_text(chunks: List[Dict[str, Any]]) -> str:
        return "\n\n".join(
            f"[source page={c.get('page')} chunk={c.get('chunk_id')}] {c.get('content', c.get('text', ''))}"
            for c in chunks
        )

    async def parsing(self, state: AgentState) -> AgentState:
        self.store.update_task(state["task_id"], "parsing")
        await self.emit(state["task_id"], "status", {"status": "parsing"})
        parsed = await self.pdf_tool.ainvoke(state["file_path"])
        try:
            vectors: List[List[float]] = []
            # Batch embeddings so very long annual reports never become one oversized API request.
            for start in range(0, len(parsed["chunks"]), 64):
                batch = parsed["chunks"][start : start + 64]
                vectors.extend(await self.embeddings.aembed_documents([c["text"] for c in batch]))
            await asyncio.to_thread(self.store.save_chunks, state["task_id"], parsed["chunks"], vectors)
        except Exception as exc:
            await self.emit(state["task_id"], "warning", {"message": f"向量入库失败，将继续页级分析: {exc}"})
        self.rag_tool.fallback_chunks[state["task_id"]] = parsed["chunks"]
        await self.emit(state["task_id"], "agent", {"agent": "document_parser", "output": parsed["text"][:2000]})
        return {**state, "status": "parsing", "document_text": parsed["text"], "tables": parsed["tables"], "chunks": parsed["chunks"]}

    async def finance(self, state: AgentState) -> AgentState:
        self.store.update_task(state["task_id"], "finance_analyzing")
        await self.emit(state["task_id"], "status", {"status": "finance_analyzing"})
        evidence = await self._retrieve(state, "营收 净利润 经营活动现金流 毛利率 同比 环比 财务指标")
        result = await self._invoke_json("""你是财务分析师。只输出JSON对象，字段metrics(dict)、notes(array)、sources(array)。每个关键metric尽量附sources数组，引用必须来自给定片段的page/chunk_id，禁止编造页码。从以下证据提取营收、净利润、现金流、毛利率及同比/环比；缺失用null。\n""" + self._evidence_text(evidence), self.finance_parser)
        result.setdefault("sources", [{"page": c.get("page"), "chunk_id": c.get("chunk_id")} for c in evidence])
        await self.emit(state["task_id"], "agent", {"agent": "finance_analyst", "output": result})
        return {**state, "finance": result}

    async def risk(self, state: AgentState) -> AgentState:
        self.store.update_task(state["task_id"], "risk_analyzing")
        await self.emit(state["task_id"], "status", {"status": "risk_analyzing"})
        evidence = await self._retrieve(state, "资产负债 现金流 异常 波动 关联交易 审计意见 风险")
        result = await self._invoke_json("""你是风险研判师。只输出JSON对象，字段risks(array，每项含level、title、evidence、suggestion、sources)、anomalies(array)、sources(array)。sources只能引用证据片段中的page/chunk_id。\n""" + self._evidence_text(evidence), self.risk_parser)
        result.setdefault("sources", [{"page": c.get("page"), "chunk_id": c.get("chunk_id")} for c in evidence])
        await self.emit(state["task_id"], "agent", {"agent": "risk_reviewer", "output": result})
        return {**state, "risks": result}

    async def summary(self, state: AgentState) -> AgentState:
        prompt = """你是行业对比与汇总Agent。只输出JSON对象，必须包含company、period、metrics、risks、summary、industry_comparison、sources。整合输入并为关键结论保留sources(page/chunk_id)。不得给出买卖或股价预测建议。\n财务分析:\n%s\n风险分析:\n%s""" % (json.dumps(state.get("finance", {}), ensure_ascii=False), json.dumps(state.get("risks", {}), ensure_ascii=False))
        result = await self._invoke_json(prompt, self.report_parser)
        result.setdefault("sources", state.get("finance", {}).get("sources", []) + state.get("risks", {}).get("sources", []))
        markdown = self._to_markdown(result)
        self.store.save_report(state["task_id"], markdown, result)
        self.store.update_task(state["task_id"], "summary_finish")
        await self.emit(state["task_id"], "status", {"status": "summary_finish"})
        await self.emit(state["task_id"], "agent", {"agent": "industry_summary", "output": markdown})
        return {**state, "status": "summary_finish", "report_json": result, "report_markdown": markdown}

    @staticmethod
    def _to_markdown(data: Dict[str, Any]) -> str:
        lines = ["# 投研分析报告", "", f"- 公司：{data.get('company', '未识别')}", f"- 期间：{data.get('period', '未识别')}", "", "## 核心指标", ""]
        metrics = data.get("metrics", {})
        for name, value in metrics.items() if isinstance(metrics, dict) else []:
            lines.append(f"- **{name}**：{json.dumps(value, ensure_ascii=False)}")
        lines.extend(["", "## 风险与异常", ""])
        for risk in data.get("risks", []) if isinstance(data.get("risks"), list) else []:
            lines.append(f"- [{risk.get('level', 'unknown')}] {risk.get('title', risk)}")
            if risk.get("sources"):
                source_labels = ", ".join("第%s页/%s" % (s.get("page"), s.get("chunk_id")) for s in risk["sources"])
                lines.append(f"  - 来源：{source_labels}")
        lines.extend(["", "## 结论", "", str(data.get("summary", "")), "", "## 来源片段", ""])
        for source in data.get("sources", []) if isinstance(data.get("sources"), list) else []:
            lines.append(f"- 第{source.get('page')}页/{source.get('chunk_id')}")
        return "\n".join(lines)


app = FastAPI(title="AI财报多Agent投研分析平台", version="0.1.0")
allowed_origins = [origin.strip() for origin in os.getenv("CORS_ALLOW_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)
store: SupabaseStore | None = None
events: Dict[str, "asyncio.Queue[str]"] = {}


def configuration_issues() -> List[str]:
    """Return actionable configuration gaps without exposing secret values."""
    issues: List[str] = []
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    supabase_url = os.getenv("SUPABASE_URL", "").strip()
    supabase_key = os.getenv("SUPABASE_SERVICE_KEY", "").strip()
    if not openai_key or openai_key in {"replace-me", "your-openai-key"}:
        issues.append("OPENAI_API_KEY")
    if not supabase_url or "your-project.supabase.co" in supabase_url:
        issues.append("SUPABASE_URL")
    if not supabase_key or supabase_key in {"replace-me", "your-service-role-key"}:
        issues.append("SUPABASE_SERVICE_KEY")
    return issues


async def emit(task_id: str, event: str, payload: Dict[str, Any]) -> None:
    queue = events.get(task_id)
    if queue:
        output = payload.get("output")
        if event == "agent" and isinstance(output, str) and len(output) > 600:
            chunks = [output[i : i + 600] for i in range(0, len(output), 600)]
            for index, chunk in enumerate(chunks):
                await queue.put(
                    "event: agent\ndata: "
                    + json.dumps({**payload, "output": chunk, "chunk_index": index, "chunk_total": len(chunks)}, ensure_ascii=False)
                    + "\n\n"
                )
            return
        await queue.put(f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n")


async def run_graph(task_id: str, file_path: str) -> None:
    assert store is not None
    try:
        agents = ReportAgents(store, emit)
        graph = StateGraph(AgentState)
        graph.add_node("parsing", agents.parsing)
        graph.add_node("finance", agents.finance)
        graph.add_node("risk", agents.risk)
        graph.add_node("summary", agents.summary)
        graph.add_edge(START, "parsing")
        graph.add_edge("parsing", "finance")
        graph.add_edge("finance", "risk")
        graph.add_edge("risk", "summary")
        graph.add_edge("summary", END)
        await graph.compile().ainvoke({"task_id": task_id, "file_path": file_path, "status": "pending"})
    except Exception as exc:
        store.update_task(task_id, "error", str(exc))
        await emit(task_id, "status", {"status": "error", "error": str(exc)})
    finally:
        try:
            Path(file_path).unlink(missing_ok=True)
        except OSError:
            pass


@app.on_event("startup")
async def startup() -> None:
    global store
    try:
        store = SupabaseStore()
    except RuntimeError:
        # Keep healthz and the local demo frontend available until secrets are configured.
        store = None


@app.post("/tasks", response_model=TaskCreateResponse)
async def upload_pdf(background: BackgroundTasks, file: UploadFile = File(...)) -> TaskCreateResponse:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "仅支持PDF文件")
    if store is None:
        raise HTTPException(503, "后端未配置 SUPABASE_URL 或 SUPABASE_SERVICE_KEY")
    
    data = await file.read()
    if not data or len(data) > int(os.getenv("MAX_UPLOAD_BYTES", str(50 * 1024 * 1024))):
        raise HTTPException(400, "文件为空或超过大小限制")
    temp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    temp.write(data)
    temp.close()
    task_id = store.create_task(file.filename, temp.name)
    events[task_id] = asyncio.Queue()
    background.add_task(run_graph, task_id, temp.name)
    return TaskCreateResponse(task_id=task_id, status="pending")


@app.get("/stream/{task_id}")
async def stream(task_id: str) -> StreamingResponse:
    if store is None:
        raise HTTPException(503, "服务尚未就绪")
    try:
        current = store.get_task(task_id)
    except KeyError:
        raise HTTPException(404, "任务不存在")
    queue = events.setdefault(task_id, asyncio.Queue())

    async def iterator() -> AsyncIterator[str]:
        yield "event: connected\ndata: {}\n\n"
        if current["status"] in {"summary_finish", "error"}:
            payload = {"status": current["status"]}
            if current.get("error_message"):
                payload["error"] = current["error_message"]
            yield f"event: status\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
            return
        while True:
            message = await queue.get()
            yield message
            if '"status": "summary_finish"' in message or '"status": "error"' in message:
                break

    return StreamingResponse(iterator(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/tasks/{task_id}", response_model=TaskResult)
async def task_result(task_id: str) -> TaskResult:
    if store is None:
        raise HTTPException(503, "后端未配置 Supabase")
    try:
        row = store.get_task(task_id)
    except KeyError:
        raise HTTPException(404, "任务不存在")
    return TaskResult(task_id=task_id, status=row["status"], report_markdown=row.get("report_markdown"), report_json=row.get("report_json"), error=row.get("error_message"))


@app.get("/tasks")
async def list_tasks(limit: int = 50) -> List[Dict[str, Any]]:
    if store is None:
        raise HTTPException(503, "后端未配置 Supabase")
    return store.list_tasks(min(max(limit, 1), 100))


@app.get("/healthz")
async def healthz() -> Dict[str, str]:
    issues = configuration_issues()
    return {
        "status": "ok" if not issues else "degraded",
        "supabase": "configured" if store is not None else "not_configured",
        "configuration": "ready" if not issues else "missing:" + ",".join(issues),
    }
