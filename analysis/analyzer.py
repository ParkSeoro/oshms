"""수익률 분석 모듈.

거래 기록을 분석하여 다양한 수익률 지표와 리포트를 생성한다.
"""

import json
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from trading.order_manager import TradeRecord
from utils.logger import setup_logger

logger = setup_logger("oshms.analysis")


class ProfitAnalyzer:
    """수익률 분석기."""

    TRADE_LOG_PATH = Path("logs/trades.json")
    REPORT_DIR = Path("reports")

    def __init__(self):
        self.trades: list[TradeRecord] = []
        self._load_trades()

    def _load_trades(self) -> None:
        """거래 기록을 로드한다."""
        if self.TRADE_LOG_PATH.exists():
            try:
                data = json.loads(self.TRADE_LOG_PATH.read_text(encoding="utf-8"))
                self.trades = [TradeRecord(**r) for r in data]
            except (json.JSONDecodeError, TypeError):
                self.trades = []

    def get_summary(self) -> dict:
        """전체 거래 요약을 반환한다."""
        if not self.trades:
            return {"message": "거래 기록 없음"}

        sells = [t for t in self.trades if t.side == "SELL"]
        buys = [t for t in self.trades if t.side == "BUY"]

        total_profit = sum(t.profit_loss for t in sells)
        total_buy_amount = sum(t.amount for t in buys)
        total_sell_amount = sum(t.amount for t in sells)

        wins = [t for t in sells if t.profit_loss > 0]
        losses = [t for t in sells if t.profit_loss < 0]
        evens = [t for t in sells if t.profit_loss == 0]

        win_rate = (len(wins) / len(sells) * 100) if sells else 0

        avg_win = sum(t.profit_loss for t in wins) / len(wins) if wins else 0
        avg_loss = sum(t.profit_loss for t in losses) / len(losses) if losses else 0
        profit_factor = abs(avg_win / avg_loss) if avg_loss != 0 else float("inf")

        return {
            "기간": self._get_period(),
            "총거래횟수": len(self.trades),
            "매수횟수": len(buys),
            "매도횟수": len(sells),
            "총매수금액": total_buy_amount,
            "총매도금액": total_sell_amount,
            "총실현손익": total_profit,
            "총수익률": (total_profit / total_buy_amount * 100) if total_buy_amount else 0,
            "승률": win_rate,
            "승리": len(wins),
            "패배": len(losses),
            "무승부": len(evens),
            "평균수익(승)": avg_win,
            "평균손실(패)": avg_loss,
            "손익비": profit_factor,
            "최대수익거래": max((t.profit_loss for t in sells), default=0),
            "최대손실거래": min((t.profit_loss for t in sells), default=0),
        }

    def get_daily_summary(self) -> list[dict]:
        """일별 거래 요약을 반환한다."""
        daily: dict[str, list[TradeRecord]] = defaultdict(list)
        for t in self.trades:
            date = t.timestamp[:10]
            daily[date].append(t)

        results = []
        for date in sorted(daily.keys()):
            day_trades = daily[date]
            sells = [t for t in day_trades if t.side == "SELL"]
            buys = [t for t in day_trades if t.side == "BUY"]
            profit = sum(t.profit_loss for t in sells)
            buy_amount = sum(t.amount for t in buys)

            results.append({
                "날짜": date,
                "매수": len(buys),
                "매도": len(sells),
                "실현손익": profit,
                "수익률": (profit / buy_amount * 100) if buy_amount else 0,
                "매수금액": buy_amount,
            })
        return results

    def get_stock_summary(self) -> list[dict]:
        """종목별 거래 요약을 반환한다."""
        by_stock: dict[str, list[TradeRecord]] = defaultdict(list)
        for t in self.trades:
            by_stock[t.stock_code].append(t)

        results = []
        for code in sorted(by_stock.keys()):
            trades = by_stock[code]
            sells = [t for t in trades if t.side == "SELL"]
            buys = [t for t in trades if t.side == "BUY"]
            profit = sum(t.profit_loss for t in sells)
            buy_amount = sum(t.amount for t in buys)
            name = trades[0].stock_name

            wins = len([t for t in sells if t.profit_loss > 0])

            results.append({
                "종목코드": code,
                "종목명": name,
                "매수횟수": len(buys),
                "매도횟수": len(sells),
                "실현손익": profit,
                "수익률": (profit / buy_amount * 100) if buy_amount else 0,
                "승률": (wins / len(sells) * 100) if sells else 0,
            })

        results.sort(key=lambda x: x["실현손익"], reverse=True)
        return results

    def get_hourly_analysis(self) -> list[dict]:
        """시간대별 매매 성과를 분석한다."""
        hourly: dict[str, list[TradeRecord]] = defaultdict(list)
        for t in self.trades:
            if t.side == "SELL":
                hour = t.timestamp[11:13]
                hourly[hour].append(t)

        results = []
        for hour in sorted(hourly.keys()):
            trades = hourly[hour]
            profit = sum(t.profit_loss for t in trades)
            wins = len([t for t in trades if t.profit_loss > 0])
            results.append({
                "시간대": f"{hour}시",
                "매도횟수": len(trades),
                "실현손익": profit,
                "승률": (wins / len(trades) * 100) if trades else 0,
            })
        return results

    def generate_report(self) -> str:
        """텍스트 기반 분석 리포트를 생성한다."""
        summary = self.get_summary()
        if "message" in summary:
            return "거래 기록이 없습니다."

        lines = [
            "=" * 60,
            f"  OSHMS 매매 분석 리포트 ({datetime.now().strftime('%Y-%m-%d %H:%M')})",
            "=" * 60,
            "",
            "▣ 전체 요약",
            f"  기간: {summary['기간']}",
            f"  총 거래: {summary['총거래횟수']}건 (매수 {summary['매수횟수']}건, 매도 {summary['매도횟수']}건)",
            f"  총 실현손익: {summary['총실현손익']:>12,}원 ({summary['총수익률']:+.2f}%)",
            f"  승률: {summary['승률']:.1f}% ({summary['승리']}승 {summary['패배']}패 {summary['무승부']}무)",
            f"  손익비: {summary['손익비']:.2f}",
            f"  평균 수익(승): {summary['평균수익(승)']:>10,.0f}원",
            f"  평균 손실(패): {summary['평균손실(패)']:>10,.0f}원",
            f"  최대 수익 거래: {summary['최대수익거래']:>10,}원",
            f"  최대 손실 거래: {summary['최대손실거래']:>10,}원",
            "",
        ]

        # 일별 요약
        daily = self.get_daily_summary()
        if daily:
            lines.append("▣ 일별 현황")
            lines.append(f"  {'날짜':<12} {'매수':>4} {'매도':>4} {'실현손익':>12} {'수익률':>8}")
            lines.append("  " + "-" * 44)
            for d in daily:
                lines.append(
                    f"  {d['날짜']:<12} {d['매수']:>4} {d['매도']:>4} "
                    f"{d['실현손익']:>11,}원 {d['수익률']:>+7.2f}%"
                )
            lines.append("")

        # 종목별 요약
        stock = self.get_stock_summary()
        if stock:
            lines.append("▣ 종목별 현황")
            lines.append(f"  {'종목명':<12} {'매수':>4} {'매도':>4} {'실현손익':>12} {'승률':>7}")
            lines.append("  " + "-" * 44)
            for s in stock:
                lines.append(
                    f"  {s['종목명']:<12} {s['매수횟수']:>4} {s['매도횟수']:>4} "
                    f"{s['실현손익']:>11,}원 {s['승률']:>6.1f}%"
                )
            lines.append("")

        # 시간대별 분석
        hourly = self.get_hourly_analysis()
        if hourly:
            lines.append("▣ 시간대별 성과")
            lines.append(f"  {'시간대':<6} {'매도':>4} {'실현손익':>12} {'승률':>7}")
            lines.append("  " + "-" * 34)
            for h in hourly:
                lines.append(
                    f"  {h['시간대']:<6} {h['매도횟수']:>4} "
                    f"{h['실현손익']:>11,}원 {h['승률']:>6.1f}%"
                )
            lines.append("")

        lines.append("=" * 60)

        report = "\n".join(lines)

        # 파일로 저장
        self.REPORT_DIR.mkdir(parents=True, exist_ok=True)
        filename = f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        report_path = self.REPORT_DIR / filename
        report_path.write_text(report, encoding="utf-8")
        logger.info("리포트 저장: %s", report_path)

        return report

    def generate_csv_report(self) -> Path:
        """CSV 형식의 거래 내역을 생성한다."""
        self.REPORT_DIR.mkdir(parents=True, exist_ok=True)
        filename = f"trades_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        filepath = self.REPORT_DIR / filename

        header = "시각,구분,종목코드,종목명,수량,가격,금액,손익,수익률,사유\n"
        rows = []
        for t in self.trades:
            rows.append(
                f"{t.timestamp},{t.side},{t.stock_code},{t.stock_name},"
                f"{t.quantity},{t.price},{t.amount},{t.profit_loss},"
                f"{t.profit_rate:.2f},{t.reason}\n"
            )

        filepath.write_text(header + "".join(rows), encoding="utf-8-sig")
        logger.info("CSV 리포트 저장: %s", filepath)
        return filepath

    def _get_period(self) -> str:
        """거래 기간을 반환한다."""
        if not self.trades:
            return "N/A"
        dates = [t.timestamp[:10] for t in self.trades]
        return f"{min(dates)} ~ {max(dates)}"
