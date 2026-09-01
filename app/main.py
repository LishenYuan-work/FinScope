
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
from langchain_core.tools import BaseToolfrom langchain_openai import OpenAIEmbeddings    
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
    table_count: int@staticmethod    
def _bucket_names(buckets: Any) -> set[str]:        
return {# Supabase free instances enforce a relatively short statement timeout.llm_configs = {imeout_secondsrisk["level"] = {"high": "高", "medium": "中", "low": "低", "critical": "高"}.get(level, risk.get("level", "中"))ents([c["text"] for c in batch]),f"已召回 {len(industry_evidence)} 个行业证据片段，正在汇总财务指标与风险事项…","); continuing in degraded mode")le.read(1024 * 1024)ne)HTTPException(404, "任务不存在").get("report_markdown"), report_json=row.get("report_json"), error=row.get("error_message"))


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
