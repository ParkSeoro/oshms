"""설정 모듈 테스트."""

import os
import unittest

from config.settings import Settings


class TestSettings(unittest.TestCase):
    """Settings 테스트."""

    def test_default_values(self):
        s = Settings()
        self.assertEqual(s.app_key, "")
        self.assertTrue(s.is_mock)  # 안전을 위해 기본값은 모의투자
        # v4.7: 자본 100,000 × 2종목 집중 투자 기준으로 정합
        self.assertEqual(s.max_buy_amount, 50_000)
        self.assertEqual(s.max_hold_count, 2)
        self.assertEqual(s.stop_loss_pct, -4.0)
        self.assertEqual(s.take_profit_pct, 1.5)
        self.assertEqual(s.initial_capital, 100_000)

    def test_mock_url(self):
        s = Settings(is_mock=True)
        self.assertIn("vts", s.base_url)

    def test_real_url(self):
        s = Settings(is_mock=False)
        self.assertNotIn("vts", s.base_url)

    def test_account_parsing(self):
        s = Settings(account_no="12345678-01")
        self.assertEqual(s.account_number, "12345678")
        self.assertEqual(s.account_suffix, "01")

    def test_account_parsing_no_dash(self):
        s = Settings(account_no="1234567801")
        self.assertEqual(s.account_number, "12345678")
        self.assertEqual(s.account_suffix, "01")

    def test_validate_empty(self):
        s = Settings()
        errors = s.validate()
        self.assertTrue(len(errors) >= 3)  # key, secret, account

    def test_validate_valid(self):
        # v4.7: 자본(100k) >= max_buy × hold_count, stop_loss <= -3.0
        s = Settings(
            app_key="test_key",
            app_secret="test_secret",
            account_no="12345678-01",
            max_buy_amount=50_000,
            max_hold_count=2,
            stop_loss_pct=-4.0,
            take_profit_pct=1.5,
            initial_capital=100_000,
        )
        errors = s.validate()
        self.assertEqual(len(errors), 0, f"예상치 못한 오류: {errors}")

    def test_validate_capital_overflow(self):
        # v4.7: max_buy × hold_count > capital 이면 오류
        s = Settings(
            app_key="k", app_secret="s", account_no="12345678-01",
            max_buy_amount=100_000, max_hold_count=5,
            stop_loss_pct=-4.0, take_profit_pct=1.5,
            initial_capital=100_000,
        )
        errors = s.validate()
        self.assertTrue(any("자본 부족" in e for e in errors))

    def test_validate_shallow_stop_loss(self):
        # v4.7: stop_loss -2.5 같은 얕은 값은 논지 매도 무력화
        s = Settings(
            app_key="k", app_secret="s", account_no="12345678-01",
            max_buy_amount=50_000, max_hold_count=2,
            stop_loss_pct=-2.5, take_profit_pct=1.5,
            initial_capital=100_000,
        )
        errors = s.validate()
        self.assertTrue(any("논지" in e for e in errors))

    def test_validate_bad_amounts(self):
        s = Settings(
            app_key="k",
            app_secret="s",
            account_no="12345678-01",
            max_buy_amount=-100,
            stop_loss_pct=2.0,
            take_profit_pct=-1.0,
        )
        errors = s.validate()
        self.assertGreater(len(errors), 0)

    def test_from_env(self):
        os.environ["KIS_APP_KEY"] = "test_key"
        os.environ["KIS_APP_SECRET"] = "test_secret"
        os.environ["KIS_ACCOUNT_NO"] = "99999999-01"
        os.environ["KIS_MOCK"] = "true"
        os.environ["MAX_BUY_AMOUNT"] = "1000000"
        os.environ["INITIAL_CAPITAL"] = "200000"

        try:
            s = Settings.from_env()
            self.assertEqual(s.app_key, "test_key")
            self.assertEqual(s.app_secret, "test_secret")
            self.assertEqual(s.account_no, "99999999-01")
            self.assertTrue(s.is_mock)
            self.assertEqual(s.max_buy_amount, 1_000_000)
            self.assertEqual(s.initial_capital, 200_000)
        finally:
            for key in ["KIS_APP_KEY", "KIS_APP_SECRET", "KIS_ACCOUNT_NO",
                         "KIS_MOCK", "MAX_BUY_AMOUNT", "INITIAL_CAPITAL"]:
                os.environ.pop(key, None)


if __name__ == "__main__":
    unittest.main()
