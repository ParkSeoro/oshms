#!/usr/bin/env python3
"""OSHMS - 주식 자동 매매 시스템.

사용법:
    python main.py trade [--stocks 005930,000660] [--interval 10] [--strategy scalping]
    python main.py report
    python main.py balance
    python main.py status
"""

import argparse
import sys

from config.settings import Settings
from api.kis_api import KISApi
from strategy import ScalpingStrategy, MomentumStrategy, CombinedStrategy
from trading.trader import AutoTrader
from analysis.analyzer import ProfitAnalyzer
from utils.logger import setup_logger


def cmd_trade(args, settings: Settings) -> None:
    """자동 매매를 실행한다."""
    api = KISApi(settings)

    strategies = {
        "scalping": ScalpingStrategy,
        "momentum": MomentumStrategy,
        "combined": CombinedStrategy,
    }

    strategy_cls = strategies.get(args.strategy, CombinedStrategy)
    strategy = strategy_cls()

    trader = AutoTrader(api, settings, strategy)

    target_stocks = None
    if args.stocks:
        target_stocks = [s.strip() for s in args.stocks.split(",")]

    trader.start(target_stocks=target_stocks, interval=args.interval)


def cmd_report(args, settings: Settings) -> None:
    """수익률 리포트를 출력한다."""
    logger = setup_logger("oshms", settings.log_level)
    analyzer = ProfitAnalyzer()

    report = analyzer.generate_report()
    print(report)

    if args.csv:
        csv_path = analyzer.generate_csv_report()
        print(f"\nCSV 파일: {csv_path}")


def cmd_balance(args, settings: Settings) -> None:
    """잔고를 조회한다."""
    api = KISApi(settings)
    balance = api.get_balance()

    print("=" * 60)
    print("  보유 종목 현황")
    print("=" * 60)

    holdings = balance.get("holdings", [])
    if not holdings:
        print("  보유 종목이 없습니다.")
    else:
        print(f"  {'종목명':<12} {'수량':>6} {'평균가':>10} {'현재가':>10} {'손익':>12} {'수익률':>8}")
        print("  " + "-" * 58)
        for h in holdings:
            print(
                f"  {h['stock_name']:<12} {h['quantity']:>6} "
                f"{h['avg_price']:>9,} {h['current_price']:>9,} "
                f"{h['profit_loss']:>11,} {h['profit_rate']:>+7.2f}%"
            )

    summary = balance.get("summary", {})
    if summary:
        print()
        print(f"  총 매수금액: {summary.get('total_buy_amount', 0):>14,}원")
        print(f"  총 평가금액: {summary.get('total_eval_amount', 0):>14,}원")
        print(f"  총 평가손익: {summary.get('total_profit_loss', 0):>14,}원")
        print(f"  예수금:      {summary.get('available_cash', 0):>14,}원")
    print("=" * 60)


def cmd_status(args, settings: Settings) -> None:
    """시스템 상태를 출력한다."""
    print("=" * 60)
    print("  OSHMS 시스템 상태")
    print("=" * 60)
    print(f"  모드: {'모의투자' if settings.is_mock else '실전투자'}")
    print(f"  API URL: {settings.base_url}")
    print(f"  계좌: {settings.account_number}-{settings.account_suffix}")
    print(f"  매매 시간: {settings.trading_start_time} ~ {settings.trading_end_time}")
    print(f"  최대 매수금액: {settings.max_buy_amount:,}원")
    print(f"  최대 보유종목: {settings.max_hold_count}개")
    print(f"  손절: {settings.stop_loss_pct}% / 익절: {settings.take_profit_pct}%")

    errors = settings.validate()
    if errors:
        print()
        print("  ⚠ 설정 오류:")
        for e in errors:
            print(f"    - {e}")
    else:
        print()
        print("  ✓ 설정 유효")
    print("=" * 60)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="OSHMS - 주식 자동 매매 시스템",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--env", help=".env 파일 경로", default=None)
    subparsers = parser.add_subparsers(dest="command", help="명령어")

    # trade 명령어
    trade_parser = subparsers.add_parser("trade", help="자동 매매 실행")
    trade_parser.add_argument(
        "--stocks", help="감시할 종목 코드 (콤마 구분, 예: 005930,000660)", default=None
    )
    trade_parser.add_argument(
        "--interval", type=int, help="매매 사이클 간격 (초)", default=10
    )
    trade_parser.add_argument(
        "--strategy",
        choices=["scalping", "momentum", "combined"],
        default="combined",
        help="매매 전략 (기본: combined)",
    )

    # report 명령어
    report_parser = subparsers.add_parser("report", help="수익률 리포트")
    report_parser.add_argument("--csv", action="store_true", help="CSV 파일도 생성")

    # balance 명령어
    subparsers.add_parser("balance", help="잔고 조회")

    # status 명령어
    subparsers.add_parser("status", help="시스템 상태 확인")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    settings = Settings.from_env(args.env)
    logger = setup_logger("oshms", settings.log_level)

    commands = {
        "trade": cmd_trade,
        "report": cmd_report,
        "balance": cmd_balance,
        "status": cmd_status,
    }

    cmd_func = commands.get(args.command)
    if cmd_func:
        cmd_func(args, settings)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
