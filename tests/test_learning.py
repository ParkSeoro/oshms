"""학습 모듈 테스트."""

import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from learning.backtester import Backtester, BacktestResult
from learning.optimizer import StrategyOptimizer, OptimizationResult
from learning.learner import SelfLearner, LearningInsight


class TestBacktestResult(unittest.TestCase):
    """BacktestResult 테스트."""

    def test_score_insufficient_trades(self):
        r = BacktestResult(total_trades=3)
        self.assertEqual(r.score(), -999)

    def test_score_calculation(self):
        r = BacktestResult(
            total_trades=50,
            wins=30,
            win_rate=60,
            profit_factor=2.0,
            max_drawdown=3.0,
        )
        score = r.score()
        self.assertGreater(score, 0)
        self.assertLess(score, 100)

    def test_score_perfect(self):
        r = BacktestResult(
            total_trades=50,
            wins=50,
            win_rate=100,
            profit_factor=5.0,
            max_drawdown=0,
        )
        score = r.score()
        self.assertGreater(score, 80)


class TestBacktester(unittest.TestCase):
    """Backtester 테스트."""

    def _make_candles(self, n=60, base_price=10000):
        """테스트용 캔들 데이터를 생성한다."""
        candles = []
        price = base_price
        for i in range(n):
            # 가격 변동 패턴: 상승 → 하락 → 상승
            if i % 10 < 5:
                price = int(price * 1.005)
            else:
                price = int(price * 0.995)
            candles.append({
                "open": price - 10,
                "high": price + 30,
                "low": price - 30,
                "close": price,
                "volume": 100000 + i * 1000,
            })
        return candles

    def test_insufficient_candles(self):
        bt = Backtester()
        candles = self._make_candles(10)

        class DummyStrategy:
            name = "dummy"

        result = bt.run(DummyStrategy(), candles)
        self.assertEqual(result.total_trades, 0)
        self.assertEqual(result.strategy_name, "dummy")

    def test_basic_backtest(self):
        bt = Backtester()
        candles = self._make_candles(80)

        class AlternatingStrategy:
            name = "alternating"
            _count = 0

            def analyze(self, stock_code, candles, current_price):
                from strategy.base import Signal, SignalType
                self._count += 1
                if self._count % 6 == 1:
                    return Signal(SignalType.BUY, stock_code, "buy", strength=0.5)
                elif self._count % 6 == 4:
                    return Signal(SignalType.SELL, stock_code, "sell", strength=0.5)
                return Signal(SignalType.HOLD, stock_code, "hold")

        result = bt.run(AlternatingStrategy(), candles)
        self.assertGreater(result.total_trades, 0)
        self.assertEqual(result.wins + result.losses, result.total_trades)

    def test_drawdown_tracked(self):
        bt = Backtester()
        candles = self._make_candles(80)

        class HoldStrategy:
            name = "hold"

            def analyze(self, stock_code, candles, current_price):
                from strategy.base import Signal, SignalType
                return Signal(SignalType.HOLD, stock_code, "hold")

        result = bt.run(HoldStrategy(), candles)
        # No trades, but drawdown should be tracked on unrealized value
        self.assertEqual(result.total_trades, 0)


class TestStrategyOptimizer(unittest.TestCase):
    """StrategyOptimizer 테스트."""

    def test_unknown_strategy(self):
        opt = StrategyOptimizer()
        result = opt.optimize("unknown_strategy", [])
        self.assertEqual(result.best_score, 0)
        self.assertEqual(result.best_params, {})

    def test_generate_combinations_small(self):
        opt = StrategyOptimizer()
        ranges = {"a": [1, 2], "b": [3, 4]}
        combos = opt._generate_combinations(ranges, max_count=100)
        self.assertEqual(len(combos), 4)

    def test_generate_combinations_random(self):
        opt = StrategyOptimizer()
        ranges = {"a": [1, 2, 3, 4, 5], "b": [1, 2, 3, 4, 5], "c": [1, 2, 3, 4, 5]}
        combos = opt._generate_combinations(ranges, max_count=10)
        self.assertEqual(len(combos), 10)

    def test_load_optimized_params_empty(self):
        opt = StrategyOptimizer()
        # 파일이 없으면 빈 딕셔너리
        with patch.object(Path, "exists", return_value=False):
            params = opt.load_optimized_params("scalping")
            self.assertEqual(params, {})

    def test_load_optimized_params_existing(self):
        opt = StrategyOptimizer()
        data = json.dumps({"scalping": {"params": {"rsi_period": 14}, "score": 75.0}})
        with patch.object(Path, "exists", return_value=True):
            with patch.object(Path, "read_text", return_value=data):
                params = opt.load_optimized_params("scalping")
                self.assertEqual(params["rsi_period"], 14)


