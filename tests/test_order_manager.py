"""주문 관리자 테스트."""

import unittest
from unittest.mock import MagicMock

from config.settings import Settings
from trading.order_manager import OrderManager, Position, TradeRecord


class TestPosition(unittest.TestCase):
    """Position 테스트."""

    def test_profit_rate(self):
        pos = Position("005930", "삼성전자", 10, 70000, "09:30:00", "테스트",
                        current_price=72000)
        self.assertAlmostEqual(pos.profit_rate, 2.857, places=2)

    def test_profit_loss(self):
        pos = Position("005930", "삼성전자", 10, 70000, "09:30:00", "테스트",
                        current_price=72000)
        self.assertEqual(pos.profit_loss, 20000)

    def test_negative_profit(self):
        pos = Position("005930", "삼성전자", 10, 70000, "09:30:00", "테스트",
                        current_price=68000)
        self.assertLess(pos.profit_rate, 0)
        self.assertEqual(pos.profit_loss, -20000)

    def test_zero_avg_price(self):
        pos = Position("005930", "삼성전자", 10, 0, "09:30:00", "테스트", current_price=100)
        self.assertEqual(pos.profit_rate, 0.0)


class TestOrderManager(unittest.TestCase):
    """OrderManager 테스트."""

    def setUp(self):
        self.settings = Settings(
            app_key="test",
            app_secret="test",
            account_no="12345678-01",
            max_buy_amount=500_000,
            max_hold_count=3,
            stop_loss_pct=-2.0,
            take_profit_pct=3.0,
        )
        self.api = MagicMock()
        self.manager = OrderManager(self.api, self.settings)
        self.manager.trade_history = []

    def test_can_buy_empty(self):
        self.assertTrue(self.manager.can_buy())

    def test_can_buy_full(self):
        for i in range(3):
            self.manager.positions[f"00{i}"] = Position(
                f"00{i}", f"종목{i}", 10, 10000, "09:30", "테스트"
            )
        self.assertFalse(self.manager.can_buy())

    def test_calc_buy_quantity_default(self):
        # v4.9: strength 무시, size_mult=1.0 기본 → max_buy_amount//price
        qty = self.manager.calc_buy_quantity(50000)
        self.assertEqual(qty, 10)  # 500,000 / 50,000

    def test_calc_buy_quantity_half_size(self):
        """v4.9: size_mult=0.5 (HALF tier)는 반 포지션만."""
        qty = self.manager.calc_buy_quantity(50000, size_mult=0.5)
        self.assertEqual(qty, 5)  # 250,000 / 50,000

    def test_calc_buy_quantity_max_size(self):
        """v4.9: size_mult=1.2 (MAX tier)는 확장 포지션."""
        qty = self.manager.calc_buy_quantity(50000, size_mult=1.2)
        self.assertEqual(qty, 12)  # 600,000 / 50,000

    def test_calc_buy_quantity_honors_small_size(self):
        """v4.9 핵심: 낮은 확신도는 작은 포지션으로 반영돼야 한다.

        이전 버전엔 min_invest 하한 때문에 size_mult=0.35여도 강제로 끌어올려
        실제로는 수익률 변동성의 주범이었다.
        """
        qty = self.manager.calc_buy_quantity(50000, size_mult=0.35)
        # 500,000 * 0.35 = 175,000 → 175,000 // 50,000 = 3
        self.assertEqual(qty, 3)

    def test_calc_buy_quantity_zero_price(self):
        qty = self.manager.calc_buy_quantity(0)
        self.assertEqual(qty, 0)

    def test_calc_buy_quantity_min_fallback_high_price(self):
        """가격이 max_buy_amount에 육박해도 size_mult=1.0이면 최소 1주."""
        # 400,000원 짜리 주식, 50만원 예산, size_mult=1.0 → 1주
        qty = self.manager.calc_buy_quantity(400000, size_mult=1.0)
        self.assertEqual(qty, 1)

    def test_check_stop_loss(self):
        self.manager.positions["005930"] = Position(
            "005930", "삼성전자", 10, 70000, "09:30", "테스트",
            current_price=68000  # -2.86%
        )
        targets = self.manager.check_stop_loss()
        self.assertIn("005930", targets)

    def test_check_stop_loss_no_target(self):
        self.manager.positions["005930"] = Position(
            "005930", "삼성전자", 10, 70000, "09:30", "테스트",
            current_price=69500  # -0.71%
        )
        targets = self.manager.check_stop_loss()
        self.assertEqual(len(targets), 0)

    def test_check_take_profit(self):
        # v2.7에서 익절 기준이 20%로 상향됨 (목표가 매도가 주 메커니즘)
        self.manager.positions["005930"] = Position(
            "005930", "삼성전자", 10, 70000, "09:30", "테스트",
            current_price=85000  # +21.4% → 20% 이상이므로 익절 대상
        )
        targets = self.manager.check_take_profit()
        self.assertIn("005930", targets)

    def test_check_take_profit_below_threshold(self):
        # 3.57% 수익은 익절 대상이 아님 (20% 미만)
        self.manager.positions["005930"] = Position(
            "005930", "삼성전자", 10, 70000, "09:30", "테스트",
            current_price=72500  # +3.57%
        )
        targets = self.manager.check_take_profit()
        self.assertNotIn("005930", targets)

    def test_check_trailing_stop(self):
        self.manager.positions["005930"] = Position(
            "005930", "삼성전자", 10, 70000, "09:30", "테스트",
            current_price=71500,  # 수익 중
            highest_price=73000,  # 최고가 73000에서 71500으로 -2.05% 하락
        )
        targets = self.manager.check_trailing_stop(trail_pct=1.5)
        self.assertIn("005930", targets)

    def test_execute_buy_success(self):
        self.api.buy_market_order.return_value = {"success": True, "order_no": "12345"}
        result = self.manager.execute_buy("005930", "삼성전자", 70000, "테스트매수")
        self.assertTrue(result)
        self.assertIn("005930", self.manager.positions)
        self.assertEqual(len(self.manager.trade_history), 1)

    def test_execute_buy_already_held(self):
        self.manager.positions["005930"] = Position(
            "005930", "삼성전자", 10, 70000, "09:30", "이전매수"
        )
        result = self.manager.execute_buy("005930", "삼성전자", 70000, "중복매수")
        self.assertFalse(result)

    def test_execute_sell_success(self):
        self.manager.positions["005930"] = Position(
            "005930", "삼성전자", 10, 70000, "09:30", "테스트",
            current_price=72000,
        )
        self.api.sell_market_order.return_value = {"success": True, "order_no": "12346"}
        result = self.manager.execute_sell("005930", "익절")
        self.assertTrue(result)
        self.assertNotIn("005930", self.manager.positions)

    def test_execute_sell_not_held(self):
        result = self.manager.execute_sell("005930", "테스트")
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
