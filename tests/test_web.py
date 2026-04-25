"""웹 서버 테스트."""

import json
import unittest
from unittest.mock import patch, MagicMock

from web.server import app


class TestWebServer(unittest.TestCase):
    """Flask 웹 서버 API 테스트."""

    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_index_page(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"OSHMS", response.data)

    def test_api_status(self):
        response = self.client.get("/api/status")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertIn("trading", data)
        self.assertIn("mock", data)
        self.assertIn("api_configured", data)

    def test_api_logs_empty(self):
        from web.server import _state
        _state["logs"] = []
        response = self.client.get("/api/logs")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertEqual(data["logs"], [])
        self.assertEqual(data["total"], 0)

    def test_api_logs_with_since(self):
        from web.server import _state
        _state["logs"] = ["log1", "log2", "log3"]
        response = self.client.get("/api/logs?since=1")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertEqual(data["logs"], ["log2", "log3"])

    def test_api_get_settings(self):
        response = self.client.get("/api/settings")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertIn("is_mock", data)
        self.assertIn("max_buy_amount", data)

    def test_api_trade_stop(self):
        from web.server import _state
        _state["trading"] = False
        _state["trader"] = None
        response = self.client.post("/api/trade/stop")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertIn("message", data)

    def test_api_trade_start_already_running(self):
        from web.server import _state
        _state["trading"] = True
        response = self.client.post(
            "/api/trade/start",
            data=json.dumps({"strategy": "expert"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.data)
        self.assertIn("error", data)
        _state["trading"] = False


if __name__ == "__main__":
    unittest.main()
