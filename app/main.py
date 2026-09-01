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
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Literal, Optional, TypedDict
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, File, Header, HTTPException, UploadFile
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


# The API contract keeps English top-level JSON keys so the existing frontend
# can render reports without a protocol change. All human-readable values
# inside those keys must be Simplified Chinese, even when source filings are
# written in English.
CHINESE_OUTPUT_RULES = """
输出语言与准确性要求（必须遵守）：
1. 即使财报原文为英文，也必须先理解原文，最终所有自然语言内容使用简体中文。
2. API 顶层 JSON 键名必须保持题目要求的英文协议键名（如 metrics、risks、summary、sources），不要改名；键名之外的指标名称、notes、风险 level/title/evidence/suggestion、summary、industry_comparison 及 Markdown 文本全部使用简体中文。
3. company 输出中文公司名；如无法确定中文名，可写“官方英文名（中文名待确认）”，不要只输出英文公司名。period 使用中文表达（例如“2025年度”或“截至2025年3月31日的财年”）。
4. 数字、币种、百分比、单位、page、chunk_id 和来源 ID 必须保持证据中的准确原样，不翻译或改写来源 ID，不得编造任何数字和页码。
5. 不得输出股票买卖建议、目标价、股价预测或交易策略。
6. 输出前自检：除固定 JSON 键名和来源 ID 外，不得出现英文句子；缺失信息填 null 或“未披露”，不要猜测。
"""


class AgentState(TypedDict, total=False):
    task_id: str
    file_path: str
    status: TaskStatus
    page_count: int
    chunk_count: int
    table_count: int
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
            return {"text": text, "tables": tables, "chunks": chunks, "page_count": len(pages)}
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


class TaskListItem(BaseModel):
    """Stable, minimal task shape for the history sidebar."""

    id: str
    filename: str
    status: TaskStatus
    error_message: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class SupabaseStore:
    def __init__(self) -> None:
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_SERVICE_KEY")
        if not url or not key:
            raise RuntimeError("SUPABASE_URL 与 SUPABASE_SERVICE_KEY 必须配置")
        self.client: Client = create_client(url, key)
        self.storage_bucket = os.getenv("SUPABASE_STORAGE_BUCKET", "annual-reports").strip() or "annual-reports"

    @staticmethod
    def _safe_filename(filename: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(filename).name).strip("._")
        return cleaned[:160] or "annual-report.pdf"

    def ensure_storage_bucket(self) -> None:
        """Create the private bucket on first boot when the service key permits it."""
        try:
            buckets = self.client.storage.list_buckets()
            names = self._bucket_names(buckets)
            if self.storage_bucket not in names:
                self.client.storage.create_bucket(self.storage_bucket, options={"public": False})
        except Exception as exc:
            # Storage may be provisioned manually or restricted by a project policy.
            # Uploads will surface the actionable error instead of failing startup.
            if "409" not in str(exc) and "Duplicate" not in str(exc):
                print(f"Supabase Storage bucket check failed: {exc}")

    @staticmethod
    def _bucket_names(buckets: Any) -> set[str]:
        return {
            item.get("name") if isinstance(item, dict) else getattr(item, "name", None)
            for item in (buckets or [])
        }

    def storage_bucket_exists(self) -> bool:
        buckets = self.client.storage.list_buckets()
        return self.storage_bucket in self._bucket_names(buckets)

    def upload_document(self, task_id: str, filename: str, content: bytes) -> str:
        path = f"{task_id}/{self._safe_filename(filename)}"
        # Free Supabase instances can occasionally close a large upload before
        # the response arrives. Retry the same idempotent path a few times so a
        # transient TLS/network interruption does not leave a task unusable.
        try:
            retry_count = max(1, min(int(os.getenv("STORAGE_UPLOAD_RETRIES", "3")), 5))
        except ValueError:
            retry_count = 3
        options = {"content-type": "application/pdf", "cache-control": "3600", "upsert": "false"}
        last_error: Optional[Exception] = None
        uploaded = False
        for attempt in range(retry_count):
            try:
                self.client.storage.from_(self.storage_bucket).upload(path, content, options)
                uploaded = True
                break
            except Exception as exc:
                last_error = exc
                if attempt == retry_count - 1:
                    raise
                time.sleep(min(2.0, 0.5 * (attempt + 1)))
        if not uploaded and last_error is not None:
            raise last_error
        return f"supabase://{self.storage_bucket}/{path}"

    def download_document(self, storage_path: str) -> bytes:
        if not storage_path.startswith("supabase://"):
            return Path(storage_path).read_bytes()
        target = storage_path.removeprefix("supabase://")
        bucket, separator, path = target.partition("/")
        if not separator or not bucket or not path:
            raise ValueError("无效的 Supabase Storage 路径")
        return self.client.storage.from_(bucket).download(path)

    def delete_document(self, storage_path: str) -> None:
        if not storage_path.startswith("supabase://"):
            return
        target = storage_path.removeprefix("supabase://")
        bucket, separator, path = target.partition("/")
        if separator and bucket and path:
            self.client.storage.from_(bucket).remove([path])

    def update_storage_path(self, task_id: str, storage_path: str) -> None:
        self.client.table("analysis_tasks").update({"storage_path": storage_path}).eq("id", task_id).execute()

    def create_task(self, filename: str, storage_path: str, task_id: Optional[str] = None) -> str:
        task_id = task_id or str(uuid4())
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
        if not rows:
            return

        # Supabase free instances enforce a relatively short statement timeout.
        # Keep vector payloads small and split a timed-out batch recursively so a
        # long annual report does not lose all of its pgvector evidence.
        try:
            configured_size = int(os.getenv("VECTOR_DB_BATCH_SIZE", "8"))
        except ValueError:
            configured_size = 8
        batch_size = max(1, min(configured_size, 50))

        def upsert_batch(batch: List[Dict[str, Any]]) -> None:
            try:
                self.client.table("document_chunks").upsert(batch, on_conflict="task_id,chunk_id").execute()
            except Exception:
                if len(batch) <= 1:
                    raise
                midpoint = len(batch) // 2
                upsert_batch(batch[:midpoint])
                upsert_batch(batch[midpoint:])

        for start in range(0, len(rows), batch_size):
            upsert_batch(rows[start : start + batch_size])

    def search_chunks(self, task_id: str, vector: List[float], limit: int = 8) -> List[Dict[str, Any]]:
        response = self.client.rpc(
            "match_document_chunks",
            {"query_embedding": vector, "match_task_id": task_id, "match_count": limit},
        ).execute()
        return response.data or []

    def get_task(self, task_id: str) -> Dict[str, Any]:
        try:
            task = self.client.table("analysis_tasks").select("*").eq("id", task_id).single().execute().data
            report_response = self.client.table("analysis_reports").select("markdown,report_json").eq("task_id", task_id).maybe_single().execute()
            report = getattr(report_response, "data", None) if report_response else None
        except Exception as exc:
            # Supabase's single() raises PGRST116 when no row exists.
            if getattr(exc, "code", None) == "PGRST116" or "0 rows" in str(exc).lower():
                raise KeyError(task_id) from exc
            raise
        if not task:
            raise KeyError(task_id)
        return {**task, "report_markdown": (report or {}).get("markdown"), "report_json": (report or {}).get("report_json")}

    def list_tasks(self, limit: int = 50) -> List[Dict[str, Any]]:
        return (
            self.client.table("analysis_tasks")
            .select("id,filename,status,error_message,created_at,updated_at")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
            .data
            or []
        )

    def append_event(self, task_id: str, event: str, payload: Dict[str, Any]) -> Optional[int]:
        response = self.client.table("analysis_task_events").insert(
            {"task_id": task_id, "event": event, "payload": payload}
        ).execute()
        rows = response.data or []
        value = rows[0].get("id") if rows and isinstance(rows[0], dict) else None
        return int(value) if value is not None else None

    def list_events(self, task_id: str, after_id: int = 0, limit: int = 500) -> List[Dict[str, Any]]:
        response = (
            self.client.table("analysis_task_events")
            .select("id,event,payload,created_at")
            .eq("task_id", task_id)
            .gt("id", after_id)
            .order("id")
            .limit(limit)
            .execute()
        )
        return response.data or []

    def list_recoverable_tasks(self) -> List[Dict[str, Any]]:
        active = ["pending", "parsing", "finance_analyzing", "risk_analyzing"]
        return self.client.table("analysis_tasks").select("id,storage_path,status").in_("status", active).execute().data or []


