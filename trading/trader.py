"""자동 매매 엔진.

전략 분석 → 리스크 관리 → 주문 실행의 전체 사이클을 관리한다.
Expert 모드에서는 시장 컨텍스트와 멀티 타임프레임 분석을 추가한다.

v2.7: 분석 기반 목표가 매도 — 상승여력 소진까지 홀딩
v2.9: 레짐 적응형 전략 전환, 멀티 타임프레임 확인, 패턴 메모리, 리스크 자동 진화
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
        self._COOLDOWN_SECONDS = 900  # 15분 쿨다운

        # 진화 엔진
        self._evolution = None
        self._trades_since_evolution = 0
        self._evolution_enabled = True

        # 종목 선정 캐시 (5분마다 갱신)
        self._stock_cache: list[str] = []
        self._stock_cache_time: float = 0
        self._STOCK_CACHE_TTL = 300  # 5분

        # 레짐 전략 조정값 (v2.9)
        self._regime_adj: dict = {}

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
        logger.info("  OSHMS 자동 매매 시스템 v2.6 가동")
        logger.info("=" * 70)
        logger.info("전략: %s%s", self.strategy.name, " (전문가 모드)" if is_expert else "")
        logger.info("매매 시간: %s ~ %s", self.settings.trading_start_time, self.settings.trading_end_time)
        logger.info("최대 매수금액: %s원", f"{self.settings.max_buy_amount:,}")
        logger.info("최대 보유종목: %d개", self.settings.max_hold_count)
        logger.info("손절: %.1f%% / 익절: %.1f%%", self.settings.stop_loss_pct, self.settings.take_profit_pct)
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
        logger.info("자동 매매 중지 (총 %d 사이클)", self._cycle_count)

    # ──────────────────────────────────────────────
    # 진화 엔진
    # ──────────────────────────────────────────────

    def _init_evolution(self):
        """진화 엔진을 초기화한다."""
        try:
            from learning.evolution import EvolutionEngine
            self._evolution = EvolutionEngine()
            # 기존 거래 수로 진화 카운터 초기화
            self._trades_since_evolution = self._count_recent_trades()
            logger.info("진화 엔진 초기화 완료 (최근 거래 %d건)", self._trades_since_evolution)
        except Exception as e:
            logger.warning("진화 엔진 초기화 실패: %s", e)
            self._evolution = None

    def _count_recent_trades(self) -> int:
        """최근 거래 수를 반환한다."""
        if not TRADES_FILE.exists():
            return 0
        try:
            trades = json.loads(TRADES_FILE.read_text(encoding="utf-8"))
            sells = [t for t in trades if t.get("side") == "SELL"]
            return len(sells) % 15  # 15건마다 진화하므로 나머지
        except Exception:
            return 0

    def _try_evolve(self):
        """진화 조건 충족 시 진화 사이클을 실행한다."""
        if not self._evolution or not self._evolution_enabled:
            return
        if not self._evolution.should_evolve(self._trades_since_evolution):
            return

        logger.info("진화 조건 충족 (%d건 거래) - 진화 사이클 시작", self._trades_since_evolution)
        try:
            trades = self._load_trades()
            if not trades:
                return

            # 최근 캔들 데이터 (진화 최적화용)
            candles = None
            held_codes = list(self.order_manager.positions.keys())
            if held_codes:
                try:
                    candles = self.api.get_daily_chart(held_codes[0], count=60)
                except Exception:
                    pass

            result = self._evolution.evolve(trades, candles)
            self._trades_since_evolution = 0

            # 진화 결과 적용
            adjustments = self._evolution.get_strategy_adjustments()
            if adjustments and isinstance(self.strategy, ExpertStrategy):
                self.strategy.apply_adjustments(adjustments)
                logger.info("진화 조정 적용: %s", adjustments)

            # v2.9: 리스크 파라미터 자동 진화 적용
            risk = result.get("risk_params", {})
            if risk:
                if risk.get("trailing_base"):
                    logger.info(
                        "리스크 진화 적용: 손절=%.1f%% 트레일링=%.1f%% 익절=%.0f%%",
                        risk.get("stop_loss_pct", -3),
                        risk.get("trailing_base", 3),
                        risk.get("take_profit_pct", 20),
                    )

            logger.info(
                "진화 세대 #%d 완료: 적합도=%.1f (규칙 +%d -%d)",
                result["generation"], result["fitness"],
                result["rules_added"], result["rules_removed"],
            )
        except Exception as e:
            logger.error("진화 실행 실패: %s", e)

    def _load_trades(self) -> list[dict]:
        """거래 기록을 로드한다."""
        if not TRADES_FILE.exists():
            return []
        try:
            return json.loads(TRADES_FILE.read_text(encoding="utf-8"))
        except Exception:
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

        거래량 + 거래대금 + 상승률 상위 종목을 합산하여
        중복 제거 후 최종 후보를 선정한다.
        """
        now = time.time()
        if self._stock_cache and (now - self._stock_cache_time) < self._STOCK_CACHE_TTL:
            return self._stock_cache

        try:
            seen = set()
            candidates = []  # (code, score)

            # 1. 거래량 상위 30개
            try:
                vol_rank = self.api.get_volume_rank(count=30)
                for i, s in enumerate(vol_rank):
                    code = s["stock_code"]
                    if code not in seen and self._is_valid_stock(s):
                        seen.add(code)
                        candidates.append((code, 30 - i))  # 상위일수록 높은 점수
            except Exception as e:
                logger.warning("거래량 순위 조회 실패: %s", e)

            time.sleep(0.3)

            # 2. 거래대금 상위 30개
            try:
                amount_rank = self.api.get_market_cap_rank(count=30)
                for i, s in enumerate(amount_rank):
                    code = s["stock_code"]
                    if self._is_valid_stock(s):
                        if code in seen:
                            # 이미 있으면 점수 추가 (중복 = 더 인기)
                            for j, (c, sc) in enumerate(candidates):
                                if c == code:
                                    candidates[j] = (c, sc + 20 - i)
                                    break
                        else:
                            seen.add(code)
                            candidates.append((code, 20 - i))
            except Exception as e:
                logger.warning("거래대금 순위 조회 실패: %s", e)

            time.sleep(0.3)

            # 3. 상승 종목 20개 (모멘텀)
            try:
                up_rank = self.api.get_fluctuation_rank(direction="up", count=20)
                for i, s in enumerate(up_rank):
                    code = s["stock_code"]
                    cr = s.get("change_rate", 0)
                    if self._is_valid_stock(s) and 0.5 < cr < 8:  # 소폭~중폭 상승만
                        if code in seen:
                            for j, (c, sc) in enumerate(candidates):
                                if c == code:
                                    candidates[j] = (c, sc + 15)
                                    break
                        else:
                            seen.add(code)
                            candidates.append((code, 15 - i))
            except Exception as e:
                logger.warning("상승률 순위 조회 실패: %s", e)

            # 점수 기준 정렬 → 상위 20개 선정
            candidates.sort(key=lambda x: x[1], reverse=True)
            result = [code for code, _ in candidates[:20]]

            if result:
                self._stock_cache = result
                self._stock_cache_time = now
                logger.info(
                    "종목 선정: %d개 후보 (거래량+거래대금+모멘텀 합산)",
                    len(result),
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
            2000 < price < 500000
            and -5 < change_rate < 8  # 급등/급락 제외 (보수적)
        )

    # ──────────────────────────────────────────────
    # 매매 사이클
    # ──────────────────────────────────────────────

    def _run_cycle(self, stocks: list[str]) -> None:
        """하나의 매매 사이클을 실행한다."""
        self._cycle_count += 1

        # 1. 보유 종목 가격 갱신
        self.order_manager.update_prices()

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

        # 4. 진화 체크
        if self._cycle_count % 5 == 0:
            self._try_evolve()

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

        # ── 1. 트레일링 스탑 (수익 보호 — 여유롭게) ──
        for code in self.order_manager.check_trailing_stop():
            try:
                pos = self.order_manager.positions.get(code)
                if not pos or pos.profit_rate <= 0:
                    continue

                # 목표가가 있고, 아직 많이 남았으면 트레일링 무시
                if pos.target_price > 0 and pos.current_price > 0:
                    remaining_upside = (pos.target_price - pos.current_price) / pos.current_price * 100
                    if remaining_upside > 5.0:
                        logger.info(
                            "[%s] 트레일링 유보: 목표가까지 %.1f%% 남음 (목표=%s원)",
                            code, remaining_upside, f"{pos.target_price:,}",
                        )
                        continue

                pr = pos.profit_rate
                self.order_manager.execute_sell(code, "트레일링스탑")
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

        # ── 3. 손절 (극단적 하락만 — -3% 하드스탑) ──
        for code in self.order_manager.check_stop_loss():
            try:
                pos = self.order_manager.positions.get(code)
                if not pos:
                    continue

                if pos.profit_rate <= -3.0:
                    pr = pos.profit_rate
                    self.order_manager.execute_sell(code, "손절")
                    self._cooldown_stocks[code] = time.time() + self._COOLDOWN_SECONDS
                    logger.info("[%s] 쿨다운 등록: %d초간 재매수 금지", code, self._COOLDOWN_SECONDS)
                    self._on_trade_completed(code, pr, "BUY")
                else:
                    logger.info(
                        "[%s] 손절 대기: %.2f%% (기준: -3.0%% 미만 시 매도)",
                        code, pos.profit_rate,
                    )
            except Exception as e:
                logger.error("[%s] 손절 매도 실패: %s", code, e)

        # ── 4. 장마감 처리 (15:20 이후 — 수익 큰 종목만 청산, 나머지 보유) ──
        now_str = datetime.now().strftime("%H:%M")
        if now_str >= "15:20":
            remaining = list(self.order_manager.positions.keys())
            for code in remaining:
                try:
                    pos = self.order_manager.positions[code]
                    # 큰 수익 (5%+): 확정 → 수익을 먹고 퇴장
                    if pos.profit_rate >= 5.0:
                        logger.info(
                            "장마감 익절: %s(%s) 수익률=%.2f%% → 수익 확정",
                            pos.stock_name, code, pos.profit_rate,
                        )
                        self.order_manager.execute_sell(code, "장마감익절(수익확정)")
                        self._on_trade_completed(code, pos.profit_rate, "SELL")
                    # 큰 손실 (-3%+): 청산
                    elif pos.profit_rate <= -3.0:
                        logger.info(
                            "장마감 손절: %s(%s) 수익률=%.2f%%",
                            pos.stock_name, code, pos.profit_rate,
                        )
                        self.order_manager.execute_sell(code, "장마감청산(손절)")
                        self._on_trade_completed(code, pos.profit_rate, "BUY")
                    else:
                        # 소폭 수익/손실: 오버나이트 보유 (내일 목표가 도달 대기)
                        logger.info(
                            "장마감 보유유지: %s(%s) 수익률=%.2f%% 목표가=%s (오버나이트)",
                            pos.stock_name, code, pos.profit_rate,
                            f"{pos.target_price:,}" if pos.target_price else "미정",
                        )
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

                # 추세 소진 + 수익 확보 → 매도
                if not upside["should_hold"] and pos.profit_rate > 2.0:
                    logger.info(
                        "📉 [%s] 추세 소진 → 수익 확정: %.1f%% | %s",
                        pos.stock_name, pos.profit_rate, upside["reason"],
                    )
                    pr = pos.profit_rate
                    self.order_manager.execute_sell(
                        code, f"추세소진(수익={pr:.1f}%) | {upside['reason']}"
                    )
                    self._on_trade_completed(code, pr, "SELL")
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
        """매도 완료 시 진화 카운터 증가 + 종목별 학습 + 레짐/패턴 기록."""
        self._trades_since_evolution += 1

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
            except Exception:
                pass

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

            # v2.9: 레짐 적응형 매수 강도 조정
            min_strength = 0.25 if isinstance(self.strategy, ExpertStrategy) else 0.3
            regime_buy_adj = self._regime_adj.get("buy_threshold_adj", 0)
            if regime_buy_adj:
                min_strength = max(0.10, min_strength + regime_buy_adj)

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
                        elif recall["bias"] == "bearish" and recall["confidence"] > 0.5:
                            logger.info(
                                "  📚 패턴메모리 경고: 유사패턴 손실 (승률=%.0f%%) → 매수 보류",
                                recall["win_rate"],
                            )
                            return
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

                self.order_manager.execute_buy(
                    stock_code, stock_name, current_price["price"], signal.reason,
                    strength=signal.strength, atr=atr_value,
                    target_price=target_price, estimated_upside=estimated_upside,
                )
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
                        # 추세 소진 → 매도
                        should_sell = True
                        sell_reason = f"추세소진(여력={upside['upside_pct']:.1f}%) | {upside['reason']}"
                        logger.info(
                            "  → 추세 소진: 상승여력=%.1f%% 모멘텀=%.2f → 매도 실행",
                            upside["upside_pct"], upside["momentum_score"],
                        )
                else:
                    # Expert가 아닌 전략: 기존 방식 (수익 2% 이상 + 매도 신호)
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
