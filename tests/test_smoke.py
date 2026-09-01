import asyncio
import os
import unittest

from app import main as backend
from app.main import JsonReportParser, ReportAgents, _env_seconds, format_sse, read_pdf_upload


class FakeUpload:
    def __init__(self, *parts: bytes):
        self.parts = iter(parts)

    async def read(self, _size: int) -> bytes:
        return next(self.parts, b"")


class DeploymentSmokeTests(unittest.TestCase):
    def test_pdf_upload_is_bounded_and_validated(self):
        data = asyncio.run(read_pdf_upload(FakeUpload(b"%PDF-1.7", b"body"), 32))
        self.assertTrue(data.startswith(b"%PDF-"))
        with self.assertRaises(Exception) as invalid:
            asyncio.run(read_pdf_upload(FakeUpload(b"not-a-pdf"), 32))
        self.assertEqual(invalid.exception.status_code, 400)

    def test_strict_parser_rejects_missing_fields(self):
        parser = JsonReportParser(required_fields=["metrics"])
        self.assertEqual(parser.parse('{"metrics": {}}'), {"metrics": {}})
        with self.assertRaises(ValueError):
            parser.parse("not-json")

    def test_sse_format_contains_id_and_json_payload(self):
        message = format_sse("agent", {"agent": "document_parser", "output": "完成"}, 17)
        self.assertIn("id: 17", message)
        self.assertIn("event: agent", message)
        self.assertIn('"output": "完成"', message)

    def test_metric_normalization_prefers_base_value(self):
        report = ReportAgents._normalize_chinese_fields(
            {"metrics": {"revenue": {"value": "13.86%", "数值": 751766, "同比变化": "13.86%"}}}
        )
        metric = report["metrics"]["营业收入"]
        self.assertEqual(metric["value"], 751766.0)

    def test_sse_replay_paginates_until_terminal_event(self):
        class ReplayStore:
            def get_task(self, _task_id):
                return {"status": "summary_finish"}

            def list_events(self, _task_id, after_id=0, limit=500):
                if after_id == 0:
                    return [{"id": index, "event": "agent", "payload": {"output": "片段"}} for index in range(1, limit + 1)]
                if after_id == 500:
                    return [{"id": 501, "event": "status", "payload": {"status": "summary_finish"}}]
                return []

        original_store = backend.store
        original_events = backend.events
        try:
            backend.store = ReplayStore()
            backend.events = {}

            async def collect():
                response = await backend.stream("task", after_id=0)
                return [item async for item in response.body_iterator]

            messages = asyncio.run(collect())
            self.assertEqual(len(messages), 502)  # connected + 500 + terminal event
            self.assertIn("summary_finish", messages[-1])
        finally:
            backend.store = original_store
            backend.events = original_events

    def test_timeout_env_is_bounded_and_defensive(self):
        original = os.environ.get("TEST_TIMEOUT_SECONDS")
        try:
            os.environ["TEST_TIMEOUT_SECONDS"] = "bad"
            self.assertEqual(_env_seconds("TEST_TIMEOUT_SECONDS", 12), 12)
            os.environ["TEST_TIMEOUT_SECONDS"] = "9999"
            self.assertEqual(_env_seconds("TEST_TIMEOUT_SECONDS", 12), 600)
            os.environ["TEST_TIMEOUT_SECONDS"] = "0"
            self.assertEqual(_env_seconds("TEST_TIMEOUT_SECONDS", 12), 1)
        finally:
            if original is None:
                os.environ.pop("TEST_TIMEOUT_SECONDS", None)
            else:
                os.environ["TEST_TIMEOUT_SECONDS"] = original

    def test_readiness_requires_event_replay_and_configuration(self):
        original_store = backend.store
        original_state = backend.event_replay_state
        try:
            backend.store = None
            backend.event_replay_state = "not_configured"
            with self.assertRaises(Exception) as not_ready:
                asyncio.run(backend.readyz())
            self.assertEqual(not_ready.exception.status_code, 503)
        finally:
            backend.store = original_store
            backend.event_replay_state = original_state

    def test_supabase_read_errors_return_retryable_503(self):
        class FailingStore:
            def list_tasks(self, _limit):
                raise ConnectionError("temporary database disconnect")

        original_store = backend.store
        try:
            backend.store = FailingStore()
            with self.assertRaises(Exception) as unavailable:
                asyncio.run(backend.list_tasks())
            self.assertEqual(unavailable.exception.status_code, 503)
        finally:
            backend.store = original_store

    def test_summary_streams_final_output_before_terminal_status(self):
        class FakeStore:
            def __init__(self):
                self.calls = []

            def save_report(self, task_id, markdown, report_json):
                self.calls.append(("save", task_id))

            def update_task(self, task_id, status):
                self.calls.append(("status", task_id, status))

        async def collect():
            agents = object.__new__(ReportAgents)
            agents.store = FakeStore()
            agents.report_parser = object()
            events = []
            prompts = []

            async def emit(task_id, event, payload):
                events.append((event, payload))

            async def invoke_json(_prompt, _parser):
                prompts.append(_prompt)
                return {
                    "company": "测试公司",
                    "period": "2025年度",
                    "metrics": {},
                    "risks": [],
                    "summary": "测试结论",
                    "industry_comparison": {},
                    "sources": [],
                }

            agents.emit = emit
            agents._invoke_json = invoke_json
            async def retrieve(_state, _query):
                return [{"page": 12, "chunk_id": "p12-c1", "content": "主营业务与竞争环境证据"}]

            agents._retrieve = retrieve
            await agents.summary({"task_id": "task", "finance": {}, "risks": {}})
            return events, prompts

        events, prompts = asyncio.run(collect())
        self.assertIn("行业与业务原文证据", prompts[0])
        self.assertIn("page=12", prompts[0])
        terminal_index = next(index for index, item in enumerate(events) if item == ("status", {"status": "summary_finish"}))
        final_output_index = next(index for index, item in enumerate(events) if item[0] == "agent" and item[1].get("agent") == "industry_summary")
        self.assertLess(final_output_index, terminal_index)


if __name__ == "__main__":
    unittest.main()