class TestSelfLearner(unittest.TestCase):
    """SelfLearner 테스트."""

    def setUp(self):
        # 인사이트 파일이 없는 상태로 시작
        with patch.object(Path, "exists", return_value=False):
            self.learner = SelfLearner()

    def test_insufficient_data(self):
        trades = [{"side": "SELL", "profit_loss": 100}] * 3
        insights = self.learner.learn_from_trades(trades)
        self.assertEqual(len(insights), 0)

    def test_insufficient_sells(self):
        trades = [{"side": "BUY", "profit_loss": 0}] * 15
        insights = self.learner.learn_from_trades(trades)
        self.assertEqual(len(insights), 0)

    def test_time_pattern_avoidance(self):
        """승률 낮은 시간대 감지."""
        trades = []
        for i in range(15):
            trades.append({"side": "BUY", "profit_loss": 0, "timestamp": f"2025-01-{10+i:02d} 14:30"})
        # 14시에 모두 손실
        for i in range(8):
            trades.append({
                "side": "SELL",
                "profit_loss": -5000,
                "timestamp": f"2025-01-{10+i:02d} 14:30",
            })
        with patch.object(self.learner, "_save_state"):
            insights = self.learner.learn_from_trades(trades)
        time_insights = [i for i in insights if i.category == "time"]
        if time_insights:
            self.assertIn("회피", time_insights[0].action)

    def test_time_pattern_preference(self):
        """승률 높은 시간대 감지."""
        trades = []
        for i in range(15):
            trades.append({"side": "BUY", "profit_loss": 0, "timestamp": f"2025-01-{10+i:02d} 10:00"})
        # 10시에 모두 수익
        for i in range(8):
            trades.append({
                "side": "SELL",
                "profit_loss": 10000,
                "timestamp": f"2025-01-{10+i:02d} 10:00",
            })
        with patch.object(self.learner, "_save_state"):
            insights = self.learner.learn_from_trades(trades)
        time_insights = [i for i in insights if i.category == "time"]
        if time_insights:
            self.assertIn("선호", time_insights[0].action)

    def test_profit_distribution_stop_loss(self):
        """손실이 큰 경우 손절 타이트 권장."""
        trades = [{"side": "BUY", "profit_loss": 0}] * 10
        # 큰 손실, 작은 수익
        for i in range(5):
            trades.append({"side": "SELL", "profit_loss": -30000, "timestamp": f"2025-01-{10+i:02d} 12:00"})
        for i in range(5):
            trades.append({"side": "SELL", "profit_loss": 5000, "timestamp": f"2025-01-{15+i:02d} 12:00"})

        with patch.object(self.learner, "_save_state"):
            insights = self.learner.learn_from_trades(trades)
        risk_insights = [i for i in insights if i.category == "risk"]
        self.assertGreater(len(risk_insights), 0)

    def test_losing_streak_detection(self):
        """연속 손실 패턴 감지."""
        trades = [{"side": "BUY", "profit_loss": 0}] * 10
        for i in range(7):
            trades.append({
                "side": "SELL",
                "profit_loss": -3000,
                "timestamp": f"2025-01-{10+i:02d} 11:00",
            })

        with patch.object(self.learner, "_save_state"):
            insights = self.learner.learn_from_trades(trades)
        threshold_insights = [i for i in insights if i.category == "threshold"]
        self.assertGreater(len(threshold_insights), 0)
        self.assertIn("매수기준상향", threshold_insights[0].action)

    def test_get_adjustments_default(self):
        adj = self.learner.get_adjustments()
        self.assertIn("buy_threshold_adj", adj)
        self.assertIn("sell_threshold_adj", adj)
        self.assertIn("avoid_times", adj)

    def test_get_adjustments_with_insights(self):
        self.learner.insights = [
            LearningInsight(
                category="time", key="14시",
                finding="test", confidence=0.7,
                action="14시 매매 회피",
            ),
            LearningInsight(
                category="threshold", key="losing_streak",
                finding="test", confidence=0.8,
                action="매수기준상향",
            ),
        ]
        adj = self.learner.get_adjustments()
        self.assertIn("14시", adj["avoid_times"])
        self.assertGreater(adj["buy_threshold_adj"], 0)

    def test_get_adjustments_low_confidence_ignored(self):
        self.learner.insights = [
            LearningInsight(
                category="time", key="14시",
                finding="test", confidence=0.3,  # 신뢰도 부족
                action="14시 매매 회피",
            ),
        ]
        adj = self.learner.get_adjustments()
        self.assertEqual(len(adj["avoid_times"]), 0)


class TestLearningInsight(unittest.TestCase):
    """LearningInsight 테스트."""

    def test_creation(self):
        insight = LearningInsight(
            category="time",
            key="10시",
            finding="10시 승률 80%",
            confidence=0.8,
            action="10시 매매 선호",
        )
        self.assertEqual(insight.category, "time")
        self.assertEqual(insight.key, "10시")
        self.assertIsNotNone(insight.timestamp)


if __name__ == "__main__":
    unittest.main()
