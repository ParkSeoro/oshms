"""자동 매매 엔진.

전략 분석 → 리스크 관리 → 주문 실행의 전체 사이클을 관리한다.
Expert 모드에서는 시장 컨텍스트와 멀티 타임프레임 분석을 추가한다.

v2.7: 분석 기반 목표가 매도 — 상승여력 소진까지 홀딩
v2.9: 레짐 적응형 전략 전환, 멀티 타임프레임 확인, 패턴 메모리, 리스크 자동 진화
v3.0: 코드 자체 진화 엔진 연동 — 프로그램이 스스로 약점을 파악하고 개선
v3.2: 전략 앙상블, Q-Learning, 포트폴리오 최적화, 자동 매매 복기
v4.7: 논지 기반 매매 + 당일 재매수 금지 — whipsaw 방지
v4.8: 계좌 보호 시스템 + 체결강도 필터 + VI 감지
v4.9: 세션 기반 단일 진입 게이트 + 통합 확신도(Conviction 0~100) +
     일일 거래 예산으로 수익률 들쭉날쭉 문제 해결
"""

import json
import time
from datetime import datetime
from pathlib import Path

from api.kis_api import KISApi
from config.settings import Settings
from strategy.base import BaseStrategy, Signal, SignalType
from strategy.conviction import (
    ConvictionScore,
    MIN_CONVICTION_TO_ENTER,
    calc_conviction,
    session_fit_score,
)
from strategy.expert import ExpertStrategy
from strategy.market_context import MarketContextAnalyzer, MarketContext
from strategy.session import TradingSession, get_profile, get_session
from trading.order_manager import OrderManager
from trading.state_manager import StateManager
from utils.logger import setup_logger

logger = setup_logger("oshms.trading.trader")

TRADES_FILE = Path("logs/trades.json")


