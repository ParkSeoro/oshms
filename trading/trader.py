"""자동 매매 엔진.

전략 분석 → 리스크 관리 → 주문 실행의 전체 사이클을 관리한다.
Expert 모드에서는 시장 컨텍스트와 멀티 타임프레임 분석을 추가한다.

v2.7: 분석 기반 목표가 매도 — 상승여력 소진까지 홀딩
v2.9: 레짐 적응형 전략 전환, 멀티 타임프레임 확인, 패턴 메모리, 리스크 자동 진화
v3.0: 코드 자체 진화 엔진 연동 — 프로그램이 스스로 약점을 파악하고 개선
v3.2: 전략 앙상블, Q-Learning, 포트폴리오 최적화, 자동 매매 복기
"""

import json
import time
from datetime import datetime
from pathlib import Path

from api.kis_api import KISApi
from config.settings import Settings
from strategy.base import BaseStrategy, Signal, SignalType
from strategy.expert import ExpertStrategy
from strategy.market_context import MarketContextAnalyzer, MarketContext
from trading.order_manager import OrderManager
from trading.state_manager import StateManager
from utils.logger import setup_logger

logger = setup_logger("oshms.trading.trader")

TRADES_FILE = Path("logs/trades.json")


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

        # 손절 후 재매수 방지 (종목코드 → 쿨다운 만료 시각)
        self._cooldown_stocks: dict[str, float] = {}
        self._COOLDOWN_SECONDS = 300  # v4.0: 15분→5분 쿨다운 (빠른 재진입)

        # 진화 엔진 (Level 0: 파라미터 진화)
        self._evolution = None
        self._trades_since_evolution = 0
        self._evolution_enabled = True

        # 코드 진화 엔진 (Level 1~3: 프로그램 자체 진화)
        self._code_evolution = None
        self._trades_since_code_evolution = 0

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
        """현재 시간이 매매 가능 시간인지 확인한다."""
        now = datetime.now().strftime("%H:%M")
        return self.settings.trading_start_time <= now <= self.settings.trading_end_time

    def start(self, target_stocks: list[str] | None = None, interval: int = 10) -> None:
        """자동 매매를 시작한다."""
        self._running = True
        is_expert = isinstance(self.strategy, ExpertStrategy)

        # 진화 엔진 초기화
        self._init_evolution()

        logger.info("=" * 70)
        logger.info("  OSHMS 자동 매매 시스템 v3.2 가동")
        logger.info("  (워렌 버핏 가치투자 + 고급 기술분석 융합 전략)")
        logger.info("=" * 70)
        mode_str = "모의투자" if self.settings.is_mock else "실전투자"
        logger.info("모드: %s | API: %s", mode_str, self.settings.base_url[:35])
        logger.info("전략: %s%s", self.strategy.name, " (전문가+가치투자)" if is_expert else "")
        logger.info("매매 시간: %s ~ %s", self.settings.trading_start_time, self.settings.trading_end_time)
        logger.info("최대 매수금액: %s원", f"{self.settings.max_buy_amount:,}")
        logger.info("최대 보유종목: %d개", self.settings.max_hold_count)
        logger.info("손절: %.1f%% / 최소매도수익: %.1f%%(또는 %s원)",
                     self.settings.stop_loss_pct,
                     self.order_manager.MIN_SELL_PROFIT_PCT,
                     f"{self.order_manager.MIN_SELL_PROFIT_KRW:,}")
        logger.info("감시 주기: %d초 | 종목풀: 전체 시장 스캔", interval)
        if self._evolution:
            logger.info("진화 엔진: 활성 (세대 #%d)", self._evolution.state.generation)
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

    def _try_evolve(self):
        """진화 조건 충족 시 진화 사이클을 실행한다."""
        if not self._evolution or not self._evolution_enabled:
            return
        if not self._evolution.should_evolve(self._trades_since_evolution):
            if self._cycle_count % 10 == 0:
                logger.debug("파라미터 진화 대기: %d/%d건",
                             self._trades_since_evolution, self._evolution.EVOLUTION_INTERVAL)
            return

        logger.info("진화 조건 충족 (%d건 거래) - 진화 사이클 시작", self._trades_since_evolution)
        try:
            trades = self._load_trades()
            if not trades:
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

            # v2.9→v3.2: 리스크 파라미터 자동 진화 — 실제 적용
            risk = result.get("risk_params", {})
            if risk:
                applied = []
                if risk.get("stop_loss_pct") and hasattr(self.settings, 'stop_loss_pct'):
                    self.settings.stop_loss_pct = risk["stop_loss_pct"]
                    applied.append(f"손절={risk['stop_loss_pct']:.1f}%")
                if risk.get("trailing_base"):
                    self._trailing_base = risk["trailing_base"]
                    applied.append(f"트레일링={risk['trailing_base']:.1f}%")
                if risk.get("take_profit_pct") and hasattr(self.settings, 'take_profit_pct'):
                    self.settings.take_profit_pct = risk["take_profit_pct"]
                    applied.append(f"익절={risk['take_profit_pct']:.0f}%")
                if risk.get("cooldown_seconds"):
                    self._COOLDOWN_SECONDS = int(risk["cooldown_seconds"])
                    applied.append(f"쿨다운={risk['cooldown_seconds']}초")
                if applied:
                    logger.info("리스크 진화 실적용: %s", " | ".join(applied))

            logger.info(
                "진화 세대 #%d 완료: 적합도=%.1f (규칙 +%d -%d)",
                result["generation"], result["fitness"],
                result["rules_added"], result["rules_removed"],
            )
        except Exception as e:
            logger.error("진화 실행 실패: %s", e)

    def _try_code_evolution(self):
        """코드 진화 엔진을 실행한다 (프로그램 자체 진화)."""
        if not self._code_evolution:
            return
        if not self._code_evolution.should_evolve(self._trades_since_code_evolution):
            if self._cycle_count % 10 == 0:
                logger.debug("코드 진화 대기: %d/%d건",
                             self._trades_since_code_evolution, self._code_evolution.CYCLE_INTERVAL)
            return

        logger.info("코드 진화 조건 충족 (%d건) — 자체 진화 사이클 시작",
                     self._trades_since_code_evolution)
        try:
            trades = self._load_trades()
            if not trades:
                logger.warning("코드 진화 스킵: 거래 기록이 비어있음")
                return

            candles = None
            held_codes = list(self.order_manager.positions.keys())
            if not held_codes:
                recent_codes = [t.get("code") for t in reversed(trades) if t.get("code")]
                held_codes = recent_codes[:1]
            if held_codes:
                try:
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

    def _select_stocks_wide(self) -> list[str]:
        """전체 시장을 스캔하여 매매 후보를 선정한다.

        거래량 + 거래대금 + 상승률 + 하락 반등 후보를 합산하여
        중복 제거 후 최종 후보를 선정한다.

        v3.0: 스캔 범위 대폭 확대 (20개 → 50개)
        """
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

            # 3. 상승 종목 30개 (모멘텀)
            try:
                up_rank = self.api.get_fluctuation_rank(direction="up", count=30)
                for i, s in enumerate(up_rank):
                    code = s["stock_code"]
                    cr = s.get("change_rate", 0)
                    if self._is_valid_stock(s) and 0.3 < cr < 10:  # 소폭~중폭 상승
                        if code in seen:
                            for j, (c, sc) in enumerate(candidates):
                                if c == code:
                                    candidates[j] = (c, sc + 20)
                                    break
                        else:
                            seen.add(code)
                            candidates.append((code, 20 - i))
            except Exception as e:
                logger.warning("상승률 순위 조회 실패: %s", e)

            time.sleep(0.3)

            # 4. 하락 반등 후보 20개 (과매도 저가 매수 기회)
            try:
                down_rank = self.api.get_fluctuation_rank(direction="down", count=20)
                for i, s in enumerate(down_rank):
                    code = s["stock_code"]
                    cr = s.get("change_rate", 0)
                    price = s.get("price", 0)
                    # 적당한 하락폭(-1%~-5%) + 가격대 필터
                    if -5 < cr < -1 and 3000 < price < 300000:
                        if code not in seen:
                            seen.add(code)
                            candidates.append((code, 10))  # 기본 점수
            except Exception as e:
                logger.warning("하락률 순위 조회 실패: %s", e)

            # 점수 기준 정렬 → 상위 50개 선정
            candidates.sort(key=lambda x: x[1], reverse=True)
            result = [code for code, _ in candidates[:50]]

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
        """매매 적합 종목인지 검증한다."""
        price = stock.get("price", 0)
        change_rate = stock.get("change_rate", 0)
        return (
            1000 < price < 1000000
            and -8 < change_rate < 15  # 범위 확대 (더 많은 기회 포착)
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

    def _check_risk_management(self) -> None:
        """리스크 관리: 상승여력 재분석, 트레일링 스탑, 손절, 장마감.

        v2.7: 보유 종목의 상승여력을 주기적으로 재분석하여
        목표가를 동적으로 갱신하고, 추세가 소진될 때까지 홀딩한다.
        """

        # ── 0. 보유 종목 상승여력 재분석 (3사이클마다) ──
        if isinstance(self.strategy, ExpertStrategy) and self._cycle_count % 3 == 0:
            self._reassess_positions()

        # ── 1. 트레일링 스탑 (v4.0: 수익 보호 즉시 실행, 목표가 무관) ──
        for code in self.order_manager.check_trailing_stop():
            try:
                pos = self.order_manager.positions.get(code)
                if not pos or pos.profit_rate <= 0:
                    continue
                pr = pos.profit_rate
                self.order_manager.execute_sell(code, f"트레일링스탑({pr:.1f}%)")
                self._on_trade_completed(code, pr, "SELL")
            except Exception as e:
                logger.error("[%s] 트레일링스탑 매도 실패: %s", code, e)

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

        # ── 3. 손절 매도 (v4.0: 빠른 손절 복원 — 손실 확대 방지) ──
        for code in self.order_manager.check_stop_loss():
            try:
                pos = self.order_manager.positions.get(code)
                if not pos:
                    continue
                pr = pos.profit_rate
                logger.warning(
                    "⚠ 손절 실행: %s(%s) 수익률=%.2f%% → 손실 최소화",
                    pos.stock_name, code, pr,
                )
                self.order_manager.execute_sell(code, f"손절({pr:.1f}%)")
                self._on_trade_completed(code, pr, "SELL")
            except Exception as e:
                logger.error("[%s] 손절 매도 실패: %s", code, e)

        # ── 4. 장마감 처리 (15:20 이후 — v4.0: 당일 전량 청산, 오버나이트 리스크 제거) ──
        now_str = datetime.now().strftime("%H:%M")
        if now_str >= "15:20":
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

    def _reassess_positions(self) -> None:
        """보유 종목의 상승여력을 재분석하여 목표가를 갱신한다.

        추세가 살아있으면 목표가를 상향 조정하고,
        추세가 소진되면 즉시 매도한다.
        """
        if not isinstance(self.strategy, ExpertStrategy):
            return

        for code, pos in list(self.order_manager.positions.items()):
            if pos.profit_rate <= 0:
                continue  # 수익 종목만 재분석

            try:
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

                # 추세 소진 + 수익 확보 → 매도 (v4.0: 소액이라도 즉시 확정)
                sell_worthy, worthy_reason = self.order_manager.is_sell_worthy(code)
                if not upside["should_hold"] and pos.profit_rate > 0.3 and sell_worthy:
                    logger.info(
                        "📉 [%s] 추세 소진 → 수익 확정: %.1f%% (%+,d원) | %s",
                        pos.stock_name, pos.profit_rate, pos.profit_loss, upside["reason"],
                    )
                    pr = pos.profit_rate
                    self.order_manager.execute_sell(
                        code, f"추세소진(수익={pr:.1f}%) | {upside['reason']}"
                    )
                    self._on_trade_completed(code, pr, "SELL")
                elif not upside["should_hold"] and pos.profit_rate > 0 and not sell_worthy:
                    logger.info(
                        "  [%s] 추세 소진이나 수익 부족: %.2f%% (%+,d원) — %s",
                        pos.stock_name, pos.profit_rate, pos.profit_loss, worthy_reason,
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

        # 누적 통계 기록 (StateManager)
        try:
            last_trade = self.order_manager.trade_history[-1] if self.order_manager.trade_history else None
            if last_trade and last_trade.side == "SELL":
                profit = float(last_trade.profit_loss)
                is_win = profit > 0
                self._state_mgr.record_trade(profit, is_win)
        except Exception as e:
            logger.warning("누적 통계 기록 실패: %s", e)

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

    def _scan_momentum_breakouts(self, stocks: list[str]) -> None:
        """v3.2: 거래량 급등 + 가격 돌파 종목을 자동 포착하여 매수한다.

        이미 분석된 종목 중 거래량이 평소 대비 3배 이상이면서
        당일 고가를 돌파하는 종목을 자동 매수한다.
        """
        if not self.order_manager.can_buy():
            return

        for stock_code in stocks[:10]:  # 상위 10종목만 스캔
            if stock_code in self.order_manager.positions:
                continue
            if stock_code in self._cooldown_stocks:
                if time.time() < self._cooldown_stocks[stock_code]:
                    continue

            try:
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
                    stock_name = current.get("stock_name", stock_code)
                    logger.info(
                        "🚀 모멘텀 돌파 감지: %s(%s) 거래량=%.1fx 가격돌파=%s→%s",
                        stock_name, stock_code, volume_ratio,
                        f"{prev_high:,}", f"{recent_close:,}",
                    )

                    # ATR 가져오기
                    atr_value = 0.0
                    if isinstance(self.strategy, ExpertStrategy):
                        try:
                            analysis = self.strategy.full_analysis(
                                stock_code, stock_name, candles, current)
                            if analysis.technical:
                                atr_value = analysis.technical.atr
                        except Exception:
                            pass

                    # 돌파 강도 = 거래량 비율 기반 (0.3~0.8)
                    strength = min(0.8, 0.3 + (volume_ratio - 3.0) * 0.05)

                    self.order_manager.execute_buy(
                        stock_code, stock_name, current["price"],
                        f"모멘텀돌파(거래량{volume_ratio:.1f}x,고가돌파)",
                        strength=strength, atr=atr_value,
                        per=current.get("per", 0),
                        pbr=current.get("pbr", 0),
                    )

                    if not self.order_manager.can_buy():
                        break
            except Exception as e:
                logger.debug("[%s] 모멘텀 스캔 오류: %s", stock_code, e)

    def _analyze_and_trade(self, stock_code: str) -> None:
        """종목을 분석하고 매매를 실행한다."""
        # 쿨다운 확인
        if stock_code in self._cooldown_stocks:
            cooldown_until = self._cooldown_stocks[stock_code]
            if time.time() < cooldown_until:
                return
            else:
                del self._cooldown_stocks[stock_code]

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

        # Expert 모드: full_analysis를 한 번만 호출하여 signal도 직접 생성
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

            # full_analysis 결과로 직접 Signal 생성
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
            )
        else:
            signal = self.strategy.analyze(stock_code, candles, current_price)

        # ── 매수 로직 ──
        if signal.signal_type == SignalType.BUY:
            if not self.order_manager.can_buy():
                return
            if stock_code in self.order_manager.positions:
                return

            # v2.9: 멀티 타임프레임 확인 (분봉+일봉 동시 확인)
            if isinstance(self.strategy, ExpertStrategy):
                try:
                    daily = self.api.get_daily_chart(stock_code, count=60)
                    if daily and candles and len(daily) >= 20:
                        mtf = self.strategy.multi_timeframe_confirm(
                            stock_code, candles, daily, current_price)
                        adj = mtf.get("strength_adj", 0)
                        if adj != 0:
                            old_str = signal.strength
                            signal = Signal(
                                signal_type=signal.signal_type,
                                stock_code=signal.stock_code,
                                reason=signal.reason,
                                strength=max(0, min(1.0, signal.strength + adj)),
                                target_price=signal.target_price,
                            )
                            if adj > 0.1:
                                logger.info("  📊 멀티TF 강화: %.2f→%.2f | %s",
                                            old_str, signal.strength, mtf["reason"])
                            elif adj < -0.05:
                                logger.info("  📊 멀티TF 약화: %.2f→%.2f | %s",
                                            old_str, signal.strength, mtf["reason"])
                except Exception as e:
                    logger.debug("멀티TF 확인 실패: %s", e)

            stock_name = current_price.get("stock_name", stock_code)
            logger.info(
                "▶ 매수 신호: %s(%s) 가격=%s 강도=%.2f | %s",
                stock_name, stock_code,
                f"{current_price['price']:,}",
                signal.strength, signal.reason,
            )

            # v4.0: 소액 빈번 거래 — 매수 문턱 낮춤 (레짐 조정 축소)
            min_strength = 0.12 if isinstance(self.strategy, ExpertStrategy) else 0.15
            regime_buy_adj = self._regime_adj.get("buy_threshold_adj", 0)
            if regime_buy_adj:
                min_strength = max(0.08, min_strength + regime_buy_adj * 0.3)

            # v2.9: 패턴 메모리 보강 (유사 패턴 승률로 강도 보정)
            if self._evolution and isinstance(self.strategy, ExpertStrategy):
                try:
                    snap = {
                        "trend_score": analysis.technical_score if 'analysis' in dir() else 0,
                        "momentum_score": analysis.pattern_score if 'analysis' in dir() else 0,
                        "rsi": analysis.technical.rsi if 'analysis' in dir() and analysis.technical else 50,
                        "bb_position": analysis.technical.bb_position if 'analysis' in dir() and analysis.technical else 0.5,
                        "volume_ratio": analysis.technical.volume_ratio if 'analysis' in dir() and analysis.technical else 1.0,
                    }
                    recall = self._evolution.recall_similar_patterns(snap)
                    if recall["matches"] >= 3:
                        if recall["bias"] == "bullish" and recall["confidence"] > 0.5:
                            signal = Signal(
                                signal_type=signal.signal_type,
                                stock_code=signal.stock_code,
                                reason=signal.reason,
                                strength=min(1.0, signal.strength + 0.08),
                                target_price=signal.target_price,
                            )
                            logger.info(
                                "  📚 패턴메모리 강화: +0.08 (유사%d건, 승률=%.0f%%)",
                                recall["matches"], recall["win_rate"],
                            )
                        elif recall["bias"] == "bearish" and recall["confidence"] > 0.7:
                            # v4.0: 0.5→0.7 (높은 확신에서만 차단, 전체 하락장 대응)
                            logger.info(
                                "  📚 패턴메모리 경고: 유사패턴 손실 (승률=%.0f%%) → 매수 보류",
                                recall["win_rate"],
                            )
                            return
                except Exception:
                    pass

            # v3.2: Q-Learning 신뢰도 보정
            if self._q_agent and isinstance(self.strategy, ExpertStrategy):
                try:
                    q_snap = {
                        "regime": self._market_ctx.regime if self._market_ctx else "ranging",
                        "rsi": analysis.technical.rsi if 'analysis' in dir() and analysis.technical else 50,
                        "trend_score": analysis.technical.trend_score if 'analysis' in dir() and analysis.technical else 0,
                        "volume_ratio": analysis.technical.volume_ratio if 'analysis' in dir() and analysis.technical else 1.0,
                    }
                    q_modifier = self._q_agent.get_confidence_modifier(q_snap)
                    if q_modifier != 0:
                        old_str = signal.strength
                        signal = Signal(
                            signal_type=signal.signal_type,
                            stock_code=signal.stock_code,
                            reason=signal.reason,
                            strength=max(0, min(1.0, signal.strength + q_modifier)),
                            target_price=signal.target_price,
                        )
                        if abs(q_modifier) > 0.05:
                            logger.info(
                                "  🧠 Q-Learning 보정: %.2f→%.2f (%+.3f)",
                                old_str, signal.strength, q_modifier,
                            )
                except Exception:
                    pass

            # v3.2: 포트폴리오 최적화 — 매수 차단 확인
            portfolio_size_mult = 1.0
            if self._portfolio_optimizer:
                try:
                    blocked, block_reason = self._portfolio_optimizer.should_block_buy(
                        stock_code, self.order_manager.positions)
                    if blocked:
                        logger.info("  📊 포트폴리오 매수 차단: %s", block_reason)
                        return
                    portfolio_size_mult = self._portfolio_optimizer.get_position_size_multiplier(
                        stock_code)
                    if portfolio_size_mult != 1.0:
                        logger.debug("  📊 포트폴리오 사이즈 승수: %.2f", portfolio_size_mult)
                except Exception:
                    pass

            if signal.strength >= min_strength:
                # 목표가 및 상승여력 계산
                target_price = getattr(signal, "target_price", 0) or 0
                estimated_upside = 0.0
                if target_price > 0 and current_price["price"] > 0:
                    estimated_upside = (target_price - current_price["price"]) / current_price["price"] * 100

                logger.info(
                    "  목표가=%s원 (상승여력=%.1f%%)",
                    f"{target_price:,}" if target_price else "미정",
                    estimated_upside,
                )

                # v3.3: 동적 비중 조절 (확신도 + 시장 심리 기반)
                confidence_mult = 1.0
                if isinstance(self.strategy, ExpertStrategy) and 'analysis' in dir():
                    try:
                        confidence_mult = self.strategy.get_confidence_size_mult(analysis)
                        if confidence_mult != 1.0:
                            logger.info("  동적 비중: %.2f (확신도+심리)", confidence_mult)
                    except Exception:
                        pass
                final_size_mult = portfolio_size_mult * confidence_mult

                self.order_manager.execute_buy(
                    stock_code, stock_name, current_price["price"], signal.reason,
                    strength=signal.strength, atr=atr_value,
                    target_price=target_price, estimated_upside=estimated_upside,
                    per=current_price.get("per", 0),
                    pbr=current_price.get("pbr", 0),
                    size_mult=final_size_mult,
                )

                # v3.2: Q-Learning 매수 상태 기록
                if self._q_agent:
                    try:
                        self._q_agent.record_buy(stock_code, q_snap if 'q_snap' in dir() else {})
                    except Exception:
                        pass
            else:
                logger.debug(
                    "  → 매수 신호 강도 부족: %.2f < %.2f (패스)", signal.strength, min_strength,
                )

        # ── 매도 로직 (분석 기반 목표가 도달 판단) ──
        elif signal.signal_type == SignalType.SELL:
            if stock_code in self.order_manager.positions:
                pos = self.order_manager.positions[stock_code]
                logger.info(
                    "▶ 매도 신호: %s(%s) 가격=%s 수익률=%.2f%% 목표가=%s 강도=%.2f | %s",
                    pos.stock_name, stock_code,
                    f"{current_price['price']:,}",
                    pos.profit_rate,
                    f"{pos.target_price:,}" if pos.target_price else "미정",
                    signal.strength, signal.reason,
                )

                # 손실 중이면 회복 대기 (손절은 _check_risk_management에서 처리)
                if pos.profit_rate <= 0:
                    logger.info("  → 손실 중(%.2f%%) — 회복 대기 (손절만 작동)", pos.profit_rate)
                    return

                # ── 최소 수익 검증 (5-10원 매도 방지) ──
                sell_worthy, worthy_reason = self.order_manager.is_sell_worthy(stock_code)
                if not sell_worthy and pos.profit_rate > 0:
                    logger.info(
                        "  → 매도 보류: %s (%s %+.2f%% %+,d원) — 수익 충분히 키우기",
                        worthy_reason, pos.stock_name, pos.profit_rate, pos.profit_loss,
                    )
                    return

                # ── 상승여력 재분석 ──
                should_sell = False
                sell_reason = signal.reason

                if isinstance(self.strategy, ExpertStrategy):
                    upside = self.strategy.estimate_upside(stock_code, candles, current_price)

                    # 목표가 갱신 (분석 결과가 더 높으면 상향)
                    if upside["target_price"] > pos.target_price:
                        old_target = pos.target_price
                        pos.target_price = upside["target_price"]
                        pos.estimated_upside = upside["upside_pct"]
                        logger.info(
                            "  📈 목표가 상향: %s → %s원 (상승여력=%.1f%%)",
                            f"{old_target:,}" if old_target else "미정",
                            f"{pos.target_price:,}", upside["upside_pct"],
                        )

                    if upside["should_hold"]:
                        # 아직 상승여력이 남아있으면 홀딩
                        logger.info(
                            "  → 홀딩 유지: 상승여력=%.1f%% 모멘텀=%.2f 추세=%s | %s",
                            upside["upside_pct"], upside["momentum_score"],
                            "살아있음" if upside["trend_alive"] else "약화",
                            upside["reason"],
                        )
                    else:
                        # 추세 소진 → 매도 (단, 최소 수익 이상일 때만)
                        if pos.profit_rate >= self.order_manager.MIN_SELL_PROFIT_PCT:
                            should_sell = True
                            sell_reason = f"추세소진(수익={pos.profit_rate:.1f}%, 여력={upside['upside_pct']:.1f}%) | {upside['reason']}"
                            logger.info(
                                "  → 추세 소진 + 수익 충분: %.1f%% (%+,d원) → 매도 실행",
                                pos.profit_rate, pos.profit_loss,
                            )
                        else:
                            logger.info(
                                "  → 추세 소진이나 수익 부족: %.2f%% (%+,d원) — 홀딩 유지",
                                pos.profit_rate, pos.profit_loss,
                            )
                else:
                    # Expert가 아닌 전략: 수익 2% 이상 + 매도 신호
                    if pos.profit_rate > 2.0 and signal.strength >= 0.15:
                        should_sell = True

                # ── 목표가 도달 확인 (무조건 매도) ──
                if pos.target_price > 0 and current_price["price"] >= pos.target_price:
                    should_sell = True
                    sell_reason = f"목표가 도달({pos.target_price:,}원) 수익률={pos.profit_rate:.1f}%"
                    logger.info(
                        "  🎯 목표가 도달! %s원 >= %s원 → 매도",
                        f"{current_price['price']:,}", f"{pos.target_price:,}",
                    )

                # ── 매도 실행 ──
                if should_sell:
                    pr = pos.profit_rate
                    self.order_manager.execute_sell(stock_code, sell_reason)
                    self._on_trade_completed(stock_code, pr, "SELL")

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
