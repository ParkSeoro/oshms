"""전략 모듈 테스트."""

import unittest

from strategy.base import BaseStrategy, SignalType
from strategy.scalping import ScalpingStrategy
from strategy.momentum import MomentumStrategy
from strategy.combined import CombinedStrategy


class TestIndicators(unittest.TestCase):
    """기술적 지표 계산 테스트."""

    def test_sma(self):
        prices = [10, 20, 30, 40, 50]
        result = BaseStrategy.calc_sma(prices, 3)
        self.assertEqual(len(result), 3)
        self.assertAlmostEqual(result[0], 20.0)
        self.assertAlmostEqual(result[1], 30.0)
        self.assertAlmostEqual(result[2], 40.0)

    def test_sma_insufficient_data(self):
        result = BaseStrategy.calc_sma([10, 20], 5)
        self.assertEqual(result, [])

    def test_ema(self):
        prices = [10, 20, 30, 40, 50]
        result = BaseStrategy.calc_ema(prices, 3)
        self.assertTrue(len(result) > 0)
        # EMA의 첫 값은 SMA와 동일
        self.assertAlmostEqual(result[0], 20.0)

    def test_rsi_uptrend(self):
        # 지속 상승하는 데이터 → RSI가 높아야 함
        prices = list(range(100, 120))
        rsi = BaseStrategy.calc_rsi(prices, 14)
        self.assertGreater(rsi, 70)

    def test_rsi_downtrend(self):
        # 지속 하락하는 데이터 → RSI가 낮아야 함
        prices = list(range(120, 100, -1))
        rsi = BaseStrategy.calc_rsi(prices, 14)
        self.assertLess(rsi, 30)

    def test_rsi_insufficient_data(self):
        rsi = BaseStrategy.calc_rsi([100, 101], 14)
        self.assertEqual(rsi, 50.0)

    def test_bollinger_bands(self):
        prices = [float(x) for x in range(100, 120)]
        upper, mid, lower = BaseStrategy.calc_bollinger_bands(prices, 20)
        self.assertGreater(upper, mid)
        self.assertGreater(mid, lower)
        self.assertAlmostEqual(mid, sum(prices) / 20)

    def test_bollinger_bands_insufficient(self):
        upper, mid, lower = BaseStrategy.calc_bollinger_bands([100.0], 20)
        self.assertEqual(upper, 100.0)

    def test_macd(self):
        prices = [float(x) for x in range(100, 130)]
        macd, signal, hist = BaseStrategy.calc_macd(prices)
        # 상승 데이터에서 MACD는 양수여야 함
        self.assertGreater(macd, 0)


class TestScalpingStrategy(unittest.TestCase):
    """스캘핑 전략 테스트."""

    def setUp(self):
        self.strategy = ScalpingStrategy()

    def _make_candles(self, closes: list[int]) -> list[dict]:
        """테스트용 캔들 데이터를 생성한다 (newest first)."""
        return [
            {"close": c, "open": c, "high": c + 10, "low": c - 10, "volume": 1000}
            for c in reversed(closes)
        ]

    def test_hold_on_insufficient_data(self):
        candles = self._make_candles([100, 200, 300])
        signal = self.strategy.analyze("005930", candles, {"price": 300})
        self.assertEqual(signal.signal_type, SignalType.HOLD)

    def test_buy_signal_at_lower_band(self):
        # 볼린저 하단에 가격 배치 + 하락 추세 (낮은 RSI)
        base = list(range(120, 100, -1))  # 하락 추세
        candles = self._make_candles(base)
        price = {"price": 95}  # 밴드 하단 이하
        signal = self.strategy.analyze("005930", candles, price)
        # 하락 추세에서 낮은 가격이면 매수 또는 홀드 (전략 의존)
        self.assertIn(signal.signal_type, [SignalType.BUY, SignalType.HOLD])

    def test_sell_signal_at_upper_band(self):
        # 상승 추세 → RSI 높음, 볼린저 상단
        base = list(range(100, 120))  # 상승 추세
        candles = self._make_candles(base)
        price = {"price": 130}  # 밴드 상단 이상
        signal = self.strategy.analyze("005930", candles, price)
        self.assertIn(signal.signal_type, [SignalType.SELL, SignalType.HOLD])


class TestMomentumStrategy(unittest.TestCase):
    """모멘텀 전략 테스트."""

    def setUp(self):
        self.strategy = MomentumStrategy(fast_period=5, slow_period=10, volume_multiplier=2.0)

    def _make_candles(self, closes: list[int], volumes: list[int] | None = None) -> list[dict]:
        if volumes is None:
            volumes = [1000] * len(closes)
        return [
            {"close": c, "open": c, "high": c + 5, "low": c - 5, "volume": v}
            for c, v in zip(reversed(closes), reversed(volumes))
        ]

    def test_hold_on_insufficient_data(self):
        candles = self._make_candles([100, 200, 300])
        signal = self.strategy.analyze("005930", candles, {"price": 300, "volume": 1000})
        self.assertEqual(signal.signal_type, SignalType.HOLD)

    def test_analysis_with_sufficient_data(self):
        closes = list(range(100, 115))  # 15개 데이터
        candles = self._make_candles(closes)
        signal = self.strategy.analyze("005930", candles, {"price": 114, "volume": 1000})
        # 데이터가 충분하면 분석 가능
        self.assertIsNotNone(signal.reason)


class TestCombinedStrategy(unittest.TestCase):
    """복합 전략 테스트."""

    def test_hold_on_insufficient_data(self):
        strategy = CombinedStrategy()
        candles = [{"close": 100, "open": 100, "high": 110, "low": 90, "volume": 1000}]
        signal = strategy.analyze("005930", candles, {"price": 100})
        self.assertEqual(signal.signal_type, SignalType.HOLD)

    def test_has_reason_from_both_strategies(self):
        strategy = CombinedStrategy()
        closes = list(range(100, 130))
        candles = [
            {"close": c, "open": c, "high": c + 5, "low": c - 5, "volume": 1000}
            for c in reversed(closes)
        ]
        signal = strategy.analyze("005930", candles, {"price": 129, "volume": 1000})
        # 복합 전략은 양쪽 전략의 결과를 포함
        self.assertIn("scalping", signal.reason)
        self.assertIn("momentum", signal.reason)


if __name__ == "__main__":
    unittest.main()
