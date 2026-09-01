import asyncio
import unittest

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


if __name__ == "__main__":
    unittest.main()
