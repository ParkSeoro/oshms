"""전문가 전략 테스트."""

import unittest

from strategy.expert import ExpertStrategy, ExpertAnalysis
from strategy.base import SignalType
from strategy.market_context import MarketContext


class TestExpertStrategy(unittest.TestCase):

    def setUp(self):
        self.strategy = ExpertStrategy()

    def _make_candles(self, closes, volumes=None):
        """과거순(oldest first) 캔들 생성."""
        if volumes is None:
            volumes = [1000] * len(closes)
        return [
            {"open": c - 3, "high": c + 10, "low": c - 10, "close": c, "volume": v}
            for c, v in zip(closes, volumes)
        ]

    def test_hold_on_insufficient_data(self):
        candles = self._make_candles([100, 200])
        signal = self.strategy.analyze("005930", candles, {"price": 200})
        self.assertEqual(signal.signal_type, SignalType.HOLD)

    def test_full_analysis_returns_expert_analysis(self):
        closes = list(range(100, 165))
        candles = self._make_candles(closes)
        result = self.strategy.full_analysis(
            "005930", "삼성전자", candles, {"price": 164, "stock_name": "삼성전자"}
        )
        self.assertIsInstance(result, ExpertAnalysis)
        self.assertEqual(result.stock_code, "005930")
        self.assertIn(result.decision, ["STRONG_BUY", "BUY", "HOLD", "SELL", "STRONG_SELL"])
        self.assertGreater(len(result.reasons), 0)

    def test_summary_output(self):
        closes = list(range(100, 165))
        candles = self._make_candles(closes)
        result = self.strategy.full_analysis(
            "005930", "삼성전자", candles, {"price": 164, "stock_name": "삼성전자"}
        )
        summary = result.summary()
        self.assertIn("삼성전자", summary)
        self.assertIn("종합점수", summary)

    def test_market_context_affects_decision(self):
        # 강한 하락장 컨텍스트 설정
        ctx = MarketContext(
            kospi_change=-3.0,
            kosdaq_change=-4.0,
            regime="trending_down",
            regime_confidence=0.8,
            market_score=-0.5,
        )
        self.strategy.set_market_context(ctx)

        closes = list(range(100, 165))
        candles = self._make_candles(closes)
        result = self.strategy.full_analysis(
            "005930", "삼성전자", candles, {"price": 164, "stock_name": "삼성전자"}
        )
        # 하락장에서 매수 기준 상향됨
        self.assertIsNotNone(result.decision)

    def test_downtrend_analysis(self):
        closes = list(range(165, 100, -1))
        candles = self._make_candles(closes)
        result = self.strategy.full_analysis(
            "005930", "삼성전자", candles, {"price": 101, "stock_name": "삼성전자"}
        )
        # 하락추세에서는 기술분석 점수가 음수
        self.assertLess(result.technical_score, 0.3)

    def test_confidence_calculation(self):
        closes = list(range(100, 165))
        candles = self._make_candles(closes)
        result = self.strategy.full_analysis(
            "005930", "삼성전자", candles, {"price": 164, "stock_name": "삼성전자"}
        )
        self.assertGreaterEqual(result.confidence, 0)
        self.assertLessEqual(result.confidence, 1.0)

    def test_ensure_ascending(self):
        # newest first (내림차순 time) → 자동 역순 정렬
        candles = [
            {"time": "150000", "open": 110, "high": 115, "low": 105, "close": 112, "volume": 100},
            {"time": "140000", "open": 105, "high": 112, "low": 103, "close": 110, "volume": 100},
            {"time": "130000", "open": 100, "high": 108, "low": 98, "close": 105, "volume": 100},
        ]
        result = self.strategy._ensure_ascending(candles)
        self.assertEqual(result[0]["time"], "130000")
        self.assertEqual(result[-1]["time"], "150000")

    def test_ensure_ascending_already_sorted(self):
        candles = [
            {"time": "130000", "open": 100, "high": 108, "low": 98, "close": 105, "volume": 100},
            {"time": "140000", "open": 105, "high": 112, "low": 103, "close": 110, "volume": 100},
        ]
        result = self.strategy._ensure_ascending(candles)
        self.assertEqual(result[0]["time"], "130000")


class TestExpertAnalysis(unittest.TestCase):

    def test_default_values(self):
        a = ExpertAnalysis(stock_code="005930", stock_name="삼성전자")
        self.assertEqual(a.decision, "")
        self.assertIsInstance(a.reasons, list)
        self.assertEqual(a.total_score, 0)

    def test_summary_format(self):
        a = ExpertAnalysis(
            stock_code="005930",
            stock_name="삼성전자",
            price=70000,
            total_score=0.45,
            confidence=0.7,
            decision="BUY",
            reasons=["RSI 과매도(25)", "골든크로스"],
        )
        s = a.summary()
        self.assertIn("삼성전자", s)
        self.assertIn("BUY", s)
        self.assertIn("RSI", s)


if __name__ == "__main__":
    unittest.main()
