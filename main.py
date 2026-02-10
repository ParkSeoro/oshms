#!/usr/bin/env python3
"""OSHMS - 주식 자동 매매 시스템.

사용법:
    python main.py trade [--stocks 005930,000660] [--interval 10] [--strategy expert]
    python main.py report
    python main.py balance
    python main.py status
    python main.py analyze 005930 --name 삼성전자
    python main.py gui          # 데스크톱 GUI 실행
    python main.py web          # 웹 서버 실행 (모바일/PC 브라우저)
"""

import argparse
import sys

from config.settings import Settings
from api.kis_api import KISApi
from strategy import (
    ScalpingStrategy, MomentumStrategy, CombinedStrategy, ExpertStrategy,
)
from trading.trader import AutoTrader
from analysis.analyzer import ProfitAnalyzer
from utils.logger import setup_logger


def cmd_trade(args, settings: Settings) -> None:
    """자동 매매를 실행한다."""
    api = KISApi(settings)

    strategies = {
        "scalping": lambda: ScalpingStrategy(),
        "momentum": lambda: MomentumStrategy(),
        "combined": lambda: CombinedStrategy(),
        "expert": lambda: ExpertStrategy(api=api, settings=settings),
    }

    factory = strategies.get(args.strategy, strategies["expert"])
    strategy = factory()

    trader = AutoTrader(api, settings, strategy)

    target_stocks = None
    if args.stocks:
        target_stocks = [s.strip() for s in args.stocks.split(",")]

    trader.start(target_stocks=target_stocks, interval=args.interval)


def cmd_analyze(args, settings: Settings) -> None:
    """특정 종목을 전문가 분석한다 (매매 실행 없이 분석만)."""
    api = KISApi(settings)
    strategy = ExpertStrategy(api=api, settings=settings)

    stock_code = args.code
    stock_name = args.name or stock_code

    logger = setup_logger("oshms", settings.log_level)
    logger.info("종목 전문가 분석: %s(%s)", stock_name, stock_code)

    # 시장 컨텍스트
    from strategy.market_context import MarketContextAnalyzer
    market_analyzer = MarketContextAnalyzer(api)
    try:
        ctx = market_analyzer.analyze()
        strategy.set_market_context(ctx)
    except Exception:
        pass

    # 시세 데이터
    current_price = api.get_current_price(stock_code)
    if not current_price:
        print(f"종목 {stock_code} 시세 조회 실패")
        return

    current_price["stock_name"] = stock_name

    # 분봉 + 일봉
    candles = api.get_minute_chart(stock_code, period="3")
    if len(candles) < 20:
        candles = api.get_daily_chart(stock_code, count=60)

    if not candles:
        print("차트 데이터 조회 실패")
        return

    # 분석 실행
    analysis = strategy.full_analysis(stock_code, stock_name, candles, current_price)
    print()
    print(analysis.summary())
    print()

    # 뉴스 헤드라인
    if analysis.sentiment and analysis.sentiment.key_headlines:
        print("─" * 50)
        print("최신 뉴스:")
        for headline in analysis.sentiment.key_headlines:
            print(f"  {headline}")
        print()

    # 지지/저항선
    if analysis.technical:
        t = analysis.technical
        print("─" * 50)
        print(f"기술적 지표 상세:")
        print(f"  SMA(5/20/60): {t.sma_5:,.0f} / {t.sma_20:,.0f} / {t.sma_60:,.0f}")
        print(f"  RSI: {t.rsi:.1f}")
        print(f"  MACD: {t.macd_line:,.0f} (신호선: {t.macd_signal:,.0f}, 히스토그램: {t.macd_histogram:,.0f})")
        print(f"  Stochastic: K={t.stoch_k:.1f} D={t.stoch_d:.1f}")
        print(f"  BB: 상단={t.bb_upper:,.0f} 중간={t.bb_middle:,.0f} 하단={t.bb_lower:,.0f} (폭={t.bb_width:.1f}%)")
        print(f"  VWAP: {t.vwap:,.0f}")
        print(f"  ATR: {t.atr:,.0f} ({t.atr_pct:.2f}%)")
        print(f"  일목균형표: 전환={t.ichimoku_tenkan:,.0f} 기준={t.ichimoku_kijun:,.0f} → {t.ichimoku_signal}")
        print(f"  거래량비: x{t.volume_ratio:.1f} (OBV추세: {t.obv_trend})")
        if t.support_levels:
            print(f"  지지선: {', '.join(f'{s:,.0f}' for s in t.support_levels)}")
        if t.resistance_levels:
            print(f"  저항선: {', '.join(f'{r:,.0f}' for r in t.resistance_levels)}")
        print()


def cmd_report(args, settings: Settings) -> None:
    """수익률 리포트를 출력한다."""
    setup_logger("oshms", settings.log_level)
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


def cmd_gui(args, settings: Settings) -> None:
    """데스크톱 GUI를 실행한다."""
    from gui.app import OshmsApp
    print("OSHMS 데스크톱 앱을 시작합니다...")
    app = OshmsApp()
    app.run()


def cmd_web(args, settings: Settings) -> None:
    """웹 서버를 실행한다."""
    from web.server import run_server
    host = args.host
    port = args.port
    print(f"OSHMS 웹 서버를 시작합니다: http://{host}:{port}")
    print("브라우저에서 접속하세요. (모바일: 같은 네트워크에서 PC의 IP로 접속)")
    run_server(host=host, port=port, debug=args.debug)


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
        print("  설정 오류:")
        for e in errors:
            print(f"    - {e}")
    else:
        print()
        print("  설정 유효")
    print("=" * 60)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="OSHMS - 주식 자동 매매 시스템 (전문가 모드)",
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
        choices=["expert", "scalping", "momentum", "combined"],
        default="expert",
        help="매매 전략 (기본: expert)",
    )

    # analyze 명령어
    analyze_parser = subparsers.add_parser("analyze", help="종목 전문가 분석 (매매 없이)")
    analyze_parser.add_argument("code", help="종목 코드 (예: 005930)")
    analyze_parser.add_argument("--name", help="종목명 (예: 삼성전자)", default=None)

    # report 명령어
    report_parser = subparsers.add_parser("report", help="수익률 리포트")
    report_parser.add_argument("--csv", action="store_true", help="CSV 파일도 생성")

    # balance 명령어
    subparsers.add_parser("balance", help="잔고 조회")

    # status 명령어
    subparsers.add_parser("status", help="시스템 상태 확인")

    # gui 명령어
    subparsers.add_parser("gui", help="데스크톱 GUI 실행")

    # web 명령어
    web_parser = subparsers.add_parser("web", help="웹 서버 실행 (모바일/PC 브라우저)")
    web_parser.add_argument("--host", default="0.0.0.0", help="바인드 주소 (기본: 0.0.0.0)")
    web_parser.add_argument("--port", type=int, default=5000, help="포트 (기본: 5000)")
    web_parser.add_argument("--debug", action="store_true", help="디버그 모드")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    settings = Settings.from_env(args.env)
    setup_logger("oshms", settings.log_level)

    commands = {
        "trade": cmd_trade,
        "analyze": cmd_analyze,
        "report": cmd_report,
        "balance": cmd_balance,
        "status": cmd_status,
        "gui": cmd_gui,
        "web": cmd_web,
    }

    cmd_func = commands.get(args.command)
    if cmd_func:
        cmd_func(args, settings)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
