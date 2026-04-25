"""고급 기술적 분석기 테스트."""

import unittest

from strategy.technical import TechnicalAnalyzer, TechnicalSnapshot


class TestTechnicalAnalyzer(unittest.TestCase):

    def setUp(self):
        self.analyzer = TechnicalAnalyzer()

    def _make_candles(self, closes, base_volume=1000):
        """테스트용 캔들 생성 (oldest first)."""
        candles = []
        for i, c in enumerate(closes):
            candles.append({
                "open": c - 5,
                "high": c + 10,
                "low": c - 10,
                "close": c,
                "volume": base_volume + i * 10,
            })
        return candles

    def test_insufficient_data(self):
        snap = self.analyzer.analyze([{"close": 100, "high": 110, "low": 90, "open": 95, "volume": 100}])
        self.assertIsInstance(snap, TechnicalSnapshot)

    def test_sma_calculation(self):
        candles = self._make_candles(list(range(100, 130)))
        snap = self.analyzer.analyze(candles)
        self.assertGreater(snap.sma_5, 0)
        self.assertGreater(snap.sma_20, 0)

    def test_rsi_uptrend(self):
        candles = self._make_candles(list(range(100, 130)))
        snap = self.analyzer.analyze(candles)
        self.assertGreater(snap.rsi, 50)

    def test_rsi_downtrend(self):
        candles = self._make_candles(list(range(130, 100, -1)))
        snap = self.analyzer.analyze(candles)
        self.assertLess(snap.rsi, 50)

    def test_bollinger_bands(self):
        candles = self._make_candles(list(range(100, 125)))
        snap = self.analyzer.analyze(candles)
        self.assertGreater(snap.bb_upper, snap.bb_middle)
        self.assertGreater(snap.bb_middle, snap.bb_lower)
        self.assertGreater(snap.bb_width, 0)

    def test_stochastic(self):
        candles = self._make_candles(list(range(100, 125)))
        snap = self.analyzer.analyze(candles)
        self.assertGreaterEqual(snap.stoch_k, 0)
        self.assertLessEqual(snap.stoch_k, 100)

    def test_atr(self):
        candles = self._make_candles(list(range(100, 125)))
        snap = self.analyzer.analyze(candles)
        self.assertGreater(snap.atr, 0)
        self.assertGreater(snap.atr_pct, 0)

    def test_vwap(self):
        candles = self._make_candles(list(range(100, 125)))
        snap = self.analyzer.analyze(candles)
        self.assertGreater(snap.vwap, 0)

    def test_ichimoku(self):
        candles = self._make_candles(list(range(100, 160)))
        snap = self.analyzer.analyze(candles)
        self.assertGreater(snap.ichimoku_tenkan, 0)
        self.assertGreater(snap.ichimoku_kijun, 0)
        self.assertIn(snap.ichimoku_signal, [
            "strong_buy", "buy", "sell", "strong_sell", "neutral"
        ])

    def test_support_resistance(self):
        # 파동 모양: 오르고 내리는 데이터
        prices = []
        for i in range(10):
            prices.extend([100 + i * 2, 110 + i * 2, 105 + i * 2])
        candles = self._make_candles(prices)
        snap = self.analyzer.analyze(candles)
        # 지지/저항이 찾아질 수 있음
        self.assertIsInstance(snap.support_levels, list)
        self.assertIsInstance(snap.resistance_levels, list)

    def test_trend_score_uptrend(self):
        candles = self._make_candles(list(range(100, 165)))
        snap = self.analyzer.analyze(candles)
        self.assertGreater(snap.trend_score, 0)

    def test_trend_score_downtrend(self):
        candles = self._make_candles(list(range(165, 100, -1)))
        snap = self.analyzer.analyze(candles)
        self.assertLess(snap.trend_score, 0)

    def test_volume_score(self):
        candles = self._make_candles(list(range(100, 125)))
        snap = self.analyzer.analyze(candles)
        # volume_score should be between -1 and 1
        self.assertGreaterEqual(snap.volume_score, -1.0)
        self.assertLessEqual(snap.volume_score, 1.0)

    def test_obv_trend_up(self):
        candles = self._make_candles(list(range(100, 125)), base_volume=5000)
        snap = self.analyzer.analyze(candles)
        self.assertEqual(snap.obv_trend, "up")


class TestTechnicalSnapshot(unittest.TestCase):

    def test_default_values(self):
        snap = TechnicalSnapshot()
        self.assertEqual(snap.rsi, 50)
        self.assertEqual(snap.trend_score, 0)
        self.assertIsInstance(snap.support_levels, list)
        self.assertIsInstance(snap.resistance_levels, list)


if __name__ == "__main__":
    unittest.main()
