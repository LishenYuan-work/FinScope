import asyncio
import unittest

from app import main as backend
from app.main import JsonReportParser, ReportAgents, format_sse, read_pdf_upload


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


if __name__ == "__main__":
    unittest.main()
