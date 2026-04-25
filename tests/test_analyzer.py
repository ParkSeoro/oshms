"""수익률 분석기 테스트."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from analysis.analyzer import ProfitAnalyzer
from trading.order_manager import TradeRecord


class TestProfitAnalyzer(unittest.TestCase):
    """ProfitAnalyzer 테스트."""

    def _make_analyzer_with_trades(self, trades: list[TradeRecord]) -> ProfitAnalyzer:
        """테스트용 거래 기록이 포함된 분석기를 생성한다."""
        analyzer = ProfitAnalyzer()
        analyzer.trades = trades
        return analyzer

    def test_empty_trades(self):
        analyzer = ProfitAnalyzer()
        analyzer.trades = []
        summary = analyzer.get_summary()
        self.assertIn("message", summary)

    def test_summary_with_trades(self):
        trades = [
            TradeRecord("005930", "삼성전자", "BUY", 10, 70000, 700000, "테스트매수",
                        "2025-01-01 09:30:00"),
            TradeRecord("005930", "삼성전자", "SELL", 10, 72000, 720000, "테스트매도",
                        "2025-01-01 10:30:00", profit_loss=20000, profit_rate=2.86),
            TradeRecord("000660", "SK하이닉스", "BUY", 5, 130000, 650000, "테스트매수",
                        "2025-01-01 11:00:00"),
            TradeRecord("000660", "SK하이닉스", "SELL", 5, 127000, 635000, "테스트매도",
                        "2025-01-01 14:00:00", profit_loss=-15000, profit_rate=-2.31),
        ]
        analyzer = self._make_analyzer_with_trades(trades)
        summary = analyzer.get_summary()

        self.assertEqual(summary["total_trades"], 4)
        self.assertEqual(summary["buy_count"], 2)
        self.assertEqual(summary["sell_count"], 2)
        self.assertEqual(summary["total_profit"], 5000)  # 20000 - 15000
        self.assertEqual(summary["wins"], 1)
        self.assertEqual(summary["losses"], 1)
        self.assertAlmostEqual(summary["win_rate"], 50.0)

    def test_daily_summary(self):
        trades = [
            TradeRecord("005930", "삼성전자", "BUY", 10, 70000, 700000, "매수",
                        "2025-01-01 09:30:00"),
            TradeRecord("005930", "삼성전자", "SELL", 10, 72000, 720000, "매도",
                        "2025-01-01 10:30:00", profit_loss=20000, profit_rate=2.86),
            TradeRecord("005930", "삼성전자", "BUY", 10, 71000, 710000, "매수",
                        "2025-01-02 09:30:00"),
            TradeRecord("005930", "삼성전자", "SELL", 10, 70000, 700000, "매도",
                        "2025-01-02 10:30:00", profit_loss=-10000, profit_rate=-1.41),
        ]
        analyzer = self._make_analyzer_with_trades(trades)
        daily = analyzer.get_daily_summary()

        self.assertEqual(len(daily), 2)
        self.assertEqual(daily[0]["date"], "2025-01-01")
        self.assertEqual(daily[0]["profit"], 20000)
        self.assertEqual(daily[1]["date"], "2025-01-02")
        self.assertEqual(daily[1]["profit"], -10000)

    def test_stock_summary(self):
        trades = [
            TradeRecord("005930", "삼성전자", "BUY", 10, 70000, 700000, "매수",
                        "2025-01-01 09:30:00"),
            TradeRecord("005930", "삼성전자", "SELL", 10, 72000, 720000, "매도",
                        "2025-01-01 10:30:00", profit_loss=20000, profit_rate=2.86),
            TradeRecord("000660", "SK하이닉스", "BUY", 5, 130000, 650000, "매수",
                        "2025-01-01 11:00:00"),
            TradeRecord("000660", "SK하이닉스", "SELL", 5, 132000, 660000, "매도",
                        "2025-01-01 14:00:00", profit_loss=10000, profit_rate=1.54),
        ]
        analyzer = self._make_analyzer_with_trades(trades)
        stock = analyzer.get_stock_summary()

        self.assertEqual(len(stock), 2)
        self.assertEqual(stock[0]["stock_code"], "005930")

    def test_hourly_analysis(self):
        trades = [
            TradeRecord("005930", "삼성전자", "SELL", 10, 72000, 720000, "매도",
                        "2025-01-01 09:30:00", profit_loss=20000, profit_rate=2.86),
            TradeRecord("000660", "SK하이닉스", "SELL", 5, 132000, 660000, "매도",
                        "2025-01-01 09:45:00", profit_loss=10000, profit_rate=1.54),
            TradeRecord("035720", "카카오", "SELL", 20, 55000, 1100000, "매도",
                        "2025-01-01 14:00:00", profit_loss=-5000, profit_rate=-0.45),
        ]
        analyzer = self._make_analyzer_with_trades(trades)
        hourly = analyzer.get_hourly_analysis()

        self.assertEqual(len(hourly), 2)
        self.assertEqual(hourly[0]["시간대"], "09시")
        self.assertEqual(hourly[0]["매도횟수"], 2)

    def test_generate_report(self):
        trades = [
            TradeRecord("005930", "삼성전자", "BUY", 10, 70000, 700000, "매수",
                        "2025-01-01 09:30:00"),
            TradeRecord("005930", "삼성전자", "SELL", 10, 72000, 720000, "매도",
                        "2025-01-01 10:30:00", profit_loss=20000, profit_rate=2.86),
        ]
        analyzer = self._make_analyzer_with_trades(trades)
        report = analyzer.generate_report()

        self.assertIn("매매 분석 리포트", report)
        self.assertIn("총 실현손익", report)
        self.assertIn("20,000", report)

    def test_profit_factor_calculation(self):
        trades = [
            TradeRecord("005930", "삼성전자", "BUY", 10, 70000, 700000, "매수",
                        "2025-01-01 09:30:00"),
            TradeRecord("005930", "삼성전자", "SELL", 10, 73000, 730000, "매도",
                        "2025-01-01 10:30:00", profit_loss=30000, profit_rate=4.29),
            TradeRecord("000660", "SK하이닉스", "BUY", 5, 130000, 650000, "매수",
                        "2025-01-01 11:00:00"),
            TradeRecord("000660", "SK하이닉스", "SELL", 5, 127000, 635000, "매도",
                        "2025-01-01 14:00:00", profit_loss=-15000, profit_rate=-2.31),
        ]
        analyzer = self._make_analyzer_with_trades(trades)
        summary = analyzer.get_summary()

        self.assertEqual(summary["avg_win"], 30000)
        self.assertEqual(summary["avg_loss"], -15000)
        self.assertAlmostEqual(summary["profit_factor"], 2.0)


if __name__ == "__main__":
    unittest.main()
