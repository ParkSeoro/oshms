"""자동 매매 엔진.

전략 분석 → 리스크 관리 → 주문 실행의 전체 사이클을 관리한다.
Expert 모드에서는 시장 컨텍스트와 멀티 타임프레임 분석을 추가한다.
"""

import time
from datetime import datetime

from api.kis_api import KISApi
from config.settings import Settings
from strategy.base import BaseStrategy, SignalType
from strategy.expert import ExpertStrategy
from strategy.market_context import MarketContextAnalyzer, MarketContext
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

        # Expert 모드 시장 분석기
        self._market_analyzer: MarketContextAnalyzer | None = None
        self._market_ctx: MarketContext | None = None
        self._market_ctx_updated: float = 0

        if isinstance(strategy, ExpertStrategy):
            self._market_analyzer = MarketContextAnalyzer(api)
            strategy.market_analyzer = self._market_analyzer

    def is_trading_time(self) -> bool:
        """현재 시간이 매매 가능 시간인지 확인한다.

        장 마감 10분 전(15:10~15:20) 이후에는 신규 매매를 하지 않는다.
        (보유 중인 종목의 손절/익절은 계속 작동)
        """
        now = datetime.now().strftime("%H:%M")
        return self.settings.trading_start_time <= now <= self.settings.trading_end_time

    def start(self, target_stocks: list[str] | None = None, interval: int = 10) -> None:
        """자동 매매를 시작한다.

        Args:
            target_stocks: 감시할 종목 코드 리스트. None이면 거래량 상위 자동 선정.
            interval: 매매 사이클 간격 (초)
        """
        self._running = True
        is_expert = isinstance(self.strategy, ExpertStrategy)

        logger.info("=" * 70)
        logger.info("  OSHMS 자동 매매 시스템 가동")
        logger.info("=" * 70)
        logger.info("전략: %s%s", self.strategy.name, " (전문가 모드)" if is_expert else "")
        logger.info("매매 시간: %s ~ %s", self.settings.trading_start_time, self.settings.trading_end_time)
        logger.info("최대 매수금액: %s원", f"{self.settings.max_buy_amount:,}")
        logger.info("최대 보유종목: %d개", self.settings.max_hold_count)
        logger.info("손절: %.1f%% / 익절: %.1f%%", self.settings.stop_loss_pct, self.settings.take_profit_pct)
        logger.info("감시 주기: %d초", interval)
        if is_expert:
            logger.info("분석 모듈: 기술적분석 + 캔들패턴 + 뉴스감성 + 시장레짐")
        logger.info("=" * 70)

        # 잔고 동기화
        self.order_manager.sync_positions()

        while self._running:
            try:
                if not self.is_trading_time():
                    now = datetime.now().strftime("%H:%M:%S")
                    logger.info("[%s] 매매 시간 외 - 대기 중...", now)
                    time.sleep(60)
                    continue

                # Expert 모드: 시장 컨텍스트 갱신 (60초마다)
                if is_expert:
                    self._update_market_context()

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

    def _update_market_context(self) -> None:
        """시장 컨텍스트를 갱신한다."""
        now = time.time()
        if now - self._market_ctx_updated < 60:
            return

        try:
            if self._market_analyzer:
                self._market_ctx = self._market_analyzer.analyze()
                if isinstance(self.strategy, ExpertStrategy):
                    self.strategy.set_market_context(self._market_ctx)
                self._market_ctx_updated = now

                # 시장 상황에 따른 동적 리스크 조정
                if self._market_ctx:
                    adj = self._market_analyzer.get_regime_strategy_adjustment(self._market_ctx)
                    if adj.get("stop_loss_adj", 0) != 0:
                        logger.debug(
                            "리스크 조정: 손절=%+.1f%% 포지션배수=%.1f",
                            adj["stop_loss_adj"], adj["position_size_mult"],
                        )
        except Exception as e:
            logger.warning("시장 컨텍스트 갱신 실패: %s", e)

    def _select_stocks(self) -> list[str]:
        """거래량 상위 종목을 자동 선정한다.

        급등/급락주와 저가주를 제외하고 안정적인 거래 대상을 선정한다.
        """
        try:
            rank = self.api.get_volume_rank(count=20)
            filtered = [
                s["stock_code"]
                for s in rank
                if (
                    -8 < s.get("change_rate", 0) < 10  # 급등/급락 제외 (비대칭: 상승은 더 허용)
                    and s.get("price", 0) > 2000  # 2,000원 미만 저가주 제외
                    and s.get("price", 0) < 500000  # 50만원 초과 고가주 제외 (슬리피지)
                )
            ]
            return filtered[:10]
        except Exception as e:
            logger.error("종목 선정 실패: %s", e)
            return []

    def _run_cycle(self, stocks: list[str]) -> None:
        """하나의 매매 사이클을 실행한다."""
        self._cycle_count += 1

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
            try:
                self.order_manager.execute_sell(code, "손절")
            except Exception as e:
                logger.error("[%s] 손절 매도 실패: %s", code, e)

        # 익절
        for code in self.order_manager.check_take_profit():
            try:
                self.order_manager.execute_sell(code, "익절")
            except Exception as e:
                logger.error("[%s] 익절 매도 실패: %s", code, e)

        # 트레일링 스탑
        for code in self.order_manager.check_trailing_stop():
            try:
                self.order_manager.execute_sell(code, "트레일링스탑")
            except Exception as e:
                logger.error("[%s] 트레일링스탑 매도 실패: %s", code, e)

    def _analyze_and_trade(self, stock_code: str) -> None:
        """종목을 분석하고 매매를 실행한다."""
        # 시세 데이터 조회
        current_price = self.api.get_current_price(stock_code)
        if not current_price:
            return

        # 멀티 타임프레임: 분봉 + 일봉 모두 수집
        candles = self.api.get_minute_chart(stock_code, period="3")
        if len(candles) < 20:
            candles = self.api.get_daily_chart(stock_code, count=60)

        if not candles:
            return

        # Expert 모드 분석 데이터 (ATR 등)
        analysis = None
        atr_value = 0.0
        if isinstance(self.strategy, ExpertStrategy):
            analysis = self.strategy.full_analysis(
                stock_code,
                current_price.get("stock_name", stock_code),
                candles,
                current_price,
            )
            if analysis.technical:
                atr_value = analysis.technical.atr
            if analysis.decision != "HOLD":
                logger.info("\n%s", analysis.summary())
            elif self._cycle_count % 5 == 1:
                logger.info(
                    "[%s] %s원 | 점수=%.3f 신뢰도=%.0f%% → HOLD",
                    current_price.get("stock_name", stock_code),
                    f"{current_price['price']:,}",
                    analysis.total_score, analysis.confidence * 100,
                )

        # 전략 분석
        signal = self.strategy.analyze(stock_code, candles, current_price)

        if signal.signal_type == SignalType.BUY:
            if not self.order_manager.can_buy():
                return
            if stock_code in self.order_manager.positions:
                return

            stock_name = current_price.get("stock_name", stock_code)
            logger.info(
                "▶ 매수 신호: %s(%s) 가격=%s 강도=%.2f | %s",
                stock_name, stock_code,
                f"{current_price['price']:,}",
                signal.strength, signal.reason,
            )

            # Expert 모드: 매수 강도 기준 차등
            min_strength = 0.20 if isinstance(self.strategy, ExpertStrategy) else 0.3
            if signal.strength >= min_strength:
                self.order_manager.execute_buy(
                    stock_code, stock_name, current_price["price"], signal.reason,
                    strength=signal.strength, atr=atr_value,
                )
            else:
                logger.debug(
                    "  → 매수 신호 강도 부족: %.2f < %.2f (패스)", signal.strength, min_strength,
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

                # 매도는 더 민감하게 (손실 최소화)
                if signal.strength >= 0.12:
                    self.order_manager.execute_sell(stock_code, signal.reason)
                else:
                    logger.debug(
                        "  → 매도 신호 강도 부족: %.2f < 0.12 (패스)", signal.strength,
                    )

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

        # 시장 컨텍스트 표시
        if self._market_ctx:
            logger.info(
                "  시장: %s (KOSPI=%+.2f%% KOSDAQ=%+.2f%%)",
                self._market_ctx.regime,
                self._market_ctx.kospi_change,
                self._market_ctx.kosdaq_change,
            )

    def run_single_cycle(self, target_stocks: list[str]) -> dict:
        """단일 매매 사이클을 실행하고 결과를 반환한다. (테스트/수동 실행용)"""
        self.order_manager.sync_positions()

        if isinstance(self.strategy, ExpertStrategy):
            self._update_market_context()

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
            "market_regime": self._market_ctx.regime if self._market_ctx else "unknown",
        }