class ReportAgents:
    def __init__(self, store: SupabaseStore, emit):
        self.store, self.emit = store, emit
        if ChatOpenAI is None or OpenAIEmbeddings is None:
            raise RuntimeError("LangChain依赖未安装")
        provider = os.getenv("LLM_PROVIDER", "openai").strip().lower()
        llm_configs = {
            "deepseek": (
                os.getenv("DEEPSEEK_API_KEY", "").strip(),
                os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
                os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
                "DEEPSEEK_API_KEY",
            ),
            "qwen": (
                os.getenv("DASHSCOPE_API_KEY", "").strip(),
                os.getenv("QWEN_MODEL", "qwen-plus"),
                os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
                "DASHSCOPE_API_KEY",
            ),
            "openai": (
                os.getenv("OPENAI_API_KEY", "").strip(),
                os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
                "OPENAI_API_KEY",
            ),
        }
        if provider not in llm_configs:
            raise RuntimeError("LLM_PROVIDER 只支持 deepseek、qwen 或 openai")
        llm_key, llm_model, llm_base_url, llm_key_name = llm_configs[provider]
        if not llm_key:
            raise RuntimeError(f"LLM_PROVIDER={provider} 时必须配置 {llm_key_name}")
        self.llm = ChatOpenAI(model=llm_model, temperature=0, api_key=llm_key, base_url=llm_base_url)
        self.model_timeout_seconds = _env_seconds("MODEL_REQUEST_TIMEOUT_SECONDS", 90)
        self.embedding_timeout_seconds = _env_seconds("EMBEDDING_REQUEST_TIMEOUT_SECONDS", 45)

        # DeepSeek目前没有公开embedding接口；Qwen兼容OpenAI的embedding协议，
        # 因此默认用Qwen text-embedding-v2，输出1536维，与schema.sql一致。
        embedding_provider = os.getenv("EMBEDDING_PROVIDER", "qwen" if provider in {"deepseek", "qwen"} else "openai").strip().lower()
        if embedding_provider == "qwen":
            embedding_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
            embedding_model = os.getenv("QWEN_EMBEDDING_MODEL", "text-embedding-v2")
            embedding_base_url = os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
            embedding_key_name = "DASHSCOPE_API_KEY"
        elif embedding_provider == "openai":
            embedding_key = os.getenv("OPENAI_API_KEY", "").strip()
            embedding_model = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
            embedding_base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
            embedding_key_name = "OPENAI_API_KEY"
        else:
            raise RuntimeError("EMBEDDING_PROVIDER 只支持 qwen 或 openai")
        if not embedding_key:
            raise RuntimeError(f"EMBEDDING_PROVIDER={embedding_provider} 时必须配置 {embedding_key_name}")
        self.embeddings = OpenAIEmbeddings(
            model=embedding_model,
            api_key=embedding_key,
            base_url=embedding_base_url,
            # tiktoken没有千问模型编码，关闭本地token计数，避免未知模型名导致初始化失败。
            tiktoken_enabled=embedding_provider == "openai",
            # Qwen's compatible endpoint accepts our bounded 1800-character chunks;
            # skip HuggingFace tokenization so deployments do not need transformers.
            check_embedding_ctx_length=embedding_provider == "openai",
        )
        self.finance_parser = JsonReportParser(required_fields=["metrics", "notes", "sources"])
        self.risk_parser = JsonReportParser(required_fields=["risks", "anomalies", "sources"])
        self.report_parser = JsonReportParser(required_fields=["company", "period", "metrics", "risks", "summary", "industry_comparison", "sources"])
        self.pdf_tool = PDFParseTool()
        self.rag_tool = RagRetrieveTool(store=store, embeddings=self.embeddings)

    async def _invoke_json(self, prompt: str, parser: JsonReportParser, retries: int = 2) -> Dict[str, Any]:
        last: Exception | None = None
        for attempt in range(retries + 1):
            try:
                result = await asyncio.wait_for(
                    self.llm.ainvoke(prompt), timeout=self.model_timeout_seconds
                )
                content = result.content if hasattr(result, "content") else str(result)
                parsed = parser.parse(content)
                return self._normalize_sources(self._normalize_chinese_fields(parsed))
            except Exception as exc:
                last = exc
                if attempt < retries:
                    await asyncio.sleep(2**attempt)
        raise RuntimeError(f"大模型或格式解析失败: {last}") from last

    @staticmethod
    def _normalize_chinese_fields(value: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize frequent English metric aliases without changing the API shape."""
        metric_aliases = {
            "revenue": "营业收入",
            "total_revenue": "营业收入",
            "operating_revenue": "营业收入",
            "net_income": "净利润",
            "net_profit": "净利润",
            "gross_margin": "毛利率",
            "gross_profit_margin": "毛利率",
            "operating_cash_flow": "经营活动现金流",
            "cash_flow_from_operations": "经营活动现金流",
            "free_cash_flow": "自由现金流",
            "operating_profit": "营业利润",
            "operating_margin": "营业利润率",
            "total_assets": "资产总额",
            "total_liabilities": "负债总额",
            "cash_and_equivalents": "现金及现金等价物",
            "rd_expense": "研发费用",
            "rd_expense_ratio": "研发费用率",
        }
        metrics = value.get("metrics")
        if isinstance(metrics, dict):
            normalized: Dict[str, Any] = {}
            for name, metric_value in metrics.items():
                alias = metric_aliases.get(str(name).strip().lower(), name)
                if isinstance(metric_value, dict):
                    # Prefer the base-period amount over a growth percentage.
                    # Models occasionally put 13.86% in `value` and 751766 in
                    # `数值`; charts must use the latter without discarding yoy.
                    def numeric(candidate: Any) -> Optional[float]:
                        if isinstance(candidate, (int, float)) and not isinstance(candidate, bool):
                            return float(candidate)
                        if isinstance(candidate, str):
                            match = re.match(r"^\s*([-+]?\d[\d,]*(?:\.\d+)?)", candidate)
                            if match:
                                return float(match.group(1).replace(",", ""))
                        return None

                    preferred_keys = (
                        "数值", "金额", "本期", "当前值", "amount", "number", "current", "value"
                    )
                    selected_key = next((key for key in preferred_keys if numeric(metric_value.get(key)) is not None), None)
                    if selected_key is not None:
                        metric_value["value"] = numeric(metric_value[selected_key])
                    else:
                        # Period-keyed model output may not use a predictable key;
                        # skip sources and obvious change labels when selecting a
                        # fallback scalar for chart rendering.
                        for detail_name, detail_value in metric_value.items():
                            if detail_name == "sources" or any(token in str(detail_name).lower() for token in ("同比", "环比", "yoy", "qoq", "growth", "change")):
                                continue
                            parsed = numeric(detail_value)
                            if parsed is not None:
                                metric_value["value"] = parsed
                                break
                normalized[str(alias)] = metric_value
            value["metrics"] = normalized

        risks = value.get("risks")
        if isinstance(risks, list):
            for risk in risks:
                if not isinstance(risk, dict):
                    continue
                level = str(risk.get("level", "")).strip().lower()
                risk["level"] = {"high": "高", "medium": "中", "low": "低", "critical": "高"}.get(level, risk.get("level", "中"))

        company = value.get("company")
        company_aliases = {
            "alibaba group": "阿里巴巴集团",
            "alibaba": "阿里巴巴集团",
            "tencent holdings": "腾讯控股",
            "tencent": "腾讯控股",
            "baidu": "百度集团",
            "baidu, inc.": "百度集团",
        }
        if isinstance(company, str):
            value["company"] = company_aliases.get(company.strip().lower(), company)
        period = value.get("period")
        if isinstance(period, str):
            value["period"] = re.sub(r"^FY\s*(\d{4})$", r"\1财年", period.strip(), flags=re.I)
        return value

    @staticmethod
    def _normalize_sources(value: Any, key: str | None = None) -> Any:
        """Convert model source ids such as p4-c1 into page-addressable objects."""
        if isinstance(value, list):
            return [ReportAgents._normalize_sources(item, key) for item in value]
        if isinstance(value, dict):
            return {name: ReportAgents._normalize_sources(item, name) for name, item in value.items()}
        if key == "sources" and isinstance(value, str):
            match = re.fullmatch(r"p(\d+)-(.+)", value.strip(), flags=re.IGNORECASE)
            if match:
                return {"page": int(match.group(1)), "chunk_id": value.strip()}
            return {"chunk_id": value.strip()}
        return value

    async def _retrieve(self, state: AgentState, query: str) -> List[Dict[str, Any]]:
        matches = await asyncio.wait_for(
            self.rag_tool.ainvoke({"task_id": state["task_id"], "query": query, "top_k": 8}),
            timeout=self.embedding_timeout_seconds,
        )
        if not matches:
            await self.emit(state["task_id"], "warning", {"message": "未检索到相关财报片段"})
        return matches

    async def _progress(self, task_id: str, agent: str, message: str) -> None:
        """Emit an auditable execution summary, never hidden model reasoning."""
        await self.emit(task_id, "agent", {"agent": agent, "output": f"> {message}"})

    @staticmethod
    def _evidence_text(chunks: List[Dict[str, Any]]) -> str:
        def field(chunk: Any, key: str, fallback: str = "") -> Any:
            if isinstance(chunk, dict):
                return chunk.get(key, fallback)
            return fallback

        return "\n\n".join(
            f"[source page={field(c, 'page', '?')} chunk={field(c, 'chunk_id', '?')}] {field(c, 'content') or field(c, 'text')}"
            for c in chunks
        )

    @staticmethod
    def _sources(chunks: List[Any]) -> List[Dict[str, Any]]:
        return [
            {"page": c.get("page"), "chunk_id": c.get("chunk_id")}
            for c in chunks
            if isinstance(c, dict)
        ]

    async def parsing(self, state: AgentState) -> AgentState:
        await asyncio.to_thread(self.store.update_task, state["task_id"], "parsing")
        await self.emit(state["task_id"], "status", {"status": "parsing"})
        await self._progress(state["task_id"], "document_parser", "正在读取 PDF 页面并提取文本与表格…")
        parsed = await self.pdf_tool.ainvoke(state["file_path"])
        await self._progress(state["task_id"], "document_parser", f"已提取 {len(parsed['chunks'])} 个页级片段，正在清洗并建立来源索引…")
        try:
            vectors: List[List[float]] = []
            # Qwen's compatible embedding endpoint accepts at most 25 inputs per request.
            # Keep the batch below that limit so pgvector ingestion is real, not fallback-only.
            for start in range(0, len(parsed["chunks"]), 24):
                batch = parsed["chunks"][start : start + 24]
                await self._progress(state["task_id"], "document_parser", f"正在生成第 {start + 1}-{start + len(batch)} 个片段的向量…")
                vectors.extend(
                    await asyncio.wait_for(
                        self.embeddings.aembed_documents([c["text"] for c in batch]),
                        timeout=self.embedding_timeout_seconds,
                    )
                )
            await asyncio.to_thread(self.store.save_chunks, state["task_id"], parsed["chunks"], vectors)
            await self._progress(state["task_id"], "document_parser", f"向量已写入 Supabase，共 {len(vectors)} 个可检索片段。")
        except Exception as exc:
            await self.emit(state["task_id"], "warning", {"message": f"向量入库失败，将继续页级分析: {exc}"})
            await self._progress(state["task_id"], "document_parser", "向量库暂不可用，将使用页级片段继续分析。")
        self.rag_tool.fallback_chunks[state["task_id"]] = parsed["chunks"]
        await self._progress(state["task_id"], "document_parser", f"文档解析完成：{parsed['page_count']} 页、{len(parsed['chunks'])} 个片段、{len(parsed['tables'])} 张表格已准备好。")
        # Keep the graph state small: agents retrieve evidence through RAG, so
        # retaining the complete PDF text/chunk list here would duplicate a
        # potentially hundreds-of-pages document across every subsequent node.
        return {
            **state,
            "status": "parsing",
            "page_count": parsed["page_count"],
            "chunk_count": len(parsed["chunks"]),
            "table_count": len(parsed["tables"]),
        }

    async def finance(self, state: AgentState) -> AgentState:
        await asyncio.to_thread(self.store.update_task, state["task_id"], "finance_analyzing")
        await self.emit(state["task_id"], "status", {"status": "finance_analyzing"})
        await self._progress(state["task_id"], "finance_analyst", "正在检索营收、净利润、现金流和毛利率相关片段…")
        evidence = await self._retrieve(state, "营收 净利润 经营活动现金流 毛利率 同比 环比 财务指标")
        await self._progress(state["task_id"], "finance_analyst", f"已召回 {len(evidence)} 个证据片段，正在核对同比与环比数字…")
        result = await self._invoke_json(CHINESE_OUTPUT_RULES + """
你是财务分析师Agent。只输出JSON对象，字段metrics(dict)、notes(array)、sources(array)。每个关键metric尽量附sources数组，引用必须来自给定片段的page/chunk_id，禁止编造页码。从以下证据提取营业收入、净利润、经营活动现金流、毛利率及同比/环比；缺失用null或“未披露”。指标名称必须使用简体中文，数值必须与原文一致。
财报检索证据：
""" + self._evidence_text(evidence), self.finance_parser)
        result.setdefault("sources", self._sources(evidence))
        await self._progress(state["task_id"], "finance_analyst", "财务指标提取完成，正在整理每项指标的来源页码。")
        await self.emit(state["task_id"], "agent", {"agent": "finance_analyst", "output": result})
        return {**state, "finance": result}

    async def risk(self, state: AgentState) -> AgentState:
        await asyncio.to_thread(self.store.update_task, state["task_id"], "risk_analyzing")
        await self.emit(state["task_id"], "status", {"status": "risk_analyzing"})
        await self._progress(state["task_id"], "risk_reviewer", "正在检索负债、现金流、异常波动和审计意见片段…")
        evidence = await self._retrieve(state, "资产负债 现金流 异常 波动 关联交易 审计意见 风险")
        await self._progress(state["task_id"], "risk_reviewer", f"已召回 {len(evidence)} 个证据片段，正在区分风险等级并核对证据…")
        result = await self._invoke_json(CHINESE_OUTPUT_RULES + """
你是风险研判Agent。只输出JSON对象，字段risks(array，每项含level、title、evidence、suggestion、sources)、anomalies(array)、sources(array)。level使用“高/中/低”。风险标题、证据摘要、跟踪建议和异常描述必须使用简体中文；sources只能引用证据片段中的page/chunk_id，不得编造页码。
财报检索证据：
""" + self._evidence_text(evidence), self.risk_parser)
        result.setdefault("sources", self._sources(evidence))
        await self._progress(state["task_id"], "risk_reviewer", "风险事项整理完成，关键判断均保留原文页码。")
        await self.emit(state["task_id"], "agent", {"agent": "risk_reviewer", "output": result})
        return {**state, "risks": result}

    async def summary(self, state: AgentState) -> AgentState:
        await self._progress(state["task_id"], "industry_summary", "正在检索主营业务、分部表现与竞争环境原文…")
        industry_evidence = await self._retrieve(
            state,
            "主营业务 行业竞争 市场份额 分部收入 业务结构 同业对比 竞争环境",
        )
        await self._progress(
            state["task_id"],
            "industry_summary",
            f"已召回 {len(industry_evidence)} 个行业证据片段，正在汇总财务指标与风险事项…",
        )
        prompt = CHINESE_OUTPUT_RULES + """
你是行业对比与汇总Agent。只输出JSON对象，必须包含company、period、metrics、risks、summary、industry_comparison、sources。整合前面Agent的结果，所有结论、行业比较和 Markdown 正文必须使用简体中文；为关键结论保留sources(page/chunk_id)。不得给出买卖或股价预测建议。
财务分析：
%s
风险分析：
%s
行业与业务原文证据：
%s
""" % (
            json.dumps(state.get("finance", {}), ensure_ascii=False),
            json.dumps(state.get("risks", {}), ensure_ascii=False),
            self._evidence_text(industry_evidence),
        )
        result = await self._invoke_json(prompt, self.report_parser)
        await self._progress(state["task_id"], "industry_summary", "汇总完成，正在生成 Markdown 报告并写入任务记录…")
        finance_state = state.get("finance") if isinstance(state.get("finance"), dict) else {}
        risks_state = state.get("risks") if isinstance(state.get("risks"), dict) else {}
        result.setdefault(
            "sources",
            finance_state.get("sources", [])
            + risks_state.get("sources", [])
            + self._sources(industry_evidence),
        )
        markdown = self._to_markdown(result)
        await asyncio.to_thread(self.store.save_report, state["task_id"], markdown, result)
        await asyncio.to_thread(self.store.update_task, state["task_id"], "summary_finish")
        # Send the final agent output before the terminal status. Browsers close
        # EventSource as soon as summary_finish arrives, so reversing this order
        # guarantees the rendered report is also visible in the live stream.
        await self.emit(state["task_id"], "agent", {"agent": "industry_summary", "output": markdown})
        await self.emit(state["task_id"], "status", {"status": "summary_finish"})
        return {**state, "status": "summary_finish", "report_json": result, "report_markdown": markdown}

    @staticmethod
    def _to_markdown(data: Dict[str, Any]) -> str:
        def display_name(name: Any) -> str:
            return {
                "value": "数值",
                "current": "当前值",
                "amount": "金额",
                "number": "数值",
                "unit": "单位",
                "yoy": "同比",
                "qoq": "环比",
                "说明": "说明",
            }.get(str(name), str(name))

        def append_details(target: List[str], obj: Dict[str, Any], indent: int) -> None:
            for detail_name, detail_value in obj.items():
                if detail_name == "sources":
                    continue
                prefix = "  " * indent
                label = display_name(detail_name)
                if isinstance(detail_value, dict):
                    target.append(f"{prefix}- {label}")
                    append_details(target, detail_value, indent + 1)
                elif isinstance(detail_value, list):
                    rendered = json.dumps(detail_value, ensure_ascii=False)
                    target.append(f"{prefix}- {label}：{rendered}")
                else:
                    rendered = "未披露" if detail_value is None else str(detail_value)
                    target.append(f"{prefix}- {label}：{rendered}")
            sources = obj.get("sources")
            if isinstance(sources, list):
                source_labels = ", ".join(
                    "第%s页/%s" % (source.get("page"), source.get("chunk_id"))
                    for source in sources
                    if isinstance(source, dict)
                )
                if source_labels:
                    target.append(f"{'  ' * indent}- 来源：{source_labels}")

        lines = ["# 投研分析报告", "", f"- 公司：{data.get('company', '未识别')}", f"- 期间：{data.get('period', '未识别')}", "", "## 核心指标", ""]
        metrics = data.get("metrics", {})
        for name, value in metrics.items() if isinstance(metrics, dict) else []:
            if not isinstance(value, dict):
                lines.append(f"- **{name}**：{value if value is not None else '未披露'}")
                continue
            lines.append(f"- **{name}**")
            append_details(lines, value, 1)
        lines.extend(["", "## 风险与异常", ""])
        for risk in data.get("risks", []) if isinstance(data.get("risks"), list) else []:
            if not isinstance(risk, dict):
                lines.append(f"- [unknown] {risk}")
                continue
            lines.append(f"- [{risk.get('level', 'unknown')}] {risk.get('title', '待确认事项')}")
            if risk.get("evidence"):
                lines.append(f"  - 证据：{risk['evidence']}")
            if risk.get("suggestion"):
                lines.append(f"  - 跟踪建议：{risk['suggestion']}")
            if isinstance(risk.get("sources"), list):
                source_labels = ", ".join("第%s页/%s" % (s.get("page"), s.get("chunk_id")) for s in risk["sources"] if isinstance(s, dict))
                lines.append(f"  - 来源：{source_labels}")
        lines.extend(["", "## 结论", "", str(data.get("summary", "")), "", "## 来源片段", ""])
        for source in data.get("sources", []) if isinstance(data.get("sources"), list) else []:
            if isinstance(source, dict):
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
# Queues only cover currently connected clients. Durable replay comes from
# analysis_task_events, so a process restart does not erase the audit trail.
events: Dict[str, "asyncio.Queue[Dict[str, Any]]"] = {}
event_persistence_available: Optional[bool] = None
event_replay_state: Optional[str] = "checking"
storage_state: Optional[str] = "checking"
event_replay_checked_at = 0.0
analysis_semaphore: Optional[asyncio.Semaphore] = None


def task_queue(task_id: str) -> "asyncio.Queue[Dict[str, Any]]":
    return events.setdefault(task_id, asyncio.Queue(maxsize=1000))


async def probe_event_replay() -> None:
    """Probe the optional replay table without blocking health checks."""
    global event_persistence_available, event_replay_state, storage_state, event_replay_checked_at
    if store is None:
        event_replay_state = "not_configured"
        storage_state = "not_configured"
        return
    try:
        await asyncio.wait_for(
            asyncio.to_thread(store.client.table("analysis_task_events").select("id").limit(1).execute),
            timeout=3.0,
        )
        event_replay_state = "available"
        event_persistence_available = True
    except Exception:
        event_replay_state = "unavailable_run_schema_sql"
        event_persistence_available = False
    try:
        bucket_exists = await asyncio.wait_for(
            asyncio.to_thread(store.storage_bucket_exists),
            timeout=3.0,
        )
        storage_state = "available" if bucket_exists else "missing_create_bucket"
    except asyncio.TimeoutError:
        storage_state = "unavailable_timeout"
    except Exception as exc:
        storage_state = "unavailable"
        print(f"Supabase Storage bucket probe failed: {exc}")
    event_replay_checked_at = time.monotonic()


async def initialize_background() -> None:
    """Run non-critical Supabase provisioning after the HTTP server is live."""
    assert store is not None
    if os.getenv("SUPABASE_STORAGE_AUTO_CREATE", "true").strip().lower() in {"1", "true", "yes"}:
        try:
            await asyncio.wait_for(asyncio.to_thread(store.ensure_storage_bucket), timeout=8.0)
        except asyncio.TimeoutError:
            print("Supabase Storage bucket check timed out; continuing in degraded mode")
    try:
        recoverable = await asyncio.wait_for(asyncio.to_thread(store.list_recoverable_tasks), timeout=8.0)
    except asyncio.TimeoutError:
        recoverable = []
        print("Recoverable task scan timed out; continuing without automatic recovery")
    except Exception as exc:
        recoverable = []
        print(f"Recoverable task scan failed: {exc}")
    for task in recoverable:
        task_queue(str(task["id"]))
        asyncio.create_task(resume_task(task))
    await probe_event_replay()


def format_sse(event: str, payload: Dict[str, Any], event_id: Optional[int] = None) -> str:
    lines: List[str] = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    lines.append("data: " + json.dumps(payload, ensure_ascii=False))
    return "\n".join(lines) + "\n\n"


def configuration_issues() -> List[str]:
    """Return actionable configuration gaps without exposing secret values."""
    issues: List[str] = []
    provider = os.getenv("LLM_PROVIDER", "openai").strip().lower()
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    deepseek_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    dashscope_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
    supabase_url = os.getenv("SUPABASE_URL", "").strip()
    supabase_key = os.getenv("SUPABASE_SERVICE_KEY", "").strip()
    if provider == "deepseek":
        if not deepseek_key or deepseek_key in {"replace-me", "your-deepseek-key"}:
            issues.append("DEEPSEEK_API_KEY")
    elif provider == "qwen":
        if not dashscope_key or dashscope_key in {"replace-me", "your-dashscope-key"}:
            issues.append("DASHSCOPE_API_KEY")
    elif not openai_key or openai_key in {"replace-me", "your-openai-key"}:
        issues.append("OPENAI_API_KEY")
    embedding_provider = os.getenv("EMBEDDING_PROVIDER", "qwen" if provider in {"deepseek", "qwen"} else "openai").strip().lower()
    if provider not in {"deepseek", "qwen", "openai"}:
        issues.append("LLM_PROVIDER")
    if embedding_provider not in {"qwen", "openai"}:
        issues.append("EMBEDDING_PROVIDER")
    if embedding_provider == "qwen" and (not dashscope_key or dashscope_key in {"replace-me", "your-dashscope-key"}) and "DASHSCOPE_API_KEY" not in issues:
        issues.append("DASHSCOPE_API_KEY (embeddings)")
    elif embedding_provider == "openai" and (not openai_key or openai_key in {"replace-me", "your-openai-key"}) and "OPENAI_API_KEY" not in issues:
        issues.append("OPENAI_API_KEY (embeddings)")
    if not supabase_url or "your-project.supabase.co" in supabase_url:
        issues.append("SUPABASE_URL")
    if not supabase_key or supabase_key in {"replace-me", "your-service-role-key"}:
        issues.append("SUPABASE_SERVICE_KEY")
    return list(dict.fromkeys(issues))


def _env_seconds(name: str, default: float) -> float:
    """Parse a positive timeout from env without allowing bad values to crash startup."""
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(1.0, min(value, 600.0))


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def max_upload_bytes() -> int:
    """Parse the upload limit defensively so a bad env value cannot cause 500s."""
    try:
        configured = int(os.getenv("MAX_UPLOAD_BYTES", str(50 * 1024 * 1024)))
    except ValueError:
        configured = 50 * 1024 * 1024
    return max(1, configured)


async def read_pdf_upload(file: UploadFile, limit: int) -> bytes:
    """Read an upload in bounded chunks and reject non-PDF content."""
    chunks: List[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise HTTPException(400, "文件超过大小限制")
        chunks.append(chunk)
    data = b"".join(chunks)
    if not data:
        raise HTTPException(400, "文件为空")
    if not data.startswith(b"%PDF-"):
        raise HTTPException(400, "文件内容不是有效PDF")
    return data


async def emit(task_id: str, event: str, payload: Dict[str, Any]) -> None:
    global event_persistence_available
    queue = task_queue(task_id)
    output = payload.get("output")
    messages: List[Dict[str, Any]] = []
    if event == "agent" and isinstance(output, str) and len(output) > 600:
        chunks = [output[i : i + 600] for i in range(0, len(output), 600)]
        messages = [
            {
                "event": event,
                "payload": {**payload, "output": chunk, "chunk_index": index, "chunk_total": len(chunks)},
            }
            for index, chunk in enumerate(chunks)
        ]
    else:
        messages = [{"event": event, "payload": payload}]

    for message in messages:
        event_id: Optional[int] = None
        if store is not None and event_persistence_available is not False:
            try:
                event_id = await asyncio.to_thread(
                    store.append_event, task_id, message["event"], message["payload"]
                )
                event_persistence_available = True
            except Exception as exc:
                # Event persistence must not stop the graph. The in-memory
                # queue still keeps an active browser connected in degraded mode.
                event_persistence_available = False
                print(f"Supabase event persistence disabled: {exc}")
        envelope = {**message, "id": event_id}
        try:
            queue.put_nowait(envelope)
        except asyncio.QueueFull:
            # Durable replay is authoritative; retain only the newest live
            # messages when a browser has been disconnected for a long time.
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            queue.put_nowait(envelope)


async def run_graph(task_id: str, file_path: str) -> None:
    assert store is not None
    global analysis_semaphore
    if analysis_semaphore is None:
        analysis_semaphore = asyncio.Semaphore(_env_int("MAX_CONCURRENT_ANALYSES", 1, 1, 4))
    try:
        async with analysis_semaphore:
            agents = ReportAgents(store, emit)
            graph = StateGraph(AgentState)
            # Keep node names distinct from AgentState keys; LangGraph rejects collisions.
            graph.add_node("parsing_agent", agents.parsing)
            graph.add_node("finance_agent", agents.finance)
            graph.add_node("risk_agent", agents.risk)
            graph.add_node("summary_agent", agents.summary)
            graph.add_edge(START, "parsing_agent")
            graph.add_edge("parsing_agent", "finance_agent")
            graph.add_edge("finance_agent", "risk_agent")
            graph.add_edge("risk_agent", "summary_agent")
            graph.add_edge("summary_agent", END)
            await graph.compile().ainvoke({"task_id": task_id, "file_path": file_path, "status": "pending"})
    except Exception as exc:
        try:
            await asyncio.to_thread(store.update_task, task_id, "error", str(exc))
        except Exception as persist_exc:
            print(f"Failed to persist task error for {task_id}: {persist_exc}")
        await emit(task_id, "status", {"status": "error", "error": str(exc)})
    finally:
        try:
            Path(file_path).unlink(missing_ok=True)
        except OSError:
            pass
        # A completed task is replayed from Supabase when available. Do not
        # retain up to 1000 in-memory messages for every historical task.
        events.pop(task_id, None)


async def resume_task(task: Dict[str, Any]) -> None:
    """Resume an unfinished task after a process restart."""
    task_id = str(task["id"])
    storage_path = str(task.get("storage_path") or "")
    temp_path: Optional[str] = None
    try:
        if not storage_path:
            raise FileNotFoundError("任务没有可恢复的文档路径")
        if storage_path.startswith("supabase://"):
            assert store is not None
            content = await asyncio.to_thread(store.download_document, storage_path)
            temp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
            temp.write(content)
            temp.close()
            temp_path = temp.name
        elif Path(storage_path).is_file():
            temp_path = storage_path
        else:
            raise FileNotFoundError("原始 PDF 不存在，无法恢复任务")
        task_queue(task_id)
        await run_graph(task_id, temp_path)
    except Exception as exc:
        assert store is not None
        try:
            await asyncio.to_thread(store.update_task, task_id, "error", str(exc))
        except Exception as persist_exc:
            print(f"Failed to persist recovery error for {task_id}: {persist_exc}")
        await emit(task_id, "status", {"status": "error", "error": str(exc)})
    finally:
        if temp_path and storage_path.startswith("supabase://"):
            try:
                Path(temp_path).unlink(missing_ok=True)
            except OSError:
                pass


@app.on_event("startup")
async def startup() -> None:
    global store, analysis_semaphore
    try:
        store = SupabaseStore()
        analysis_semaphore = asyncio.Semaphore(_env_int("MAX_CONCURRENT_ANALYSES", 1, 1, 4))
        asyncio.create_task(initialize_background())
    except RuntimeError:
        # Keep healthz and the local demo frontend available until secrets are configured.
        store = None


@app.post("/tasks", response_model=TaskCreateResponse)
async def upload_pdf(background: BackgroundTasks, file: UploadFile = File(...)) -> TaskCreateResponse:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "仅支持PDF文件")
    if store is None:
        raise HTTPException(503, "后端未配置 SUPABASE_URL 或 SUPABASE_SERVICE_KEY")
    
    data = await read_pdf_upload(file, max_upload_bytes())
    task_id = str(uuid4())
    placeholder = f"pending://{task_id}/{SupabaseStore._safe_filename(file.filename)}"
    storage_path: Optional[str] = None
    try:
        await asyncio.to_thread(store.create_task, file.filename, placeholder, task_id)
        storage_path = await asyncio.to_thread(store.upload_document, task_id, file.filename, data)
        await asyncio.to_thread(store.update_storage_path, task_id, storage_path)
        temp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
        temp.write(data)
        temp.close()
    except Exception as exc:
        if storage_path:
            try:
                await asyncio.to_thread(store.delete_document, storage_path)
            except Exception as cleanup_exc:
                print(f"Uploaded document cleanup failed for {task_id}: {cleanup_exc}")
        try:
            await asyncio.to_thread(store.update_task, task_id, "error", f"文件持久化失败: {exc}")
        except Exception:
            pass
        raise HTTPException(503, f"文件持久化失败，请检查 Supabase Storage 配置: {exc}") from exc
    task_queue(task_id)
    background.add_task(run_graph, task_id, temp.name)
    return TaskCreateResponse(task_id=task_id, status="pending")


@app.get("/stream/{task_id}")
async def stream(
    task_id: str,
    last_event_id: Optional[str] = Header(default=None, alias="Last-Event-ID"),
    after_id: Optional[int] = None,
) -> StreamingResponse:
    if store is None:
        raise HTTPException(503, "服务尚未就绪")
    try:
        current = await asyncio.to_thread(store.get_task, task_id)
    except KeyError:
        raise HTTPException(404, "任务不存在")
    except Exception as exc:
        print(f"Supabase task lookup failed for {task_id}: {exc}")
        raise HTTPException(503, "任务服务暂时不可用，请稍后重试") from exc
    queue = task_queue(task_id)
    try:
        replay_after_id = max(after_id if after_id is not None else int(last_event_id or "0"), 0)
    except ValueError:
        replay_after_id = 0

    async def iterator() -> AsyncIterator[str]:
        yield "event: connected\ndata: {}\n\n"
        seen_ids: set[int] = set()
        terminal = False
        replay_count = 0
        replay_after = replay_after_id
        if store is not None:
            while not terminal:
                try:
                    replay_page = await asyncio.to_thread(store.list_events, task_id, replay_after, 500)
                except Exception as exc:
                    print(f"Supabase event replay failed for {task_id}: {exc}")
                    replay_page = []
                if not replay_page:
                    break
                replay_count += len(replay_page)
                for row in replay_page:
                    try:
                        event_id = int(row["id"])
                        seen_ids.add(event_id)
                        replay_after = max(replay_after, event_id)
                        event_name = str(row["event"])
                        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
                        yield format_sse(event_name, payload, event_id)
                        terminal = terminal or (event_name == "status" and payload.get("status") in {"summary_finish", "error"})
                    except (KeyError, TypeError, ValueError):
                        continue
                if len(replay_page) < 500:
                    break
        if terminal:
            return
        if current["status"] in {"summary_finish", "error"} and replay_count == 0:
            payload = {"status": current["status"]}
            if current.get("error_message"):
                payload["error"] = current["error_message"]
            yield format_sse("status", payload)
            return
        while True:
            try:
                message = await asyncio.wait_for(queue.get(), timeout=15)
            except asyncio.TimeoutError:
                # SSE comments are ignored by EventSource but keep Render and
                # other reverse proxies from closing a long model call.
                yield ": keep-alive\n\n"
                continue
            event_id = message.get("id")
            if isinstance(event_id, int) and event_id in seen_ids:
                continue
            if isinstance(event_id, int):
                seen_ids.add(event_id)
            event_name = str(message.get("event", "message"))
            payload = message.get("payload") if isinstance(message.get("payload"), dict) else {}
            yield format_sse(event_name, payload, event_id if isinstance(event_id, int) else None)
            if event_name == "status" and payload.get("status") in {"summary_finish", "error"}:
                break

    return StreamingResponse(
        iterator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/tasks/{task_id}", response_model=TaskResult)
async def task_result(task_id: str) -> TaskResult:
    if store is None:
        raise HTTPException(503, "后端未配置 Supabase")
    try:
        row = await asyncio.to_thread(store.get_task, task_id)
    except KeyError:
        raise HTTPException(404, "任务不存在")
    except Exception as exc:
        print(f"Supabase result lookup failed for {task_id}: {exc}")
        raise HTTPException(503, "任务结果服务暂时不可用，请稍后重试") from exc
    return TaskResult(task_id=task_id, status=row["status"], report_markdown=row.get("report_markdown"), report_json=row.get("report_json"), error=row.get("error_message"))


@app.get("/tasks", response_model=List[TaskListItem])
async def list_tasks(limit: int = 50) -> List[TaskListItem]:
    if store is None:
        raise HTTPException(503, "后端未配置 Supabase")
    try:
        rows = await asyncio.to_thread(store.list_tasks, min(max(limit, 1), 100))
    except Exception as exc:
        print(f"Supabase task list failed: {exc}")
        raise HTTPException(503, "历史任务服务暂时不可用，请稍后重试") from exc
    return [TaskListItem.model_validate(item) for item in rows]


@app.get("/healthz")
async def healthz() -> Dict[str, str]:
    # Re-check periodically so running the schema SQL after boot clears the
    # degraded event-replay state without requiring a service restart.
    if store is not None and time.monotonic() - event_replay_checked_at > 30:
        await probe_event_replay()
    issues = configuration_issues()
    event_replay = "not_configured"
    storage = "not_configured"
    if store is not None:
        event_replay = event_replay_state or "checking"
        storage = storage_state or "checking"
    degraded = bool(issues) or event_replay not in {"available", "not_configured"} or storage not in {"available", "not_configured"}
    return {
        "status": "ok" if not degraded else "degraded",
        "supabase": "configured" if store is not None else "not_configured",
        "storage_bucket": store.storage_bucket if store is not None else "not_configured",
        "storage": storage,
        "event_replay": event_replay,
        "configuration": "ready" if not issues else "missing:" + ",".join(issues),
    }


@app.get("/readyz")
async def readyz() -> Dict[str, str]:
    """Strict deployment readiness check for Render and other orchestrators.

    ``/healthz`` intentionally stays a non-blocking diagnostic endpoint so a
    local UI can boot before credentials are entered.  A production router
    must use this endpoint instead: accepting traffic before Supabase event
    replay is available would make SSE updates non-durable after a restart.
    """
    if store is not None and time.monotonic() - event_replay_checked_at > 30:
        await probe_event_replay()
    issues = configuration_issues()
    event_replay = event_replay_state if store is not None else "not_configured"
    storage = storage_state if store is not None else "not_configured"
    payload = {
        "status": "ready",
        "supabase": "configured" if store is not None else "not_configured",
        "storage_bucket": store.storage_bucket if store is not None else "not_configured",
        "storage": storage or "checking",
        "event_replay": event_replay or "checking",
        "configuration": "ready" if not issues else "missing:" + ",".join(issues),
    }
    if store is None or issues or event_replay != "available" or storage != "available":
        payload["status"] = "not_ready"
        raise HTTPException(status_code=503, detail=payload)
    return payload
