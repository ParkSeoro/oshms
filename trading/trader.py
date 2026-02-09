"""자동 매매 엔진.

전략 분석 → 리스크 관리 → 주문 실행의 전체 사이클을 관리한다.
"""

import time
from datetime import datetime

from api.kis_api import KISApi
from config.settings import Settings
from strategy.base import BaseStrategy, SignalType
from trading.order_manager import OrderManager
from utils.logger import setup_logger

logger = setup_logger("oshms.trading.trader")


class AutoTrader:
    """자동 매매 트레이더."""

    def __init__(self, api: KISApi, settings: Settings, strategy: BaseStrategy):
        self.api = api
        self.settings = settings
        self.strategy = strategy
        self.order_manager = OrderManager(api, settings)
        self._running = False
        self._cycle_count = 0

    def is_trading_time(self) -> bool:
        """현재 시간이 매매 가능 시간인지 확인한다."""
        now = datetime.now().strftime("%H:%M")
        return self.settings.trading_start_time <= now <= self.settings.trading_end_time

    def start(self, target_stocks: list[str] | None = None, interval: int = 10) -> None:
        """자동 매매를 시작한다.

        Args:
            target_stocks: 감시할 종목 코드 리스트. None이면 거래량 상위 자동 선정.
            interval: 매매 사이클 간격 (초)
        """
        self._running = True
        logger.info("=" * 60)
        logger.info("자동 매매 시작")
        logger.info("전략: %s", self.strategy.name)
        logger.info("매매 시간: %s ~ %s", self.settings.trading_start_time, self.settings.trading_end_time)
        logger.info("최대 매수금액: %s원", f"{self.settings.max_buy_amount:,}")
        logger.info("최대 보유종목: %d개", self.settings.max_hold_count)
        logger.info("손절: %.1f%% / 익절: %.1f%%", self.settings.stop_loss_pct, self.settings.take_profit_pct)
        logger.info("감시 주기: %d초", interval)
        logger.info("=" * 60)

        # 잔고 동기화
        self.order_manager.sync_positions()

        while self._running:
            try:
                if not self.is_trading_time():
                    now = datetime.now().strftime("%H:%M:%S")
                    logger.info("[%s] 매매 시간 외 - 대기 중...", now)
                    time.sleep(60)
                    continue

                stocks = target_stocks or self._select_stocks()
                self._run_cycle(stocks)

                time.sleep(interval)
            except KeyboardInterrupt:
                logger.info("사용자 중단 요청")
                self.stop()
            except Exception as e:
                logger.error("매매 사이클 오류: %s", e, exc_info=True)
                time.sleep(30)

    def stop(self) -> None:
        """자동 매매를 중지한다."""
        self._running = False
        logger.info("자동 매매 중지 (총 %d 사이클)", self._cycle_count)

    def _select_stocks(self) -> list[str]:
        """거래량 상위 종목을 자동 선정한다."""
        try:
            rank = self.api.get_volume_rank(count=10)
            # 변동률이 ±15% 이내인 종목만 (급등/급락주 제외)
            filtered = [
                s["stock_code"]
                for s in rank
                if -15 < s.get("change_rate", 0) < 15 and s.get("price", 0) > 1000
            ]
            return filtered[:10]
        except Exception as e:
            logger.error("종목 선정 실패: %s", e)
            return []

    def _run_cycle(self, stocks: list[str]) -> None:
        """하나의 매매 사이클을 실행한다."""
        self._cycle_count += 1
        now = datetime.now().strftime("%H:%M:%S")

        # 1. 보유 종목 가격 갱신
        self.order_manager.update_prices()

        # 2. 손절/익절 확인 (최우선)
        self._check_risk_management()

        # 3. 종목별 전략 분석
        for stock_code in stocks:
            if not self._running:
                break

            try:
                self._analyze_and_trade(stock_code)
                time.sleep(0.2)  # API 속도 제한 방지
            except Exception as e:
                logger.error("[%s] 분석 중 오류: %s", stock_code, e)

        if self._cycle_count % 10 == 0:
            self._log_status()

    def _check_risk_management(self) -> None:
        """리스크 관리: 손절, 익절, 트레일링 스탑을 확인한다."""
        # 손절
        for code in self.order_manager.check_stop_loss():
            self.order_manager.execute_sell(code, "손절")

        # 익절
        for code in self.order_manager.check_take_profit():
            self.order_manager.execute_sell(code, "익절")

        # 트레일링 스탑
        for code in self.order_manager.check_trailing_stop():
            self.order_manager.execute_sell(code, "트레일링스탑")

    def _analyze_and_trade(self, stock_code: str) -> None:
        """종목을 분석하고 매매를 실행한다."""
        # 시세 데이터 조회
        current_price = self.api.get_current_price(stock_code)
        if not current_price:
            return

        candles = self.api.get_minute_chart(stock_code, period="3")
        if len(candles) < 20:
            # 분봉 부족 시 일봉으로 보완
            candles = self.api.get_daily_chart(stock_code, count=60)

        if not candles:
            return

        # 전략 분석
        signal = self.strategy.analyze(stock_code, candles, current_price)

        if signal.signal_type == SignalType.BUY:
            if not self.order_manager.can_buy():
                return
            if stock_code in self.order_manager.positions:
                return

            stock_name = self._get_stock_name(stock_code, current_price)
            logger.info(
                "▶ 매수 신호: %s(%s) 가격=%s 강도=%.2f | %s",
                stock_name, stock_code,
                f"{current_price['price']:,}",
                signal.strength, signal.reason,
            )

            if signal.strength >= 0.4:
                self.order_manager.execute_buy(
                    stock_code, stock_name, current_price["price"], signal.reason
                )

        elif signal.signal_type == SignalType.SELL:
            if stock_code in self.order_manager.positions:
                pos = self.order_manager.positions[stock_code]
                logger.info(
                    "▶ 매도 신호: %s(%s) 가격=%s 수익률=%.2f%% 강도=%.2f | %s",
                    pos.stock_name, stock_code,
                    f"{current_price['price']:,}",
                    pos.profit_rate, signal.strength, signal.reason,
                )

                if signal.strength >= 0.3:
                    self.order_manager.execute_sell(stock_code, signal.reason)

    def _get_stock_name(self, stock_code: str, price_data: dict) -> str:
        """종목명을 가져온다."""
        return price_data.get("stock_name", stock_code)

    def _log_status(self) -> None:
        """현재 상태를 로그에 출력한다."""
        positions = self.order_manager.positions
        if not positions:
            logger.info("[사이클 #%d] 보유 종목 없음", self._cycle_count)
            return

        logger.info("[사이클 #%d] === 보유 현황 ===", self._cycle_count)
        total_profit = 0
        for code, pos in positions.items():
            total_profit += pos.profit_loss
            logger.info(
                "  %s(%s): %d주 평균=%s 현재=%s 손익=%s (%.2f%%)",
                pos.stock_name, code, pos.quantity,
                f"{pos.avg_price:,}", f"{pos.current_price:,}",
                f"{pos.profit_loss:,}", pos.profit_rate,
            )
        logger.info("  총 평가손익: %s원", f"{total_profit:,}")

    def run_single_cycle(self, target_stocks: list[str]) -> dict:
        """단일 매매 사이클을 실행하고 결과를 반환한다. (테스트/수동 실행용)"""
        self.order_manager.sync_positions()
        self._run_cycle(target_stocks)

        return {
            "cycle": self._cycle_count,
            "positions": {
                code: {
                    "name": pos.stock_name,
                    "quantity": pos.quantity,
                    "avg_price": pos.avg_price,
                    "current_price": pos.current_price,
                    "profit_rate": pos.profit_rate,
                }
                for code, pos in self.order_manager.positions.items()
            },
            "today_trades": len([
                t for t in self.order_manager.trade_history
                if t.timestamp.startswith(datetime.now().strftime("%Y-%m-%d"))
            ]),
        }
