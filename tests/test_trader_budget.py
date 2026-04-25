"""v4.9 일일/세션 진입 예산 카운터 테스트."""

import unittest
from unittest.mock import MagicMock

from config.settings import Settings
from trading.trader import AutoTrader


class TestDailyEntryBudget(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(
            app_key="test", app_secret="test", account_no="12345678-01",
            max_buy_amount=50_000, max_hold_count=2,
        )
        self.api = MagicMock()
        self.strategy = MagicMock()
        self.strategy.name = "test"
        # ExpertStrategy check should fail → market_analyzer 초기화 스킵
        self.trader = AutoTrader(self.api, self.settings, self.strategy)

    def test_budget_resets_on_new_day(self):
        self.trader._daily_entry_date = "2025-12-01"
        self.trader._daily_entries_total = 5
        self.trader._session_entries = {"morning": 3}
        self.trader._reset_daily_entries_if_new_day()
        self.assertNotEqual(self.trader._daily_entry_date, "2025-12-01")
        self.assertEqual(self.trader._daily_entries_total, 0)
        self.assertEqual(self.trader._session_entries, {})

    def test_session_budget_depletes(self):
        """세션 내 max_new_entries 도달 시 False."""
        self.trader._reset_daily_entries_if_new_day()
        for _ in range(4):
            self.trader._record_entry("morning")
        # morning max = 4, 이미 4건 들어갔으므로 더 이상 불가
        self.assertFalse(self.trader._session_has_budget("morning", 4))

    def test_global_budget_caps_even_with_session_space(self):
        """일일 상한(6)이 세션 상한보다 먼저 작동해야 한다."""
        self.trader._reset_daily_entries_if_new_day()
        self.trader._daily_trade_budget = 3  # 더 낮게 설정
        self.trader._record_entry("morning")
        self.trader._record_entry("morning")
        self.trader._record_entry("morning")
        # morning 세션은 4까지 허용이지만 일일 3건 소진
        self.assertFalse(self.trader._session_has_budget("morning", 4))

    def test_record_entry_increments_both(self):
        self.trader._reset_daily_entries_if_new_day()
        self.trader._record_entry("morning")
        self.assertEqual(self.trader._daily_entries_total, 1)
        self.assertEqual(self.trader._session_entries["morning"], 1)
        self.trader._record_entry("afternoon")
        self.assertEqual(self.trader._daily_entries_total, 2)
        self.assertEqual(self.trader._session_entries["afternoon"], 1)


if __name__ == "__main__":
    unittest.main()