class AutoTrader:
    """자동 매매 트레이더."""

    def __init__(self, api: KISApi, settings: Settings, strategy: BaseStrategy,
                 market: str = "KR"):
        self.api = api
        self.settings = settings
        self.strategy = strategy
        self.market = market  # 활성 시장 코드 (KR, NASD, NYSE, AMEX, SEHK, TKSE)
        self.order_manager = OrderManager(api, settings, market=market)
        self._running = False
        self._cycle_count = 0

        # Expert 모드 시장 분석기
        self._market_analyzer: MarketContextAnalyzer | None = None
        self._market_ctx: MarketContext | None = None
        self._market_ctx_updated: float = 0

        # 손절 후 재매수 방지 (종목코드 → 쿨다운 만료 시각)
        self._cooldown_stocks: dict[str, float] = {}
        self._COOLDOWN_SECONDS = 300  # v4.0: 15분→5분 쿨다운 (빠른 재진입)

        # v4.6: 트레일링 베이스 — 진화가 조정하는 값. check_trailing_stop()에 전달됨
        self._trailing_base: float = 0.5

        # v4.7: 당일 손실 매도한 종목 — 같은 날 재매수 금지 (whipsaw 방지)
        # {stock_code: "YYYY-MM-DD"} — 날짜가 바뀌면 해제
        self._loss_stocks_today: dict[str, str] = {}

        # ── v4.8: 계좌 보호 시스템 ─────────────────────────────────────────
        # 당일 실현 손익 (원) — 매도 완료 시마다 누적
        self._daily_realized_pnl: float = 0.0
        self._daily_pnl_date: str = ""          # 날짜 바뀌면 초기화
        # 연속 손실 횟수 — 수익 매도 시 0으로 리셋
        self._consecutive_losses: int = 0
        # 방어 모드: 일일 손실 한도 도달 시 True (조건 강화, 포지션 축소)
        self._defense_mode: bool = False
        # 자동 정지: 연속 손실 한도 도달 시 True (매수 전면 중단)
        self._auto_stopped: bool = False
        # 자동 정지 진입 시점의 일일 손익 — 복구 판단 기준
        self._stop_entry_pnl: float = 0.0
        # VI 발동 추적: {stock_code: 마지막_변동률_스냅샷} — 급등 감지용
        self._prev_change_rate: dict[str, float] = {}

        # ── v4.9: 확신도 기반 단일 진입 게이트 + 일일 거래 예산 ───────────────
        # 일일 신규 진입 카운터 (오버트레이드 방지)
        self._daily_entry_date: str = ""
        self._daily_entries_total: int = 0                 # 오늘 전체 진입
        self._session_entries: dict[str, int] = {}         # session_name → count
        # 글로벌 일일 상한 (세션 프로필과 함께 이중 안전장치)
        self._daily_trade_budget: int = 6

        # 진화 엔진 (Level 0: 파라미터 진화)
        self._evolution = None
        self._trades_since_evolution = 0
        self._evolution_enabled = True
        self._last_evolution_time: float = 0  # v4.1: 시간 기반 진화 트리거

        # 코드 진화 엔진 (Level 1~3: 프로그램 자체 진화)
        self._code_evolution = None
        self._trades_since_code_evolution = 0
        self._last_code_evolution_time: float = 0  # v4.1: 시간 기반 진화 트리거

        # 종목 선정 캐시 (5분마다 갱신)
        self._stock_cache: list[str] = []
        self._stock_cache_time: float = 0
        self._STOCK_CACHE_TTL = 180  # 3분 (더 빈번한 종목 갱신)

        # 레짐 전략 조정값 (v2.9)
        self._regime_adj: dict = {}

        # 누적 통계 관리
        self._state_mgr = StateManager()

        # v3.2: 전략 앙상블
        self._ensemble = None

        # v3.2: Q-Learning 에이전트
        self._q_agent = None

        # v3.2: 포트폴리오 최적화
        self._portfolio_optimizer = None

        if isinstance(strategy, ExpertStrategy):
            self._market_analyzer = MarketContextAnalyzer(api)
            strategy.market_analyzer = self._market_analyzer

    def is_trading_time(self) -> bool:
        """현재 시간이 매매 가능 시간인지 확인한다.

        v4.2: 멀티마켓 지원 — 시장별 거래 시간 자동 판단.
        """
        from trading.market_scheduler import MARKETS, is_trading_time as _is_mkt_time
        m = MARKETS.get(self.market)
        if m:
            return _is_mkt_time(m)
        # 폴백: 설정 기반
        now = datetime.now().strftime("%H:%M")
        return self.settings.trading_start_time <= now <= self.settings.trading_end_time

    def _is_market_hours(self) -> bool:
        """시장이 아직 열려있는지 확인 (청산 시간 포함).

        v4.5: is_trading_time()은 신규 매수 시간만 체크.
        이 함수는 장마감 청산까지 포함하여 시장 종료 전까지 True.
        """
        from trading.market_scheduler import MARKETS, is_market_open as _is_open
        m = MARKETS.get(self.market)
        if m:
            return _is_open(m)
        now = datetime.now().strftime("%H:%M")
        return self.settings.trading_start_time <= now <= "15:30"

    def _resolve_stock_name(self, stock_code: str, price_data: dict = None) -> str:
        """종목명을 확실히 반환한다 (빈 문자열 방지).

        v4.3: API 데이터에 종목명이 없으면 캐시에서 조회.
        """
        if price_data:
            name = price_data.get("stock_name") or ""
            if name:
                return name
        # API 캐시에서 조회
        if hasattr(self.api, 'get_stock_name'):
            return self.api.get_stock_name(stock_code)
        return stock_code

    def set_market(self, market: str):
        """활성 시장을 변경한다."""
        self.market = market
        self.order_manager.market = market
        logger.info("활성 시장 변경: %s", market)

    def start(self, target_stocks: list[str] | None = None, interval: int = 10) -> None:
        """자동 매매를 시작한다."""
        self._running = True
        is_expert = isinstance(self.strategy, ExpertStrategy)

        # 진화 엔진 초기화
        self._init_evolution()

        logger.info("=" * 70)
        logger.info("  OSHMS 자동 매매 시스템 v4.9 가동")
        logger.info("  (세션 기반 + 통합 확신도 + 일일 거래 예산 — 수익률 안정화)")
        logger.info("=" * 70)
        mode_str = "모의투자" if self.settings.is_mock else "실전투자"
        logger.info("모드: %s | API: %s", mode_str, self.settings.base_url[:35])
        logger.info("전략: %s%s", self.strategy.name, " (전문가+가치투자)" if is_expert else "")
        logger.info("매매 시간: %s ~ %s", self.settings.trading_start_time, self.settings.trading_end_time)
        logger.info("최대 매수금액: %s원", f"{self.settings.max_buy_amount:,}")
        logger.info("최대 보유종목: %d개", self.settings.max_hold_count)
        logger.info("손절: %.1f%% / 최소매도수익: %.1f%%",
                     self.settings.stop_loss_pct,
                     self.order_manager.MIN_SELL_PROFIT_PCT)
        logger.info("감시 주기: %d초 | 종목풀: 전체 시장 스캔", interval)
        if self._evolution:
            logger.info("진화 엔진: 활성 (세대 #%d)", self._evolution.state.generation)
        logger.info("=" * 70)

        # 잔고 동기화
        self.order_manager.sync_positions()

        while self._running:
            try:
                if not self.is_trading_time():
                    # v4.5: 매매 시간은 끝났지만 시장이 열려있으면 리스크 관리 실행
                    # (장마감 청산 등 — 보유 종목이 다음날로 넘어가는 것 방지)
                    if self._is_market_hours() and self.order_manager.positions:
                        logger.info("[청산 모드] 매매 시간 종료 — 보유 종목 %d개 청산 확인 중",
                                    len(self.order_manager.positions))
                        self.order_manager.update_prices()
                        self._check_risk_management()
                        time.sleep(interval)
                        continue

                    now = datetime.now().strftime("%H:%M:%S")
                    logger.info("[%s] 매매 시간 외 - 대기 중...", now)
                    time.sleep(60)
                    continue

                # Expert 모드: 시장 컨텍스트 갱신 (60초마다)
                if is_expert:
                    self._update_market_context()

                stocks = target_stocks or self._select_stocks_wide()
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
        # 세션 종료 기록
        try:
            sells = [t for t in self.order_manager.trade_history if t.side == "SELL"]
            session_profit = sum(t.profit_loss for t in sells[-self._trades_since_evolution:]) if sells else 0
            self._state_mgr.end_session(self._trades_since_evolution, session_profit)
        except Exception as e:
            logger.warning("세션 종료 기록 실패: %s", e)
        logger.info("자동 매매 중지 (총 %d 사이클)", self._cycle_count)

    # ──────────────────────────────────────────────
    # 진화 엔진
    # ──────────────────────────────────────────────

    def _init_evolution(self):
        """진화 엔진을 초기화한다."""
        # Level 0: 파라미터 진화 엔진
        try:
            from learning.evolution import EvolutionEngine
            self._evolution = EvolutionEngine()
            self._trades_since_evolution = self._count_recent_trades()
            logger.info("진화 엔진 초기화 완료 (최근 거래 %d건)", self._trades_since_evolution)

            # v4.6: 재시작 시 저장된 진화 상태를 실제로 적용
            # (기존엔 상태만 로드하고 다음 진화 사이클까지 적용 안 됨)
            self._apply_persisted_evolution()
        except Exception as e:
            logger.warning("진화 엔진 초기화 실패: %s", e)
            self._evolution = None

        # Level 1~3: 코드 자체 진화 엔진 (+ 자동 매매 복기 v3.2)
        try:
            from learning.code_evolution import CodeEvolutionEngine
            self._code_evolution = CodeEvolutionEngine()
            self._trades_since_code_evolution = self._count_recent_trades_for_code_evo()
            logger.info("코드 진화 엔진 초기화 완료 (사이클 #%d, 개선 %d건)",
                        self._code_evolution.state.cycle,
                        self._code_evolution.state.total_improvements)
        except Exception as e:
            logger.warning("코드 진화 엔진 초기화 실패: %s", e)
            self._code_evolution = None

        # v3.2: 전략 앙상블 엔진
        try:
            from strategy.combined import CombinedStrategy
            self._ensemble = CombinedStrategy()
            logger.info("전략 앙상블 초기화 완료: %s", self._ensemble.get_ensemble_summary())
        except Exception as e:
            logger.warning("전략 앙상블 초기화 실패: %s", e)
            self._ensemble = None

        # v3.2: Q-Learning 에이전트
        try:
            from learning.q_learning import QLearningAgent
            self._q_agent = QLearningAgent()
            logger.info("Q-Learning 에이전트 초기화 완료 (상태 %d개, 학습 %d회)",
                        self._q_agent.total_states, self._q_agent.total_updates)
        except Exception as e:
            logger.warning("Q-Learning 에이전트 초기화 실패: %s", e)
            self._q_agent = None

        # v3.2: 포트폴리오 최적화
        try:
            from trading.portfolio_optimizer import PortfolioOptimizer
            self._portfolio_optimizer = PortfolioOptimizer()
            logger.info("포트폴리오 최적화 초기화 완료")
        except Exception as e:
            logger.warning("포트폴리오 최적화 초기화 실패: %s", e)
            self._portfolio_optimizer = None

    def _apply_persisted_evolution(self) -> None:
        """저장된 진화 상태를 실제 시스템에 적용한다 (v4.6 신규).

        이전엔 진화가 data/evolution_state.json에 저장만 되고,
        재시작 시 WEIGHTS/BUY_THRESHOLD가 기본값으로 리셋되어
        다음 진화 사이클(30분)까지 적용되지 않았다.

        이 메서드가 재시작 직후 저장된 조정값을 즉시 복원한다.
        """
        if not self._evolution:
            return

        try:
            # 1. 전략 조정값 (WEIGHTS, BUY_THRESHOLD 등)
            adjustments = self._evolution.get_strategy_adjustments()
            if adjustments and isinstance(self.strategy, ExpertStrategy):
                self.strategy.apply_adjustments(adjustments)
                logger.info("[재시작 복원] 저장된 전략 조정 적용: %s", list(adjustments.keys()))

            # 2. 리스크 파라미터 (stop_loss, trailing, take_profit, cooldown)
            risk = getattr(self._evolution.state, "risk_params", {}) or {}
            applied = []
            if "stop_loss_pct" in risk and hasattr(self.settings, "stop_loss_pct"):
                safe_stop = max(-2.5, float(risk["stop_loss_pct"]))
                self.settings.stop_loss_pct = safe_stop
                applied.append(f"손절={safe_stop:.1f}%")
            if "trailing_base" in risk:
                safe_trail = max(0.3, min(2.0, float(risk["trailing_base"])))
                self._trailing_base = safe_trail
                applied.append(f"트레일링={safe_trail:.1f}%")
            if "take_profit_pct" in risk and hasattr(self.settings, "take_profit_pct"):
                safe_tp = max(1.0, min(5.0, float(risk["take_profit_pct"])))
                self.settings.take_profit_pct = safe_tp
                applied.append(f"익절={safe_tp:.1f}%")
            if "cooldown_seconds" in risk:
                self._COOLDOWN_SECONDS = int(risk["cooldown_seconds"])
                applied.append(f"쿨다운={self._COOLDOWN_SECONDS}초")
            if applied:
                logger.info("[재시작 복원] 저장된 리스크 파라미터 적용: %s", " | ".join(applied))
        except Exception as e:
            logger.warning("진화 상태 복원 중 오류: %s", e)

    def _count_recent_trades(self) -> int:
        """마지막 진화 이후 누적 거래 수를 반환한다 (파라미터 진화용).

        v3.2: 재시작 시에도 전날 거래까지 포함하여 누적 카운팅.
        마지막 진화 시점의 총 매도 수를 기억하고, 현재 총 매도 수와 비교.
        """
        if not TRADES_FILE.exists():
            return 0
        try:
            trades = json.loads(TRADES_FILE.read_text(encoding="utf-8"))
            total_sells = len([t for t in trades if t.get("side") == "SELL"])
            last_evo_sells = 0
            if self._evolution and hasattr(self._evolution, 'state'):
                last_evo_sells = getattr(self._evolution.state, 'last_evolve_sell_count', 0)
            return total_sells - last_evo_sells
        except Exception:
            return 0

    def _count_recent_trades_for_code_evo(self) -> int:
        """마지막 코드 진화 이후 누적 거래 수를 반환한다.

        v3.2: 재시작 시에도 전날 거래까지 포함하여 누적 카운팅.
        """
        if not TRADES_FILE.exists():
            return 0
        try:
            trades = json.loads(TRADES_FILE.read_text(encoding="utf-8"))
            total_sells = len([t for t in trades if t.get("side") == "SELL"])
            last_code_evo_sells = 0
            if self._code_evolution and hasattr(self._code_evolution, 'state'):
                last_code_evo_sells = getattr(self._code_evolution.state, 'last_evolve_sell_count', 0)
            return total_sells - last_code_evo_sells
        except Exception:
            return 0

    _EVOLUTION_TIME_FALLBACK = 1800  # 30분마다 시간 기반 진화 (거래 없어도 실행)

    def _try_evolve(self):
        """진화 조건 충족 시 진화 사이클을 실행한다.

        v4.1: 거래 수 기반 + 시간 기반 듀얼 트리거.
        거래가 없어도 30분마다 진화 사이클이 돌아간다.
        """
        if not self._evolution or not self._evolution_enabled:
            return

        now = time.time()
        trade_trigger = self._evolution.should_evolve(self._trades_since_evolution)
        time_trigger = (now - self._last_evolution_time) >= self._EVOLUTION_TIME_FALLBACK

        if not trade_trigger and not time_trigger:
            if self._cycle_count % 10 == 0:
                remaining_time = max(0, self._EVOLUTION_TIME_FALLBACK - (now - self._last_evolution_time))
                logger.debug("파라미터 진화 대기: %d/%d건 또는 %.0f초 후",
                             self._trades_since_evolution, self._evolution.EVOLUTION_INTERVAL,
                             remaining_time)
            return

        trigger_reason = "거래" if trade_trigger else "시간(30분)"
        logger.info("진화 조건 충족 (%s, %d건 거래) - 진화 사이클 시작",
                     trigger_reason, self._trades_since_evolution)
        self._last_evolution_time = time.time()
        try:
            trades = self._load_trades()
            if not trades:
                if time_trigger:
                    logger.info("시간 기반 진화: 거래 기록 없이 기본 진화 실행")
                    trades = []  # 빈 거래 기록으로도 진화 시도
                else:
                    logger.warning("진화 스킵: 거래 기록이 비어있음")
                    return

            # 캔들 데이터 — 보유 종목 또는 최근 거래 종목에서 가져오기
            candles = None
            held_codes = list(self.order_manager.positions.keys())
            if not held_codes:
                # 보유 종목이 없으면 최근 거래 종목에서 캔들 확보
                recent_codes = [t.get("code") for t in reversed(trades) if t.get("code")]
                held_codes = recent_codes[:1]
            if held_codes:
                try:
                    # v4.2: 시장별 API 분기
                    if self.market != "KR":
                        candles = self.api.get_overseas_daily_chart(self.market, held_codes[0], count=60)
                    else:
                        candles = self.api.get_daily_chart(held_codes[0], count=60)
                except Exception:
                    pass

            result = self._evolution.evolve(trades, candles)
            self._trades_since_evolution = 0

            # v3.2: 현재 총 매도 수를 기록 후 다시 저장 (재시작 시 누적 카운팅용)
            total_sells = len([t for t in trades if t.get("side") == "SELL"])
            self._evolution.state.last_evolve_sell_count = total_sells
            self._evolution._save_state()

            # 진화 결과 적용
            adjustments = self._evolution.get_strategy_adjustments()
            if adjustments and isinstance(self.strategy, ExpertStrategy):
                self.strategy.apply_adjustments(adjustments)
                logger.info("진화 조정 적용: %s", adjustments)

            # v4.5: 리스크 파라미터 자동 진화 — 안전 한계 내에서만 적용
            risk = result.get("risk_params", {})
            if risk:
                applied = []
                if risk.get("stop_loss_pct") and hasattr(self.settings, 'stop_loss_pct'):
                    # 안전 한계: -2.5% 이상으로 절대 확대 불가
                    safe_stop = max(-2.5, risk["stop_loss_pct"])
                    self.settings.stop_loss_pct = safe_stop
                    applied.append(f"손절={safe_stop:.1f}%")
                if risk.get("trailing_base"):
                    # 안전 한계: 0.3~2.0% 범위
                    safe_trail = max(0.3, min(2.0, risk["trailing_base"]))
                    self._trailing_base = safe_trail
                    applied.append(f"트레일링={safe_trail:.1f}%")
                if risk.get("take_profit_pct") and hasattr(self.settings, 'take_profit_pct'):
                    # 안전 한계: 1.0~5.0% 범위
                    safe_tp = max(1.0, min(5.0, risk["take_profit_pct"]))
                    self.settings.take_profit_pct = safe_tp
                    applied.append(f"익절={safe_tp:.1f}%")
                if risk.get("cooldown_seconds"):
                    self._COOLDOWN_SECONDS = int(risk["cooldown_seconds"])
                    applied.append(f"쿨다운={risk['cooldown_seconds']}초")
                if applied:
                    logger.info("리스크 진화 적용 (안전 한계 내): %s", " | ".join(applied))

            # v4.9: StateManager에 진화 세대 동기화 (이전엔 누락되어 0 고정)
            try:
                self._state_mgr.state.evolution_generation = result["generation"]
                self._state_mgr._save_state()
            except Exception:
                pass

            logger.info(
                "진화 세대 #%d 완료: 적합도=%.1f (규칙 +%d -%d)",
                result["generation"], result["fitness"],
                result["rules_added"], result["rules_removed"],
            )
        except Exception as e:
            logger.error("진화 실행 실패: %s", e)

    _CODE_EVOLUTION_TIME_FALLBACK = 2400  # 40분마다 코드 진화 (거래 없어도 실행)

    def _try_code_evolution(self):
        """코드 진화 엔진을 실행한다 (프로그램 자체 진화).

        v4.1: 거래 수 기반 + 시간 기반 듀얼 트리거.
        """
        if not self._code_evolution:
            return

        now = time.time()
        trade_trigger = self._code_evolution.should_evolve(self._trades_since_code_evolution)
        time_trigger = (now - self._last_code_evolution_time) >= self._CODE_EVOLUTION_TIME_FALLBACK

        if not trade_trigger and not time_trigger:
            if self._cycle_count % 10 == 0:
                remaining_time = max(0, self._CODE_EVOLUTION_TIME_FALLBACK - (now - self._last_code_evolution_time))
                logger.debug("코드 진화 대기: %d/%d건 또는 %.0f초 후",
                             self._trades_since_code_evolution, self._code_evolution.CYCLE_INTERVAL,
                             remaining_time)
            return

        trigger_reason = "거래" if trade_trigger else "시간(40분)"
        logger.info("코드 진화 조건 충족 (%s, %d건) — 자체 진화 사이클 시작",
                     trigger_reason, self._trades_since_code_evolution)
        self._last_code_evolution_time = time.time()
        try:
            trades = self._load_trades()
            if not trades:
                if time_trigger:
                    logger.info("시간 기반 코드 진화: 거래 기록 없이 기본 진화 실행")
                    trades = []
                else:
                    logger.warning("코드 진화 스킵: 거래 기록이 비어있음")
                    return

            candles = None
            held_codes = list(self.order_manager.positions.keys())
            if not held_codes:
                recent_codes = [t.get("code") for t in reversed(trades) if t.get("code")]
                held_codes = recent_codes[:1]
            if held_codes:
                try:
                    # v4.2: 시장별 API 분기
                    if self.market != "KR":
                        candles = self.api.get_overseas_daily_chart(self.market, held_codes[0], count=60)
                    else:
                        candles = self.api.get_daily_chart(held_codes[0], count=60)
                except Exception:
                    pass

            result = self._code_evolution.run_evolution_cycle(trades, candles)
            self._trades_since_code_evolution = 0

            # v3.2: 현재 총 매도 수를 기록 후 다시 저장 (재시작 시 누적 카운팅용)
            total_sells = len([t for t in trades if t.get("side") == "SELL"])
            self._code_evolution.state.last_evolve_sell_count = total_sells
            self._code_evolution._save_state()

            status = result.get("status", "")

            # 코드 진화 결과를 전략에 반영
            if status == "evolved":
                evolved_config = self._code_evolution.get_active_config()
                self._apply_code_evolution(evolved_config)
                logger.info(
                    "코드 진화 사이클 #%d 완료: 개선 %d건 적용 (점수=%.1f, 약점=%d개)",
                    result.get("cycle", 0), result.get("modules_executed", 0),
                    result.get("diagnosis_score", 0), result.get("weaknesses", 0),
                )
            elif status == "analyzed":
                logger.info(
                    "코드 진화 사이클 #%d: 분석 완료 (점수=%.1f, 약점=%d개) — 조정 불필요",
                    result.get("cycle", 0),
                    result.get("diagnosis_score", 0), result.get("weaknesses", 0),
                )
            elif status == "rollback":
                logger.warning(
                    "코드 진화 사이클 #%d: 성능 악화 감지 → 이전 설정 롤백",
                    result.get("cycle", 0),
                )
            else:
                logger.info(
                    "코드 진화 사이클: %s — %s",
                    status, result.get("reason", ""),
                )
        except Exception as e:
            logger.error("코드 진화 실행 실패: %s", e)

    def _apply_code_evolution(self, config: dict):
        """코드 진화 결과를 전략에 반영한다."""
        if not isinstance(self.strategy, ExpertStrategy):
            return

        adjustments = {}

        # 진입 신호 조정 적용
        entry = config.get("entry_signals", {})
        if entry.get("buy_threshold_adj"):
            adjustments["buy_threshold_adj"] = entry["buy_threshold_adj"]

        # 매도 타이밍 조정 적용
        exit_cfg = config.get("exit_timing", {})
        if exit_cfg.get("trailing_base_adj"):
            adjustments["trailing_base_adj"] = exit_cfg["trailing_base_adj"]
        if exit_cfg.get("stop_loss_adj"):
            adjustments["stop_loss_adj"] = exit_cfg["stop_loss_adj"]

        # 레짐 전략 적용
        regime = config.get("regime_strategies", {})
        if regime.get("buy_threshold_adj"):
            adjustments["buy_threshold_adj"] = (
                adjustments.get("buy_threshold_adj", 0) + regime["buy_threshold_adj"])
        if regime.get("position_size_mult"):
            adjustments["position_size_mult"] = regime["position_size_mult"]

        # 지표 가중치 적용
        weights = config.get("indicator_weights", {})
        for ind, data in weights.items():
            if isinstance(data, dict) and "recommended_weight" in data:
                if ind in ("technical", "pattern", "sentiment", "market"):
                    adjustments[ind] = data["recommended_weight"] - 0.20

        if adjustments:
            self.strategy.apply_adjustments(adjustments)
            logger.info("코드 진화 조정 적용: %s", adjustments)

    def _load_trades(self) -> list[dict]:
        """거래 기록을 로드한다."""
        if not TRADES_FILE.exists():
            logger.warning("거래 기록 파일 없음: %s — 진화 불가", TRADES_FILE)
            return []
        try:
            return json.loads(TRADES_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error("거래 기록 파일 로드 실패: %s", e)
            return []

    # ──────────────────────────────────────────────
    # 시장 분석
    # ──────────────────────────────────────────────

    def _update_market_context(self) -> None:
        """시장 컨텍스트를 갱신하고 레짐 적응형 전략을 적용한다."""
        now = time.time()
        if now - self._market_ctx_updated < 60:
            return

        try:
            if self._market_analyzer:
                self._market_ctx = self._market_analyzer.analyze()
                if isinstance(self.strategy, ExpertStrategy):
                    self.strategy.set_market_context(self._market_ctx)
                self._market_ctx_updated = now

                # v2.9: 레짐 적응형 전략 전환
                if self._market_ctx and self._evolution:
                    regime = self._market_ctx.regime
                    self._regime_adj = self._evolution.get_regime_strategy(regime)
                    mode = self._regime_adj.get("mode", "normal")
                    if mode != "normal":
                        logger.info(
                            "🔄 레짐 전략 전환: %s → %s 모드 | %s",
                            regime, mode, self._regime_adj.get("reason", ""),
                        )
        except Exception as e:
            logger.warning("시장 컨텍스트 갱신 실패: %s", e)

    # ──────────────────────────────────────────────
    # 종목 선정 (전체 시장 스캔)
    # ──────────────────────────────────────────────

    # 해외 시장 인기 종목 (v4.2: 멀티마켓)
    _OVERSEAS_STOCKS = {
        "NASD": ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "TSLA", "META", "NFLX",
                 "AMD", "INTC", "AVGO", "QCOM", "COST", "PEP", "ADBE", "CRM",
                 "PYPL", "ABNB", "UBER", "COIN"],
        "NYSE": ["JPM", "V", "JNJ", "WMT", "PG", "UNH", "HD", "BAC",
                 "XOM", "CVX", "DIS", "NKE", "KO", "MCD", "GS", "BA",
                 "CAT", "IBM", "MMM", "GE"],
        "AMEX": ["SPY", "QQQ", "IWM", "GLD", "SLV", "XLE", "XLF", "XLK",
                 "VXX", "UVXY", "ARKK", "SOXL", "TQQQ", "SQQQ", "SPXL", "TNA"],
        "SEHK": ["00700", "09988", "01810", "03690", "09618", "02318", "00941",
                 "01024", "09999", "02020", "00005", "01299", "00388", "00669",
                 "03988", "01211"],
        "TKSE": ["7203", "6758", "9984", "6861", "7267", "8306", "9983",
                 "6501", "7751", "4502", "6902", "7974", "8035", "9432",
                 "6367", "4063"],
    }

    def _select_stocks_wide(self) -> list[str]:
        """전체 시장을 스캔하여 매매 후보를 선정한다.

        v4.2: 멀티마켓 — 해외 시장은 인기 종목 리스트 사용.
        국내 시장은 거래량+거래대금+모멘텀+하락반등 합산.
        """
        # 해외 시장이면 인기 종목 리스트 반환
        if self.market != "KR":
            stocks = self._OVERSEAS_STOCKS.get(self.market, [])
            if stocks:
                logger.info("[%s] 해외 종목 %d개 로드", self.market, len(stocks))
                return stocks
            return []
        now = time.time()
        if self._stock_cache and (now - self._stock_cache_time) < self._STOCK_CACHE_TTL:
            return self._stock_cache

        try:
            seen = set()
            candidates = []  # (code, score)

            # 1. 거래량 상위 50개
            try:
                vol_rank = self.api.get_volume_rank(count=50)
                for i, s in enumerate(vol_rank):
                    code = s["stock_code"]
                    if code not in seen and self._is_valid_stock(s):
                        seen.add(code)
                        candidates.append((code, 50 - i))  # 상위일수록 높은 점수
            except Exception as e:
                logger.warning("거래량 순위 조회 실패: %s", e)

            time.sleep(0.3)

            # 2. 거래대금 상위 50개
            try:
                amount_rank = self.api.get_market_cap_rank(count=50)
                for i, s in enumerate(amount_rank):
                    code = s["stock_code"]
                    if self._is_valid_stock(s):
                        if code in seen:
                            # 이미 있으면 점수 추가 (중복 = 더 인기)
                            for j, (c, sc) in enumerate(candidates):
                                if c == code:
                                    candidates[j] = (c, sc + 30 - i)
                                    break
                        else:
                            seen.add(code)
                            candidates.append((code, 30 - i))
            except Exception as e:
                logger.warning("거래대금 순위 조회 실패: %s", e)

            time.sleep(0.3)

            # 3. 소폭 상승 종목 30개 (초기 모멘텀 — 핵심 전략)
            # v4.4: 1~3% 상승 = 모멘텀 시작, 급등 아직 아닌 구간
            try:
                up_rank = self.api.get_fluctuation_rank(direction="up", count=30)
                for i, s in enumerate(up_rank):
                    code = s["stock_code"]
                    cr = s.get("change_rate", 0)
                    if self._is_valid_stock(s) and 0.5 < cr < 3.0:  # v4.4: 0.3~4→0.5~3 (더 좁은 범위)
                        if code in seen:
                            for j, (c, sc) in enumerate(candidates):
                                if c == code:
                                    candidates[j] = (c, sc + 20)  # v4.4: 15→20 (모멘텀 중시)
                                    break
                        else:
                            seen.add(code)
                            candidates.append((code, 20 - i))
            except Exception as e:
                logger.warning("상승률 순위 조회 실패: %s", e)

            time.sleep(0.3)

            # 4. 하락 반등 후보 20개 (v4.4: 축소 — 떨어지는 칼날 위험)
            # 하락 반등은 위험이 크므로 비중 축소, 소폭 하락만
            try:
                down_rank = self.api.get_fluctuation_rank(direction="down", count=20)
                for i, s in enumerate(down_rank):
                    code = s["stock_code"]
                    cr = s.get("change_rate", 0)
                    price = s.get("price", 0)
                    max_price = min(self.settings.max_buy_amount, 300000)
                    # v4.4: -1%~-2.5%만 (소폭 하락만, 급락 제외)
                    if -2.5 < cr < -1 and 3000 < price < max_price:
                        if code in seen:
                            for j, (c, sc) in enumerate(candidates):
                                if c == code:
                                    candidates[j] = (c, sc + 10)  # v4.4: 25→10 (비중 축소)
                                    break
                        else:
                            seen.add(code)
                            candidates.append((code, 10))  # v4.4: 25→10
            except Exception as e:
                logger.warning("하락률 순위 조회 실패: %s", e)

            # 점수 기준 정렬 → 상위 30개 선정 (v4.4: 50→30 집중)
            candidates.sort(key=lambda x: x[1], reverse=True)
            result = [code for code, _ in candidates[:30]]

            if result:
                self._stock_cache = result
                self._stock_cache_time = now
                logger.info(
                    "종목 선정: %d개 후보 (거래량+거래대금+모멘텀+하락반등 합산, 총 스캔 %d개)",
                    len(result), len(seen),
                )
            return result

        except Exception as e:
            logger.error("종목 선정 실패: %s", e)
            return self._stock_cache or []

    def _is_valid_stock(self, stock: dict) -> bool:
        """매매 적합 종목인지 검증한다.

        v4.4: 더 엄격한 필터 — 안정적인 종목만 허용.
        - 가격: 3,000~max_buy_amount (너무 싼 주식은 변동성 과다)
        - 등락률: -5% ~ +8% (극단적 변동 제외)
        """
        price = stock.get("price", 0)
        change_rate = stock.get("change_rate", 0)
        max_price = min(self.settings.max_buy_amount, 500000)
        return (
            3000 < price <= max_price      # v4.4: 1000→3000 (페니주 제외)
            and -5 < change_rate < 8       # v4.4: -8~15 → -5~8 (극단 제외)
        )

    def _score_stock_value(self, stock_data: dict) -> float:
        """종목의 가치투자 점수를 계산한다 (버핏 원칙).

        Returns:
            가치 점수 (-1.0 ~ +1.0). 높을수록 매수 매력적.
        """
        per = stock_data.get("per", 0)
        pbr = stock_data.get("pbr", 0)
        score = 0.0

        if 0 < per < 10:
            score += 0.3
        elif 0 < per < 15:
            score += 0.15
        elif per > 40:
            score -= 0.2
        elif per < 0:
            score -= 0.4  # 적자 기업 패널티

        if 0 < pbr < 1.0:
            score += 0.2
        elif 0 < pbr < 1.5:
            score += 0.1
        elif pbr > 5.0:
            score -= 0.15

        return max(-1.0, min(1.0, score))

    # ──────────────────────────────────────────────
    # 매매 사이클
    # ──────────────────────────────────────────────

    def _run_cycle(self, stocks: list[str]) -> None:
        """하나의 매매 사이클을 실행한다."""
        self._cycle_count += 1

        # 1. 보유 종목 가격 갱신
        self.order_manager.update_prices()

        # 1.5 v3.2: 포트폴리오 최적화 갱신 (10사이클마다)
        if self._portfolio_optimizer and self._cycle_count % 10 == 1:
            try:
                self._portfolio_optimizer.update(
                    self.order_manager.positions,
                    self.order_manager.trade_history,
                )
            except Exception as e:
                logger.debug("포트폴리오 최적화 갱신 실패: %s", e)

        # v4.8: 계좌 보호 상태 체크 (방어 모드 / 자동 정지 전환)
        self._check_account_protection()

        # v4.8: 오후 강제 청산 체크 (14시 이후 손실 포지션 정리)
        self._check_afternoon_force_sell()

        # v4.9: 마감전 세션이면 약세 포지션 적극 정리
        session_profile = get_profile()
        if session_profile.force_defensive_sell and self.order_manager.positions:
            self._force_defensive_sell(session_profile)

        # 2. 리스크 관리 (최우선)
        self._check_risk_management()

        # 3. 종목별 전략 분석
        for stock_code in stocks:
            if not self._running:
                break

            try:
                self._analyze_and_trade(stock_code)
                time.sleep(0.2)
            except Exception as e:
                logger.error("[%s] 분석 중 오류: %s", stock_code, e)

        # 4. 진화 체크 — 매 사이클마다 (v3.2: 5사이클→매사이클, 빠른 적응)
        self._try_evolve()
        self._try_code_evolution()

        # 5. v3.2: 모멘텀 돌파 스캔 (3사이클마다 — 거래량 급등 종목 자동 포착)
        if self._cycle_count % 3 == 0 and self.order_manager.can_buy():
            self._scan_momentum_breakouts(stocks)

        if self._cycle_count % 10 == 0:
            self._log_status()

    def _is_blocked_today(self, stock_code: str) -> bool:
        """당일 손실 매도한 종목인지 확인 (v4.7 whipsaw 방지).

        날짜가 바뀐 항목은 자동 정리.
        """
        today = datetime.now().strftime("%Y-%m-%d")
        # 날짜 바뀐 항목 정리
        stale = [c for c, d in self._loss_stocks_today.items() if d != today]
        for c in stale:
            del self._loss_stocks_today[c]
        return stock_code in self._loss_stocks_today

    # ── v4.8 계좌 보호 메서드 ────────────────────────────────────────────────

    def _update_daily_pnl(self, realized_profit: float) -> None:
        """매도 완료 시 일일 손익 누적. 날짜 바뀌면 초기화."""
        today = datetime.now().strftime("%Y-%m-%d")
        if self._daily_pnl_date != today:
            self._daily_pnl_date = today
            self._daily_realized_pnl = 0.0
            self._defense_mode = False
            self._auto_stopped = False
            self._consecutive_losses = 0
            logger.info("📅 일일 계좌 보호 카운터 초기화 (새 거래일: %s)", today)
        self._daily_realized_pnl += realized_profit

    def _check_account_protection(self) -> None:
        """매 사이클마다 호출. 일일 손실 한도·연속 손실 기준으로 상태 전환."""
        capital = self.settings.initial_capital
        if capital <= 0:
            return
        daily_pct = (self._daily_realized_pnl / capital) * 100

        # 자동 정지 복구 체크 — 손실의 50% 회복 시 재개
        if self._auto_stopped:
            loss_at_stop = self._stop_entry_pnl  # 음수
            recovered = self._daily_realized_pnl - loss_at_stop  # 얼마나 회복했나
            needed = abs(loss_at_stop) * self.settings.recovery_threshold
            if recovered >= needed:
                self._auto_stopped = False
                self._consecutive_losses = 0
                logger.info(
                    "✅ 자동 정지 해제: 손실의 %.0f%% 회복 (회복액=%+,.0f원)",
                    self.settings.recovery_threshold * 100, recovered,
                )

        # 방어 모드 체크 — 일일 손실 한도
        limit = self.settings.daily_loss_limit  # 음수 (e.g. -3.0)
        was_defense = self._defense_mode
        self._defense_mode = daily_pct <= limit
        if self._defense_mode and not was_defense:
            logger.warning(
                "🛡 방어 모드 진입: 일일 손익=%.1f%% (한도 %.1f%%) "
                "→ 포지션 크기 50%% 축소, 진입 조건 강화",
                daily_pct, limit,
            )

    def _can_enter_new_position(self) -> bool:
        """신규 매수 가능 여부. 자동 정지 또는 방어 모드 심화 시 차단."""
        if self._auto_stopped:
            return False
        # 방어 모드에서도 손익비 1:2 기대값 충족 종목은 허용
        # (실제 종목 필터는 _analyze_and_trade에서 수행)
        return True

    def _is_surge_chasing(self, stock_code: str, current_change_rate: float) -> bool:
        """급등 추격 금지. 당일 변동률 초과 OR VI 발동 직후 True 반환."""
        # 당일 급등 추격 금지
        if current_change_rate > self.settings.max_chase_rate:
            logger.debug(
                "⛔ 급등 추격 금지: %s 당일+%.1f%% > 기준+%.1f%%",
                stock_code, current_change_rate, self.settings.max_chase_rate,
            )
            return True

        # VI 감지: 직전 체크 대비 변동률이 3% 이상 급변
        prev = self._prev_change_rate.get(stock_code)
        if prev is not None and abs(current_change_rate - prev) >= 3.0:
            logger.debug(
                "⛔ VI 감지 진입 금지: %s 변동률 %.1f%%→%.1f%% (급변 %.1f%%)",
                stock_code, prev, current_change_rate,
                abs(current_change_rate - prev),
            )
            return True
        self._prev_change_rate[stock_code] = current_change_rate
        return False

    # ── v4.9 세션 + 확신도 기반 단일 진입 게이트 ─────────────────────────────

    def _reset_daily_entries_if_new_day(self) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        if self._daily_entry_date != today:
            self._daily_entry_date = today
            self._daily_entries_total = 0
            self._session_entries = {}

    def _session_has_budget(self, session_name: str, session_max: int) -> bool:
        """세션 내 신규 진입 여유가 있는지 확인."""
        self._reset_daily_entries_if_new_day()
        used = self._session_entries.get(session_name, 0)
        if used >= session_max:
            return False
        if self._daily_entries_total >= self._daily_trade_budget:
            return False
        return True

    def _record_entry(self, session_name: str) -> None:
        self._reset_daily_entries_if_new_day()
        self._daily_entries_total += 1
        self._session_entries[session_name] = self._session_entries.get(session_name, 0) + 1

    def _try_enter_position(
        self,
        stock_code: str,
        stock_name: str,
        price_data: dict,
        reason: str,
        analysis=None,
        atr_value: float = 0.0,
        target_price: int = 0,
        estimated_upside: float = 0.0,
        volume_ratio_override: float = 0.0,
    ) -> bool:
        """v4.9: 모든 진입 경로가 통과해야 하는 단일 게이트.

        `_analyze_and_trade`와 `_scan_momentum_breakouts` 둘 다 여기를 통해
        동일한 필터·확신도 계산·크기 결정을 받는다.

        Returns: 매수 실행되었으면 True.
        """
        price = price_data.get("price", 0) or 0
        if price <= 0:
            return False

        # 공통 필터 (1): 보유 중이면 skip
        if stock_code in self.order_manager.positions:
            return False
        if not self.order_manager.can_buy():
            return False

        # 공통 필터 (2): 당일 손실 차단 / 쿨다운
        if self._is_blocked_today(stock_code):
            return False
        if stock_code in self._cooldown_stocks:
            if time.time() < self._cooldown_stocks[stock_code]:
                return False

        # 공통 필터 (3): 계좌 보호 — 자동 정지 중이면 차단
        if not self._can_enter_new_position():
            return False

        # 세션 필터 (4): 세션 프로필 로드
        profile = get_profile()
        session_name = profile.session.value
        if not profile.allow_new_entry:
            logger.debug("⛔ 세션 차단: %s — %s", stock_code, profile.reason)
            return False

        # 세션 필터 (5): 세션/일일 진입 예산
        if not self._session_has_budget(session_name, profile.max_new_entries):
            used = self._session_entries.get(session_name, 0)
            logger.debug(
                "⛔ 진입 예산 소진: %s 세션=%s (%d/%d) 일일=%d/%d",
                stock_code, session_name, used, profile.max_new_entries,
                self._daily_entries_total, self._daily_trade_budget,
            )
            return False

        # 가격 필터 (6): 1주도 못 사는 종목은 skip
        if price > self.settings.max_buy_amount:
            return False

        # 급등 추격 / VI 필터 (7)
        change_rate = price_data.get("change_rate", 0.0)
        if self._is_surge_chasing(stock_code, change_rate):
            return False

        # 장마감 15분 전 차단 (8)
        from trading.market_scheduler import MARKETS
        mkt = MARKETS.get(self.market)
        if mkt:
            now_str = datetime.now().strftime("%H:%M")
            if not mkt.crosses_midnight and now_str >= mkt.liquidate_time:
                return False

        # 연속 손실 보호 (9)
        recent_sells = [t for t in self.order_manager.trade_history[-5:]
                        if t.side == "SELL"]
        if len(recent_sells) >= 3:
            recent_losses = [t for t in recent_sells[-3:] if t.profit_loss <= 0]
            if len(recent_losses) >= 3:
                logger.info("⛔ 연속 손실 보호: 매수 보류 (%s)", stock_code)
                return False

        # 체결강도 필터 (10, KR만)
        contract_strength = 0.0
        if self.market == "KR":
            contract_strength = self._get_contract_strength(stock_code)
            min_cs = self.settings.min_contract_strength
            if contract_strength > 0 and contract_strength < min_cs:
                logger.debug(
                    "⛔ 체결강도 부족: %s %.0f < %.0f",
                    stock_code, contract_strength, min_cs,
                )
                return False

        # ── 확신도 계산 ──────────────────────────────────────────────────────
        expert_total = 0.0
        expert_conf = 0.0
        trend_score = 0.0
        volume_ratio = volume_ratio_override or 1.0
        macd_cross = ""
        thesis: dict = {}

        if analysis is not None:
            expert_total = float(getattr(analysis, "total_score", 0.0))
            expert_conf = float(getattr(analysis, "confidence", 0.0))
            if analysis.technical:
                t = analysis.technical
                trend_score = float(getattr(t, "trend_score", 0.0))
                if not volume_ratio_override:
                    volume_ratio = float(getattr(t, "volume_ratio", 1.0))
                macd_cross = str(getattr(t, "macd_cross", "") or "")
                thesis = {
                    "rsi": getattr(t, "rsi", 50.0),
                    "trend_score": trend_score,
                    "momentum_score": getattr(t, "momentum_score", 0.0),
                    "volume_ratio": volume_ratio,
                    "macd_cross": macd_cross,
                    "regime": self._market_ctx.regime if self._market_ctx else "",
                }

        # 브레이크아웃 경로(analysis=None)일 때도 돌파 강도만으로 확신도 계산
        if analysis is None and volume_ratio_override:
            trend_score = min(0.5, max(0.0, (volume_ratio_override - 2.0) * 0.15))
            expert_total = min(0.45, max(0.0, (volume_ratio_override - 2.0) * 0.1))
            expert_conf = 0.5

        # v4.9: Q-Learning 보정 — 과거 유사 상태에서의 수익률 학습 반영
        # 매수 유리 상태면 expert_total 보강, 매도 유리 상태면 억제
        if self._q_agent and thesis:
            try:
                q_mod = self._q_agent.get_confidence_modifier(thesis)
                if q_mod != 0:
                    expert_total += q_mod
                    logger.debug(
                        "Q-Learning 보정: %s → expert_total %+.3f (누적=%.3f)",
                        stock_code, q_mod, expert_total,
                    )
            except Exception:
                pass

        session_fit = session_fit_score(session_name, volume_ratio, trend_score)
        conviction = calc_conviction(
            expert_total_score=expert_total,
            expert_confidence=expert_conf,
            trend_score=trend_score,
            volume_ratio=volume_ratio,
            contract_strength=contract_strength,
            session_fit=session_fit,
            macd_cross=macd_cross,
        )

        # 세션별 최소 확신도 + 글로벌 최소치 모두 충족해야 진입
        if not conviction.should_enter(profile.min_conviction):
            logger.info(
                "⛔ 확신도 부족: %s(%s) %s vs 세션최소=%.0f",
                stock_name, stock_code, conviction.describe(), profile.min_conviction,
            )
            return False

        # 방어 모드에서는 최소 확신도 +10 가산 (더 엄격하게)
        if self._defense_mode and conviction.total < (profile.min_conviction + 10):
            logger.info(
                "🛡 방어 모드 추가 필터: %s %s < %.0f",
                stock_code, conviction.describe(), profile.min_conviction + 10,
            )
            return False

        # 방어 모드 손익비 1:2 체크 (기존 v4.8 로직 유지)
        if self._defense_mode:
            stop_distance = abs(self.settings.stop_loss_pct / 100 * price)
            upside_distance = (target_price - price) if target_price > price else 0
            if upside_distance < stop_distance * 2:
                logger.info(
                    "🛡 방어 모드: %s 손익비 부족 (상승=%.0f < 필요=%.0f) → 스킵",
                    stock_code, upside_distance, stop_distance * 2,
                )
                return False

        # ── 실행 ────────────────────────────────────────────────────────────
        conviction_size_mult = conviction.size_mult()
        session_size_mult = profile.size_mult
        final_size_mult = conviction_size_mult * session_size_mult
        if self._defense_mode:
            final_size_mult *= 0.5  # 방어 모드는 추가로 반토막

        logger.info(
            "▶ 매수 실행 [v4.9]: %s(%s) %s원 | %s | 세션=%s 크기=%.2fx | %s",
            stock_name, stock_code, f"{price:,}",
            conviction.describe(), session_name, final_size_mult, reason,
        )
        if thesis:
            logger.info(
                "  매수근거: RSI=%.0f 추세=%.2f 모멘텀=%.2f 거래량=%.1fx MACD=%s",
                thesis.get("rsi", 50), thesis.get("trend_score", 0),
                thesis.get("momentum_score", 0), thesis.get("volume_ratio", 1.0),
                thesis.get("macd_cross") or "-",
            )

        ok = self.order_manager.execute_buy(
            stock_code, stock_name, price, reason,
            strength=conviction.total / 100.0,  # 0~1 정규화
            atr=atr_value,
            target_price=target_price,
            estimated_upside=estimated_upside,
            per=price_data.get("per", 0),
            pbr=price_data.get("pbr", 0),
            size_mult=final_size_mult,
            thesis=thesis,
        )
        if ok:
            self._record_entry(session_name)
            # v4.9: Q-Learning 매수 기록 — 매도 시 학습에 필수
            # (이전 버전에서 누락되어 Q-Learning이 완전히 죽어있었음)
            if self._q_agent and thesis:
                try:
                    self._q_agent.record_buy(stock_code, thesis)
                except Exception as e:
                    logger.debug("Q-Learning record_buy 실패: %s", e)
        return ok

    def _force_defensive_sell(self, profile) -> None:
        """v4.9: 마감전 세션에서 약세 포지션을 적극 정리한다.

        SESSION_PROFILES에서 force_defensive_sell=True인 세션(PRE_CLOSE)에서
        보합·약손실 포지션을 장 마감 전에 정리하여 익일 리스크 제거.
        """
        for code, pos in list(self.order_manager.positions.items()):
            if pos.profit_rate < 0.3:
                try:
                    pr = pos.profit_rate
                    logger.info(
                        "🔶 세션 방어매도: %s(%s) 수익률=%.2f%% | 세션=%s — %s",
                        pos.stock_name, code, pr, profile.session.value, profile.reason,
                    )
                    self.order_manager.execute_sell(
                        code, f"세션방어({profile.session.value},{pr:.1f}%)",
                    )
                    self._on_trade_completed(code, pr, "SELL")
                except Exception as e:
                    logger.error("[%s] 세션 방어매도 실패: %s", code, e)

    def _check_afternoon_force_sell(self) -> None:
        """14시 이후 약세 포지션 강제 청산."""
        now_str = datetime.now().strftime("%H:%M")
        if now_str < self.settings.afternoon_force_sell_time:
            return
        threshold = self.settings.afternoon_force_sell_pct
        for code, pos in list(self.order_manager.positions.items()):
            if pos.profit_rate <= threshold:
                try:
                    pr = pos.profit_rate
                    logger.info(
                        "⏰ 오후 강제 청산: %s(%s) 수익률=%.2f%% (기준 %.1f%%, %s 이후)",
                        pos.stock_name, code, pr,
                        threshold, self.settings.afternoon_force_sell_time,
                    )
                    self.order_manager.execute_sell(
                        code, f"오후강제청산({pr:.1f}%,{now_str})"
                    )
                    self._on_trade_completed(code, pr, "SELL")
                except Exception as e:
                    logger.error("[%s] 오후 강제 청산 실패: %s", code, e)

    def _collect_position_snapshots(self) -> dict[str, dict]:
        """보유 종목들의 현재 기술적 스냅샷을 수집한다 (v4.7).

        논지 기반 매도(check_thesis_broken)에서 사용.
        각 종목의 최신 RSI/추세/모멘텀/거래량/MACD를 반환.
        """
        snapshots = {}
        if not isinstance(self.strategy, ExpertStrategy):
            return snapshots
        if not self.order_manager.positions:
            return snapshots

        for code in list(self.order_manager.positions.keys()):
            try:
                # v4.2: 시장별 API 분기
                if self.market != "KR":
                    cp = self.api.get_overseas_price(self.market, code)
                    cs = self.api.get_overseas_daily_chart(self.market, code, count=30)
                else:
                    cp = self.api.get_current_price(code)
                    cs = self.api.get_minute_chart(code, period="3")
                    if not cs or len(cs) < 10:
                        cs = self.api.get_daily_chart(code, count=30)
                if not cp or not cs or len(cs) < 10:
                    continue

                sorted_candles = self.strategy._ensure_ascending(cs)
                t = self.strategy.technical.analyze(sorted_candles, cp.get("price", 0))
                snapshots[code] = {
                    "rsi": float(t.rsi),
                    "trend_score": float(t.trend_score),
                    "momentum_score": float(t.momentum_score),
                    "volume_ratio": float(t.volume_ratio),
                    "macd_cross": str(t.macd_cross or ""),
                }
            except Exception as e:
                logger.debug("[%s] 스냅샷 수집 실패: %s", code, e)
        return snapshots

    def _check_risk_management(self) -> None:
        """리스크 관리: 상승여력 재분석, 트레일링 스탑, 손절, 장마감.

        v2.7: 보유 종목의 상승여력을 주기적으로 재분석하여
        목표가를 동적으로 갱신하고, 추세가 소진될 때까지 홀딩한다.
        """

        # ── 0. 보유 종목 상승여력 재분석 (3사이클마다) ──
        if isinstance(self.strategy, ExpertStrategy) and self._cycle_count % 3 == 0:
            self._reassess_positions()

        # ── 1. 트레일링 스탑 (v4.9: 0.1%부터 수익 보호) ──
        for code in self.order_manager.check_trailing_stop(trail_pct=self._trailing_base):
            try:
                pos = self.order_manager.positions.get(code)
                if not pos:
                    continue
                pr = pos.profit_rate
                self.order_manager.execute_sell(code, f"트레일링스탑({pr:.1f}%)")
                self._on_trade_completed(code, pr, "SELL")
            except Exception as e:
                logger.error("[%s] 트레일링스탑 매도 실패: %s", code, e)

        # ── 1.5 시간 기반 마이크로 익절 (v4.9 신규) ──
        # 5분 이상 보유 + 수익 0.1% 이상 → 모멘텀 약화 시 수익 확정
        # 소액 포지션에서 트레일링/목표가 도달 불가능한 데드존 해소
        self._check_time_based_micro_exit()

        # ── 2. 목표가 도달 확인 (분석 기반 익절) ──
        for code, pos in list(self.order_manager.positions.items()):
            if pos.target_price > 0 and pos.current_price >= pos.target_price:
                try:
                    pr = pos.profit_rate
                    logger.info(
                        "🎯 목표가 도달: %s(%s) 현재=%s 목표=%s 수익률=%.1f%%",
                        pos.stock_name, code,
                        f"{pos.current_price:,}", f"{pos.target_price:,}",
                        pr,
                    )
                    self.order_manager.execute_sell(code, f"목표가도달({pos.target_price:,}원)")
                    self._on_trade_completed(code, pr, "SELL")
                except Exception as e:
                    logger.error("[%s] 목표가 매도 실패: %s", code, e)

        # ── 2.5 논지 기반 매도 (v4.7 신규): "왜 샀는지"의 근거가 깨졌는가? ──
        # 매수 시 판단한 근거(RSI/추세/모멘텀/거래량/MACD)가 여전히 유효한지 확인.
        # 근거 깨짐 + 손실 → 즉시 매도 (잘못된 판단 빠른 정정)
        # v4.9: 논지 깨짐 → 수익/손실 무관하게 즉시 매도
        # 매수 근거가 무효화됐으면 더 보유할 이유가 없다
        thesis_snaps = self._collect_position_snapshots()
        if thesis_snaps:
            for code, broken_reason in self.order_manager.check_thesis_broken(thesis_snaps):
                try:
                    pos = self.order_manager.positions.get(code)
                    if not pos:
                        continue
                    pr = pos.profit_rate
                    if pr >= 0:
                        logger.info(
                            "🟡 논지깨짐+수익확정: %s(%s) 수익률=%.2f%% | %s",
                            pos.stock_name, code, pr, broken_reason,
                        )
                    else:
                        logger.info(
                            "🔴 논지깨짐+손절: %s(%s) 수익률=%.2f%% | %s",
                            pos.stock_name, code, pr, broken_reason,
                        )
                    self.order_manager.execute_sell(
                        code, f"논지깨짐({pr:.1f}%|{broken_reason})"
                    )
                    self._on_trade_completed(code, pr, "SELL")
                except Exception as e:
                    logger.error("[%s] 논지 매도 실패: %s", code, e)

        # ── 3. 하드 손절 (v4.7: -2.5%→-4% 완화, 진짜 위험할 때만 작동하는 안전망) ──
        # 논지 체크가 1차 방어선. 하드 손절은 극단적 낙폭에서만 발동하는 최종 안전망.
        hard_stop_pct = getattr(self.settings, "stop_loss_pct", -4.0)
        for code in self.order_manager.check_stop_loss(stop_loss_pct=hard_stop_pct):
            try:
                pos = self.order_manager.positions.get(code)
                if not pos:
                    continue
                pr = pos.profit_rate
                logger.warning(
                    "⚠ 하드 손절 실행: %s(%s) 수익률=%.2f%% → 극단 낙폭 차단",
                    pos.stock_name, code, pr,
                )
                self.order_manager.execute_sell(code, f"하드손절({pr:.1f}%)")
                self._on_trade_completed(code, pr, "SELL")
            except Exception as e:
                logger.error("[%s] 하드 손절 매도 실패: %s", code, e)

        # ── 3.5 익절 매도 (v4.6: 진화된 settings.take_profit_pct 실제 반영) ──
        for code in self.order_manager.check_take_profit(
            take_profit_pct=getattr(self.settings, "take_profit_pct", 1.2)
        ):
            try:
                pos = self.order_manager.positions.get(code)
                if not pos:
                    continue
                pr = pos.profit_rate
                logger.info(
                    "💰 익절 실행: %s(%s) 수익률=%.2f%% → 수익 확정",
                    pos.stock_name, code, pr,
                )
                self.order_manager.execute_sell(code, f"익절({pr:.1f}%)")
                self._on_trade_completed(code, pr, "SELL")
            except Exception as e:
                logger.error("[%s] 익절 매도 실패: %s", code, e)

        # ── 3.7 시간 기반 정리 (v4.7: 45분, 손실 -1.5% 이상일 때만) ──
        # 논지가 유효해도 오랫동안 진행 없으면 기회비용 고려해서 정리.
        # 단, 진짜 의미 있는 손실(-1.5%+)일 때만. 작은 흔들림은 버틴다.
        for code, pos in list(self.order_manager.positions.items()):
            try:
                buy_time = datetime.strptime(pos.buy_time, "%H:%M:%S")
                now = datetime.now()
                buy_dt = now.replace(hour=buy_time.hour, minute=buy_time.minute,
                                     second=buy_time.second)
                elapsed_min = (now - buy_dt).total_seconds() / 60
                # v4.7: 20분/-0.3% → 45분/-1.5% (논지 유효 시 충분한 시간 보장)
                if elapsed_min >= 45 and pos.profit_rate < -1.5:
                    pr = pos.profit_rate
                    logger.info(
                        "⏰ 시간 정리: %s(%s) %.0f분 보유, 수익률=%.2f%% → 기회비용 회수",
                        pos.stock_name, code, elapsed_min, pr,
                    )
                    self.order_manager.execute_sell(code, f"시간정리({elapsed_min:.0f}분,{pr:.1f}%)")
                    self._on_trade_completed(code, pr, "SELL")
            except (ValueError, TypeError):
                pass

        # ── 4. 장마감 처리 (v4.2: 시장별 마감 시간 자동 판단) ──
        from trading.market_scheduler import MARKETS, is_liquidation_time as _is_liq
        now_str = datetime.now().strftime("%H:%M")
        market_info = MARKETS.get(self.market)
        should_liquidate = _is_liq(market_info, now_str) if market_info else now_str >= "15:20"
        if should_liquidate:
            remaining = list(self.order_manager.positions.keys())
            for code in remaining:
                try:
                    pos = self.order_manager.positions[code]
                    pr = pos.profit_rate
                    if pr >= 0:
                        reason = f"장마감익절({pr:.1f}%)"
                    else:
                        reason = f"장마감정리({pr:.1f}%)"
                    logger.info(
                        "장마감 청산: %s(%s) 수익률=%.2f%% → %s",
                        pos.stock_name, code, pr, reason,
                    )
                    self.order_manager.execute_sell(code, reason)
                    self._on_trade_completed(code, pr, "SELL")
                except Exception as e:
                    logger.error("[%s] 장마감 처리 실패: %s", code, e)

    def _check_time_based_micro_exit(self) -> None:
        """v4.9: 시간 기반 마이크로 익절 — 데드존 해소.

        조건: 5분+ 보유 AND 수익 0.1%~1.0% AND 최고가 대비 하락 중
        → 모멘텀이 꺾인 소액 수익을 확정한다.
        트레일링스탑에 잡히지 않는 "오르다 멈춘" 포지션을 처리.
        """
        for code, pos in list(self.order_manager.positions.items()):
            try:
                if pos.profit_rate < 0.1 or pos.profit_rate > 1.0:
                    continue

                buy_time = datetime.strptime(pos.buy_time, "%H:%M:%S")
                now = datetime.now()
                buy_dt = now.replace(
                    hour=buy_time.hour, minute=buy_time.minute,
                    second=buy_time.second)
                elapsed_min = (now - buy_dt).total_seconds() / 60
                if elapsed_min < 5:
                    continue

                if pos.highest_price <= 0 or pos.current_price <= 0:
                    continue
                drop_from_high = (
                    (pos.highest_price - pos.current_price) / pos.highest_price * 100
                )
                if drop_from_high < 0.15:
                    continue

                pr = pos.profit_rate
                logger.info(
                    "💫 마이크로익절: %s(%s) %.0f분보유 수익=%.2f%% 고점대비-%.2f%%",
                    pos.stock_name, code, elapsed_min, pr, drop_from_high,
                )
                self.order_manager.execute_sell(
                    code, f"마이크로익절({pr:.2f}%,{elapsed_min:.0f}분)"
                )
                self._on_trade_completed(code, pr, "SELL")
            except (ValueError, TypeError):
                pass

    def _reassess_positions(self) -> None:
        """보유 종목의 상승여력을 재분석하여 목표가를 갱신한다.

        추세가 살아있으면 목표가를 상향 조정하고,
        추세가 소진되면 즉시 매도한다.
        """
        if not isinstance(self.strategy, ExpertStrategy):
            return

        for code, pos in list(self.order_manager.positions.items()):
            # v4.9: 손실 종목도 재분석 (추세 소진 시 빠른 탈출)
            # 이전엔 수익 종목만 재분석하여 손실 포지션이 방치됨

            try:
                # v4.2: 시장별 API 분기
                if self.market != "KR":
                    current_price = self.api.get_overseas_price(self.market, code)
                    if not current_price:
                        continue
                    candles = self.api.get_overseas_daily_chart(self.market, code, count=60)
                    if not candles:
                        continue
                else:
                    current_price = self.api.get_current_price(code)
                    if not current_price:
                        continue
                    candles = self.api.get_minute_chart(code, period="3")
                    if len(candles) < 20:
                        candles = self.api.get_daily_chart(code, count=60)
                    if not candles:
                        continue

                upside = self.strategy.estimate_upside(code, candles, current_price)

                # 목표가 갱신 (상향만 — 하향은 안 함)
                if upside["target_price"] > pos.target_price:
                    old = pos.target_price
                    pos.target_price = upside["target_price"]
                    pos.estimated_upside = upside["upside_pct"]
                    logger.info(
                        "📈 [%s] 목표가 상향: %s→%s원 (여력=%.1f%%)",
                        pos.stock_name,
                        f"{old:,}" if old else "미정",
                        f"{pos.target_price:,}",
                        upside["upside_pct"],
                    )

                # 추세 소진 시 매도 판단
                sell_worthy, worthy_reason = self.order_manager.is_sell_worthy(code)
                if not upside["should_hold"]:
                    pr = pos.profit_rate
                    if pr > 0.1 and sell_worthy:
                        logger.info(
                            "📉 [%s] 추세 소진 → 수익 확정: %.1f%% (%+,d원) | %s",
                            pos.stock_name, pr, pos.profit_loss, upside["reason"],
                        )
                        self.order_manager.execute_sell(
                            code, f"추세소진(수익={pr:.1f}%) | {upside['reason']}"
                        )
                        self._on_trade_completed(code, pr, "SELL")
                    elif pr < -0.5 and upside["momentum_score"] < -0.3:
                        # v4.9: 손실 + 추세 소진 + 모멘텀 음전 → 빠른 탈출
                        logger.info(
                            "📉 [%s] 손실+추세소진 → 빠른 탈출: %.1f%% | 모멘텀=%.2f | %s",
                            pos.stock_name, pr, upside["momentum_score"], upside["reason"],
                        )
                        self.order_manager.execute_sell(
                            code, f"추세소진탈출({pr:.1f}%,모멘텀={upside['momentum_score']:.2f})"
                        )
                        self._on_trade_completed(code, pr, "SELL")
                    elif pr > 0 and not sell_worthy:
                        logger.info(
                            "  [%s] 추세 소진이나 수익 부족: %.2f%% (%+,d원) — %s",
                            pos.stock_name, pr, pos.profit_loss, worthy_reason,
                        )
                elif self._cycle_count % 10 == 0:
                    logger.info(
                        "  [%s] 보유유지: 수익=%.1f%% 여력=%.1f%% 모멘텀=%.2f 목표=%s원",
                        pos.stock_name, pos.profit_rate,
                        upside["upside_pct"], upside["momentum_score"],
                        f"{pos.target_price:,}" if pos.target_price else "미정",
                    )

            except Exception as e:
                logger.error("[%s] 상승여력 재분석 실패: %s", code, e)

    def _on_trade_completed(self, stock_code: str = "", profit_rate: float = 0,
                               decision: str = ""):
        """매도 완료 시 진화 카운터 증가 + 누적 통계 기록 + 종목별 학습 + 레짐/패턴 기록."""
        self._trades_since_evolution += 1
        self._trades_since_code_evolution += 1

        # v4.4: 매도 후 쿨다운 — 같은 종목 즉시 재매수 방지
        if stock_code:
            if profit_rate < 0:
                # v4.7: 손실 매도 → 당일 재매수 금지 (whipsaw 원천 차단)
                today = datetime.now().strftime("%Y-%m-%d")
                self._loss_stocks_today[stock_code] = today
                logger.info(
                    "🚫 당일 재매수 금지: %s (손실 %.2f%% 매도, %s 까지)",
                    stock_code, profit_rate, today,
                )
                cooldown_sec = 1800  # 백업 쿨다운 (날짜 바뀌면 해제되므로)
            else:
                # 수익 매도: 10분 쿨다운 (단기 급등 후 하락 방지)
                cooldown_sec = 600
            self._cooldown_stocks[stock_code] = time.time() + cooldown_sec

        # 누적 통계 기록 (StateManager)
        realized_profit = 0.0
        try:
            last_trade = self.order_manager.trade_history[-1] if self.order_manager.trade_history else None
            if last_trade and last_trade.side == "SELL":
                realized_profit = float(last_trade.profit_loss)
                is_win = realized_profit > 0
                self._state_mgr.record_trade(realized_profit, is_win)
        except Exception as e:
            logger.warning("누적 통계 기록 실패: %s", e)

        # v4.8: 일일 손익 누적 + 연속 손실 카운터
        self._update_daily_pnl(realized_profit)
        if profit_rate < 0:
            self._consecutive_losses += 1
            limit = self.settings.consecutive_loss_limit
            if self._consecutive_losses >= limit and not self._auto_stopped:
                self._auto_stopped = True
                self._stop_entry_pnl = self._daily_realized_pnl
                logger.warning(
                    "🛑 자동 정지: 연속 %d회 손실 → 매수 중단. "
                    "일일 손익=%.0f원. 손실 %.0f%% 회복 시 재개.",
                    self._consecutive_losses, self._daily_realized_pnl,
                    self.settings.recovery_threshold * 100,
                )
        else:
            if self._consecutive_losses > 0:
                logger.info("  연속 손실 초기화 (수익 매도, 이전 연속=%d)", self._consecutive_losses)
            self._consecutive_losses = 0

        # 종목별 임계값 학습
        if stock_code and isinstance(self.strategy, ExpertStrategy):
            self.strategy.learn_from_trade(stock_code, profit_rate, decision)

        # v2.9: 레짐별 매매 성과 기록
        if self._evolution and self._market_ctx:
            regime = self._market_ctx.regime
            self._evolution.record_regime_trade(regime, profit_rate)

        # v2.9: 패턴 메모리 저장 (매도 시 기술적 스냅샷 + 결과 기록)
        if self._evolution and stock_code:
            try:
                snapshot = {"regime": self._market_ctx.regime if self._market_ctx else "unknown"}
                # 실시간 기술적 분석 스냅샷 수집
                if isinstance(self.strategy, ExpertStrategy):
                    try:
                        # v4.2: 시장별 API 분기
                        if self.market != "KR":
                            cp = self.api.get_overseas_price(self.market, stock_code)
                            cs = self.api.get_overseas_daily_chart(self.market, stock_code, count=30)
                        else:
                            cp = self.api.get_current_price(stock_code)
                            cs = self.api.get_minute_chart(stock_code, period="3")
                        if cp and cs and len(cs) >= 10:
                            t = self.strategy.technical.analyze(
                                self.strategy._ensure_ascending(cs), cp.get("price", 0))
                            snapshot.update({
                                "trend_score": round(t.trend_score, 3),
                                "momentum_score": round(t.momentum_score, 3),
                                "rsi": round(t.rsi, 1),
                                "bb_position": round(t.bb_position, 3),
                                "volume_ratio": round(t.volume_ratio, 2),
                            })
                    except Exception:
                        snapshot.update({
                            "trend_score": 0, "momentum_score": 0,
                            "rsi": 50, "bb_position": 0.5, "volume_ratio": 1.0,
                        })
                outcome = {"profit_rate": profit_rate, "decision": decision}
                self._evolution.memorize_pattern(stock_code, snapshot, outcome)

                # v3.2: Q-Learning 학습 (매도 완료 시 보상 업데이트)
                if self._q_agent:
                    self._q_agent.record_sell(stock_code, profit_rate, snapshot)

            except Exception:
                pass

        # v3.2: 전략 앙상블 성과 업데이트
        if self._ensemble and stock_code:
            try:
                last_trade = self.order_manager.trade_history[-1] if self.order_manager.trade_history else None
                if last_trade and last_trade.side == "SELL":
                    reason = last_trade.reason if hasattr(last_trade, 'reason') else ""
                    strategy_name = self._ensemble.identify_strategy(reason)
                    self._ensemble.update_performance(
                        strategy_name,
                        float(last_trade.profit_loss),
                        float(last_trade.profit_rate),
                    )
            except Exception as e:
                logger.debug("앙상블 성과 업데이트 실패: %s", e)

    def _get_contract_strength(self, stock_code: str) -> float:
        """체결강도 계산: 매수호가잔량 / 매도호가잔량 × 100.

        KIS 호가 API에서 총 매수호가잔량과 총 매도호가잔량을 구해 비율을 계산.
        100 = 균형, 120+ = 매수 우위(진입 조건), 80- = 매도 우위.
        API 호출 실패 시 0을 반환 (호출부에서 0이면 필터 무력화).
        """
        try:
            ob = self.api.get_orderbook(stock_code)
            bid = ob.get("total_bid_volume", 0)
            ask = ob.get("total_ask_volume", 0)
            if ask <= 0:
                return 0.0
            return (bid / ask) * 100
        except Exception:
            return 0.0

    def _scan_momentum_breakouts(self, stocks: list[str]) -> None:
        """v3.2: 거래량 급등 + 가격 돌파 종목을 자동 포착하여 매수한다.

        이미 분석된 종목 중 거래량이 평소 대비 3배 이상이면서
        당일 고가를 돌파하는 종목을 자동 매수한다.
        """
        if not self.order_manager.can_buy():
            return

        # v4.5: 장마감 15분 전 신규 매수 금지
        from trading.market_scheduler import MARKETS
        mkt = MARKETS.get(self.market)
        if mkt:
            now_str = datetime.now().strftime("%H:%M")
            if not mkt.crosses_midnight and now_str >= mkt.liquidate_time:
                return

        for stock_code in stocks[:10]:  # 상위 10종목만 스캔
            if stock_code in self.order_manager.positions:
                continue
            # v4.7: 당일 손실 매도 종목 차단 (whipsaw 방지)
            if self._is_blocked_today(stock_code):
                continue
            if stock_code in self._cooldown_stocks:
                if time.time() < self._cooldown_stocks[stock_code]:
                    continue

            try:
                # v4.2: 시장별 API 분기
                if self.market != "KR":
                    current = self.api.get_overseas_price(self.market, stock_code)
                    if not current or current.get("price", 0) <= 0:
                        continue
                    candles = self.api.get_overseas_daily_chart(self.market, stock_code, count=30)
                    if not candles or len(candles) < 10:
                        continue
                else:
                    current = self.api.get_current_price(stock_code)
                    if not current or current.get("price", 0) <= 0:
                        continue
                    candles = self.api.get_minute_chart(stock_code, period="3")
                    if not candles or len(candles) < 10:
                        continue

                # 거래량 비율 계산 (최근 3봉 vs 이전 평균)
                recent_vols = [c.get("volume", 0) for c in candles[:3]]
                older_vols = [c.get("volume", 0) for c in candles[3:13]]
                avg_recent = sum(recent_vols) / max(len(recent_vols), 1)
                avg_older = sum(older_vols) / max(len(older_vols), 1)

                if avg_older <= 0:
                    continue

                volume_ratio = avg_recent / avg_older

                # 가격 돌파 확인 (최근 종가 > 이전 10봉 최고가)
                recent_close = candles[0].get("close", 0)
                prev_highs = [c.get("high", 0) for c in candles[1:11]]
                prev_high = max(prev_highs) if prev_highs else 0

                # 돌파 조건: 거래량 3배 이상 + 가격 돌파 + 양봉
                is_breakout = (
                    volume_ratio >= 3.0
                    and recent_close > prev_high > 0
                    and recent_close > candles[0].get("open", 0)  # 양봉
                )

                if is_breakout:
                    stock_name = self._resolve_stock_name(stock_code, current)
                    logger.info(
                        "🚀 모멘텀 돌파 감지: %s(%s) 거래량=%.1fx 가격돌파=%s→%s",
                        stock_name, stock_code, volume_ratio,
                        f"{prev_high:,}", f"{recent_close:,}",
                    )

                    # v4.9: 브레이크아웃도 동일한 진입 게이트를 통과해야 함.
                    # 돌파 강도(volume_ratio)가 확신도 계산 안에서 반영된다.
                    atr_value = 0.0
                    analysis_for_gate = None
                    if isinstance(self.strategy, ExpertStrategy):
                        try:
                            analysis_for_gate = self.strategy.full_analysis(
                                stock_code, stock_name, candles, current)
                            if analysis_for_gate.technical:
                                atr_value = analysis_for_gate.technical.atr
                        except Exception:
                            analysis_for_gate = None

                    self._try_enter_position(
                        stock_code=stock_code,
                        stock_name=stock_name,
                        price_data=current,
                        reason=f"모멘텀돌파(거래량{volume_ratio:.1f}x,고가돌파)",
                        analysis=analysis_for_gate,
                        atr_value=atr_value,
                        volume_ratio_override=volume_ratio,
                    )

                    if not self.order_manager.can_buy():
                        break
            except Exception as e:
                logger.debug("[%s] 모멘텀 스캔 오류: %s", stock_code, e)

    def _analyze_and_trade(self, stock_code: str) -> None:
        """종목을 분석하고 매매를 실행한다.

        v4.1 재설계: 단순명확한 3단계
          1. 데이터 수집
          2. 전략 분석 → BUY/SELL/HOLD 결정
          3. 즉시 실행 (불필요한 재검증 제거)
        """
        # ── 1단계: 기본 검증 (데이터 + 용량) ──
        if not self.order_manager.can_buy() and stock_code not in self.order_manager.positions:
            return  # 매수 불가 + 보유도 아님 → 분석 불필요

        # v4.7: 당일 손실 매도 종목은 분석조차 안 함 (보유 중 아닐 때만)
        if (stock_code not in self.order_manager.positions
                and self._is_blocked_today(stock_code)):
            return

        # v4.8: 자동 정지 중이면 신규 매수 차단 (보유 종목 청산은 계속)
        if (stock_code not in self.order_manager.positions
                and not self._can_enter_new_position()):
            return

        if stock_code in self._cooldown_stocks:
            if time.time() < self._cooldown_stocks[stock_code]:
                return
            del self._cooldown_stocks[stock_code]

        # v4.2: 시장별 API 분기
        if self.market != "KR":
            current_price = self.api.get_overseas_price(self.market, stock_code)
            if not current_price or not current_price.get("price"):
                return
            candles = self.api.get_overseas_daily_chart(self.market, stock_code, count=60)
            if not candles:
                return
        else:
            current_price = self.api.get_current_price(stock_code)
            if not current_price or not current_price.get("price"):
                return

            # v4.3: 매수금액 대비 가격 필터 — 1주도 못 사는 종목은 분석 스킵
            price = current_price["price"]
            if stock_code not in self.order_manager.positions and price > self.settings.max_buy_amount:
                return

            candles = self.api.get_minute_chart(stock_code, period="3")
            if len(candles) < 20:
                candles = self.api.get_daily_chart(stock_code, count=60)
            if not candles:
                return

        # ── 2단계: 전략 분석 (한 번만) ──
        stock_name = self._resolve_stock_name(stock_code, current_price)
        atr_value = 0.0

        if isinstance(self.strategy, ExpertStrategy):
            analysis = self.strategy.full_analysis(
                stock_code, stock_name, candles, current_price)
            if analysis.technical:
                atr_value = analysis.technical.atr

            if analysis.decision != "HOLD":
                logger.info("\n%s", analysis.summary())

            # Signal 생성
            if analysis.decision in ("STRONG_BUY", "BUY"):
                signal_type = SignalType.BUY
            elif analysis.decision in ("STRONG_SELL", "SELL"):
                signal_type = SignalType.SELL
            else:
                signal_type = SignalType.HOLD

            reason_str = " | ".join(analysis.reasons[:3]) if analysis.reasons else analysis.decision
            signal = Signal(
                signal_type=signal_type,
                stock_code=stock_code,
                reason=reason_str,
                strength=analysis.confidence,
                target_price=getattr(analysis, '_target_price', 0),
            )
        else:
            signal = self.strategy.analyze(stock_code, candles, current_price)

        # ── 3단계: 즉시 실행 ──

        # 매수: v4.9 — 모든 필터/확신도 계산은 _try_enter_position 이 담당
        if signal.signal_type == SignalType.BUY:
            target_price = getattr(signal, "target_price", 0) or 0
            estimated_upside = 0.0
            if target_price > 0 and current_price["price"] > 0:
                estimated_upside = (target_price - current_price["price"]) / current_price["price"] * 100

            analysis_for_gate = analysis if isinstance(self.strategy, ExpertStrategy) else None
            self._try_enter_position(
                stock_code=stock_code,
                stock_name=stock_name,
                price_data=current_price,
                reason=signal.reason,
                analysis=analysis_for_gate,
                atr_value=atr_value,
                target_price=target_price,
                estimated_upside=estimated_upside,
            )

        # 매도: 전략이 SELL 결정 + 보유 중 + 매도 적합 → 바로 실행
        elif signal.signal_type == SignalType.SELL:
            if stock_code in self.order_manager.positions:
                pos = self.order_manager.positions[stock_code]
                sell_worthy, worthy_reason = self.order_manager.is_sell_worthy(stock_code)
                if sell_worthy:
                    pr = pos.profit_rate
                    logger.info(
                        "▶ 매도 실행: %s(%s) 수익=%.1f%% | %s",
                        stock_name, stock_code, pr, signal.reason,
                    )
                    self.order_manager.execute_sell(stock_code, signal.reason)
                    self._on_trade_completed(stock_code, pr, "SELL")
                elif pos.profit_rate > 0:
                    logger.info(
                        "  매도 보류: %s (%s %.2f%%) — %s",
                        stock_name, stock_code, pos.profit_rate, worthy_reason,
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

        if self._market_ctx:
            logger.info(
                "  시장: %s (KOSPI=%+.2f%% KOSDAQ=%+.2f%%)",
                self._market_ctx.regime,
                self._market_ctx.kospi_change,
                self._market_ctx.kosdaq_change,
            )

        if self._evolution:
            logger.info(
                "  진화: 세대 #%d (적합도=%.1f) | 다음 진화까지 %d건",
                self._evolution.state.generation,
                self._evolution.state.best_fitness,
                15 - self._trades_since_evolution,
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
