"""전문가 전략 엔진 (워렌 버핏 가치투자 + 고급 기술분석 융합).

워렌 버핏의 가치투자 철학:
- "좋은 기업을 적정 가격에 매수하라" → PER/PBR 기반 가치 분석
- "안전마진을 확보하라" → 52주 최저가 대비 위치, 지지선 분석
- "확신이 있을 때 집중투자하라" → 강한 신호에 큰 포지션
- "시장이 공포에 빠질 때 탐욕스러워라" → 시장 하락 시 저PER 저PBR 매수
- "잔챙이 수익에 팔지 마라" → 최소 수익 임계값, 장기 보유 지향

분석 가중치 (6대 분석):
- 고급 기술적 분석 (추세/모멘텀/변동성/거래량): 30%
- 가치 분석 (PER/PBR/52주 위치): 20%  ← NEW: 버핏 가치투자
- 캔들스틱 패턴: 10%
- 뉴스 감성 분석: 10%
- 시장 컨텍스트 (레짐/시간대): 15%
- 지지/저항 & 가격 위치: 15%
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from api.kis_api import KISApi
from config.settings import Settings
from strategy.base import BaseStrategy, Signal, SignalType
from strategy.technical import TechnicalAnalyzer, TechnicalSnapshot
from strategy.patterns import PatternRecognizer, PatternDirection
from strategy.news_sentiment import NewsSentimentAnalyzer, SentimentResult
from strategy.market_context import MarketContextAnalyzer, MarketContext
from utils.logger import setup_logger

logger = setup_logger("oshms.strategy.expert")


@dataclass
class ExpertAnalysis:
    """전문가 분석 결과."""
    stock_code: str
    stock_name: str
    price: int = 0

    # 개별 점수 (-1 ~ +1)
    technical_score: float = 0
    value_score: float = 0       # 버핏 가치투자 점수
    pattern_score: float = 0
    sentiment_score: float = 0
    market_score: float = 0
    price_level_score: float = 0

    # 펀더멘탈 데이터
    per: float = 0
    pbr: float = 0
    w52_high: int = 0
    w52_low: int = 0

    # 종합 점수
    total_score: float = 0
    confidence: float = 0  # 신뢰도 0~1

    # 상세 분석
    technical: TechnicalSnapshot = None
    patterns: list = field(default_factory=list)
    sentiment: SentimentResult = None
    market_ctx: MarketContext = None

    # 결정
    decision: str = ""  # "STRONG_BUY", "BUY", "HOLD", "SELL", "STRONG_SELL"
    reasons: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"══ {self.stock_name}({self.stock_code}) 전문가 분석 (버핏+기술적) ══",
            f"현재가: {self.price:,}원 | 판정: {self.decision}",
            f"종합점수: {self.total_score:+.3f} (신뢰도: {self.confidence:.0%})",
            f"  기술분석: {self.technical_score:+.3f}",
            f"  가치분석: {self.value_score:+.3f} (PER={self.per:.1f} PBR={self.pbr:.2f})",
            f"  패턴분석: {self.pattern_score:+.3f}",
            f"  뉴스감성: {self.sentiment_score:+.3f}",
            f"  시장환경: {self.market_score:+.3f}",
            f"  가격위치: {self.price_level_score:+.3f}",
        ]
        if self.reasons:
            lines.append("근거:")
            for r in self.reasons:
                lines.append(f"  • {r}")
        return "\n".join(lines)


class ExpertStrategy(BaseStrategy):
    """전문가 종합 전략.

    모든 분석 모듈을 통합하여 최종 매매 결정을 내린다.
    """

    name = "expert"

    # 분석 가중치 (버핏 가치투자 + 기술적 분석 융합)
    WEIGHTS = {
        "technical": 0.30,     # 기술적 분석 (추세, MACD, RSI, 볼린저 등)
        "value": 0.20,         # 버핏 가치 분석 (PER, PBR, 52주 위치)
        "pattern": 0.10,       # 캔들스틱 패턴
        "sentiment": 0.10,     # 뉴스 감성
        "market": 0.15,        # 시장 환경 (KOSPI/KOSDAQ 레짐)
        "price_level": 0.15,   # 지지/저항 가격 위치
    }

    # 기본 매매 임계값 (종목별 동적으로 조정됨)
    # v4.0: 소액 빈번 거래 — 매수 문턱 낮춰서 거래 빈도 ↑
    STRONG_BUY_THRESHOLD = 0.15  # v4.0: 0.25→0.15
    BUY_THRESHOLD = 0.08         # v4.0: 0.15→0.08 (더 자주 매수)
    SELL_THRESHOLD = -0.03       # v4.0: -0.05→-0.03 (더 빨리 매도 신호)
    STRONG_SELL_THRESHOLD = -0.10  # v4.0: -0.15→-0.10

    # 종목 프로필 파일
    STOCK_PROFILES_FILE = Path("data/stock_profiles.json")

    def __init__(self, api: KISApi | None = None, settings: Settings | None = None):
        self.technical = TechnicalAnalyzer()
        self.pattern_recognizer = PatternRecognizer()
        self.news_analyzer = NewsSentimentAnalyzer()
        self.market_analyzer = MarketContextAnalyzer(api) if api else None
        self._market_ctx: MarketContext | None = None
        self._market_ctx_time: float = 0

        # v3.4: 섹터 모멘텀 로테이션
        self._sector_analyzer = None
        if api:
            try:
                from analysis.sector import SectorAnalyzer
                self._sector_analyzer = SectorAnalyzer(api)
            except Exception:
                pass

        # v3.3: 시장 심리 지수 (공포/탐욕)
        self._fear_greed_score: float = 0.0  # -1.0(극공포) ~ +1.0(극탐욕)
        self._fear_greed_time: float = 0

        # 종목별 동적 임계값 프로필
        self._stock_profiles: dict = self._load_stock_profiles()

    def set_market_context(self, ctx: MarketContext) -> None:
        """시장 컨텍스트를 외부에서 설정한다."""
        self._market_ctx = ctx

    def apply_adjustments(self, adjustments: dict) -> None:
        """진화 엔진의 조정값을 적용한다.

        지원하는 키:
        - WEIGHTS 키 (technical, pattern, sentiment, market, price_level)
        - buy_threshold_adj, sell_threshold_adj
        - stop_loss_adj, take_profit_adj
        """
        changes = []

        # 1. 가중치 조정
        weight_changed = False
        for key in ("technical", "value", "pattern", "sentiment", "market", "price_level"):
            adj = adjustments.get(key, 0)
            if adj and isinstance(adj, (int, float)):
                old = self.WEIGHTS[key]
                self.WEIGHTS[key] = max(0.05, min(0.60, old + adj))
                weight_changed = True
                changes.append(f"{key}: {old:.2f}→{self.WEIGHTS[key]:.2f}")

        if weight_changed:
            # 가중치 정규화 (합이 1.0이 되도록)
            total = sum(self.WEIGHTS.values())
            if total > 0:
                for key in self.WEIGHTS:
                    self.WEIGHTS[key] /= total

        # 2. 매매 임계값 조정
        if adjustments.get("buy_threshold_adj"):
            adj = adjustments["buy_threshold_adj"]
            if isinstance(adj, (int, float)):
                old = self.BUY_THRESHOLD
                self.BUY_THRESHOLD = max(0.05, min(0.40, old + adj))
                changes.append(f"buy_thr: {old:.2f}→{self.BUY_THRESHOLD:.2f}")

        if adjustments.get("sell_threshold_adj"):
            adj = adjustments["sell_threshold_adj"]
            if isinstance(adj, (int, float)):
                old = self.SELL_THRESHOLD
                self.SELL_THRESHOLD = max(-0.30, min(-0.03, old + adj))
                changes.append(f"sell_thr: {old:.2f}→{self.SELL_THRESHOLD:.2f}")

        if changes:
            logger.info("진화 조정 적용: %s", " | ".join(changes))

    # ──────────────────────────────────────────────
    # 종목별 동적 임계값
    # ──────────────────────────────────────────────

    def _load_stock_profiles(self) -> dict:
        """종목별 프로필을 로드한다."""
        if self.STOCK_PROFILES_FILE.exists():
            try:
                return json.loads(self.STOCK_PROFILES_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def _save_stock_profiles(self):
        """종목별 프로필을 저장한다."""
        try:
            self.STOCK_PROFILES_FILE.parent.mkdir(parents=True, exist_ok=True)
            self.STOCK_PROFILES_FILE.write_text(
                json.dumps(self._stock_profiles, ensure_ascii=False, indent=2),
                encoding="utf-8")
        except Exception as e:
            logger.warning("종목 프로필 저장 실패: %s", e)

    def get_stock_thresholds(self, stock_code: str, atr: float = 0,
                              price: float = 0) -> dict:
        """종목별 동적 매매 임계값을 반환한다.

        ATR(변동성) 기반 + 과거 거래 성과 학습 결과를 결합.
        변동성이 큰 종목은 임계값을 높이고 (신중하게),
        안정적인 종목은 낮춘다 (빠르게 진입).
        """
        profile = self._stock_profiles.get(stock_code, {})

        # 1. ATR 기반 변동성 계수 (0.5 ~ 2.0)
        volatility_mult = 1.0
        if atr > 0 and price > 0:
            atr_pct = atr / price * 100  # ATR을 %로 변환
            if atr_pct > 3.0:
                volatility_mult = 1.5  # 고변동: 기준 상향
            elif atr_pct > 2.0:
                volatility_mult = 1.2
            elif atr_pct < 1.0:
                volatility_mult = 0.7  # 저변동: 기준 하향
            elif atr_pct < 1.5:
                volatility_mult = 0.85

        # 2. 학습된 조정값 (과거 거래 성과 기반)
        learned_buy_adj = profile.get("buy_adj", 0)
        learned_sell_adj = profile.get("sell_adj", 0)

        # 3. 최종 임계값 계산
        buy_thr = self.BUY_THRESHOLD * volatility_mult + learned_buy_adj
        strong_buy_thr = self.STRONG_BUY_THRESHOLD * volatility_mult + learned_buy_adj
        sell_thr = self.SELL_THRESHOLD * volatility_mult + learned_sell_adj
        strong_sell_thr = self.STRONG_SELL_THRESHOLD * volatility_mult + learned_sell_adj

        # 안전 범위 제한
        buy_thr = max(0.05, min(0.45, buy_thr))
        strong_buy_thr = max(0.10, min(0.55, strong_buy_thr))
        sell_thr = max(-0.35, min(-0.03, sell_thr))
        strong_sell_thr = max(-0.45, min(-0.10, strong_sell_thr))

        return {
            "buy": buy_thr,
            "strong_buy": strong_buy_thr,
            "sell": sell_thr,
            "strong_sell": strong_sell_thr,
            "volatility_mult": volatility_mult,
        }

    def learn_from_trade(self, stock_code: str, profit_rate: float,
                          decision: str):
        """거래 결과로 종목별 임계값을 학습한다.

        승리: 현재 임계값이 적절 → 미세 강화
        패배: 임계값 조정 필요
          - 매수 후 손실 → 매수 기준 상향 (더 신중하게)
          - 매도 놓침 → 매도 기준 하향 (더 빠르게)
        """
        profile = self._stock_profiles.get(stock_code, {
            "buy_adj": 0, "sell_adj": 0,
            "trades": 0, "wins": 0, "total_profit": 0,
        })

        profile["trades"] = profile.get("trades", 0) + 1
        profile["total_profit"] = profile.get("total_profit", 0) + profit_rate

        is_win = profit_rate > 0

        if is_win:
            profile["wins"] = profile.get("wins", 0) + 1

        # 학습 속도 (거래 횟수에 따라 감쇠)
        lr = max(0.005, 0.03 / (1 + profile["trades"] * 0.1))

        if decision in ("BUY", "STRONG_BUY"):
            if not is_win:
                # 매수 후 손실 → 매수 기준 상향 (더 신중하게)
                profile["buy_adj"] = profile.get("buy_adj", 0) + lr
            else:
                # 매수 후 수익 → 매수 기준 약간 하향 (좋은 진입)
                profile["buy_adj"] = profile.get("buy_adj", 0) - lr * 0.3
        elif decision in ("SELL", "STRONG_SELL"):
            if profit_rate < -1:
                # 큰 손실 매도 → 매도 기준 민감하게 (더 빠르게 팔기)
                profile["sell_adj"] = profile.get("sell_adj", 0) + lr
            elif is_win:
                # 수익 매도 → 현재 기준 적절
                profile["sell_adj"] = profile.get("sell_adj", 0) - lr * 0.2

        # 조정값 안전 범위 제한
        profile["buy_adj"] = max(-0.10, min(0.15, profile.get("buy_adj", 0)))
        profile["sell_adj"] = max(-0.10, min(0.10, profile.get("sell_adj", 0)))

        self._stock_profiles[stock_code] = profile

        # 20건마다 저장
        if profile["trades"] % 20 == 0:
            self._save_stock_profiles()

    def analyze(self, stock_code: str, candles: list[dict], current_price: dict) -> Signal:
        """종합 분석을 수행하여 매매 신호를 생성한다."""
        price = current_price.get("price", 0)
        stock_name = current_price.get("stock_name", stock_code)

        analysis = self.full_analysis(stock_code, stock_name, candles, current_price)

        signal_type = SignalType.HOLD
        if analysis.decision in ("STRONG_BUY", "BUY"):
            signal_type = SignalType.BUY
        elif analysis.decision in ("STRONG_SELL", "SELL"):
            signal_type = SignalType.SELL

        reason_str = " | ".join(analysis.reasons[:3]) if analysis.reasons else analysis.decision

        return Signal(
            signal_type=signal_type,
            stock_code=stock_code,
            reason=reason_str,
            strength=analysis.confidence,
            target_price=self._calc_target_price(analysis),
        )

    def full_analysis(
        self, stock_code: str, stock_name: str,
        candles: list[dict], current_price: dict,
    ) -> ExpertAnalysis:
        """전체 상세 분석을 수행한다."""
        result = ExpertAnalysis(
            stock_code=stock_code,
            stock_name=stock_name,
            price=current_price.get("price", 0),
        )

        # 캔들 정렬 (oldest first)
        sorted_candles = self._ensure_ascending(candles)

        # ── 1. 고급 기술적 분석 ──
        tech_snap = self.technical.analyze(sorted_candles, result.price)
        result.technical = tech_snap
        result.technical_score = self._calc_technical_composite(tech_snap)

        # ── 2. 버핏 가치 분석 (PER/PBR/52주 위치) ──
        result.per = current_price.get("per", 0)
        result.pbr = current_price.get("pbr", 0)
        result.w52_high = current_price.get("w52_high", 0)
        result.w52_low = current_price.get("w52_low", 0)
        result.value_score = self._analyze_value(result)

        # ── 3. 캔들스틱 패턴 ──
        patterns = self.pattern_recognizer.recognize_all(sorted_candles)
        result.patterns = patterns
        result.pattern_score = self.pattern_recognizer.get_signal_score(patterns)

        # ── 4. 뉴스 감성 분석 ──
        try:
            sentiment = self.news_analyzer.analyze(stock_code, stock_name)
            result.sentiment = sentiment
            result.sentiment_score = sentiment.overall_score
        except Exception as e:
            logger.warning("[%s] 뉴스 분석 실패: %s", stock_code, e)
            result.sentiment_score = 0

        # ── 5. 시장 컨텍스트 ──
        if self._market_ctx:
            result.market_ctx = self._market_ctx
            result.market_score = self._market_ctx.market_score
        else:
            result.market_score = 0

        # ── 6. 가격 위치 분석 ──
        result.price_level_score = self._analyze_price_level(tech_snap, result.price)

        # ── v3.3: 시장 심리 지수 갱신 ──
        fg = self._calc_fear_greed_index()
        if abs(fg) >= 0.3:
            label = "탐욕" if fg > 0 else "공포"
            logger.debug("[%s] 시장 심리: %s (%.2f)", stock_name, label, fg)

        # ── v3.4: 섹터 모멘텀 분석 ──
        if self._sector_analyzer:
            try:
                self._sector_analyzer.update()
            except Exception:
                pass

        # ── 종합 점수 계산 (v3.3 심리 + v3.4 섹터 포함) ──
        result.total_score = self._weighted_score(result)
        result.confidence = self._calc_confidence(result)

        # ── 최종 결정 ──
        result.decision = self._make_decision(result)
        result.reasons = self._generate_reasons(result)

        logger.info(
            "[%s] 전문가분석: 점수=%.3f 신뢰도=%.0f%% 결정=%s",
            stock_name, result.total_score, result.confidence * 100, result.decision,
        )

        return result

    # ─────────── 점수 계산 ───────────

    def _calc_technical_composite(self, snap: TechnicalSnapshot) -> float:
        """기술적 지표 종합 점수.

        추세 추종 + 역발상을 상황에 맞게 결합한다.
        - 강한 추세: 추세를 따른다 (모멘텀 순방향)
        - 약한 추세/횡보: 역발상 (과매도 매수, 과매수 매도)
        """
        trend_weight = 0.40
        momentum_weight = 0.25
        volume_weight = 0.25
        volatility_weight = 0.10

        # 추세 강도에 따라 모멘텀 방향 결정
        trend_strength = abs(snap.trend_score)
        if trend_strength > 0.6:
            # 강한 추세: 추세를 따라간다 (모멘텀 순방향)
            momentum_adjusted = snap.momentum_score * 0.5
        elif trend_strength < 0.2:
            # 횡보: 역발상 (과매도 매수, 과매수 매도)
            momentum_adjusted = -snap.momentum_score
        else:
            # 중간: 약한 역발상
            momentum_adjusted = -snap.momentum_score * 0.5

        # 변동성이 높으면 신호 감쇠 (but 약간만)
        volatility_factor = 1.0 - snap.volatility_score * 0.2

        # 거래량 확인: OBV와 추세가 같은 방향이면 보너스
        volume_adjusted = snap.volume_score
        if (snap.trend_score > 0 and snap.obv_trend == "up") or \
           (snap.trend_score < 0 and snap.obv_trend == "down"):
            volume_adjusted *= 1.3  # 추세-거래량 일치 보너스

        score = (
            snap.trend_score * trend_weight
            + momentum_adjusted * momentum_weight
            + volume_adjusted * volume_weight
        ) * volatility_factor

        # 특수 상황 보너스 (크로스 시그널)
        if snap.macd_cross == "golden":
            score += 0.18
        elif snap.macd_cross == "dead":
            score -= 0.18

        if snap.stoch_cross == "golden":
            score += 0.12
        elif snap.stoch_cross == "dead":
            score -= 0.12

        # 일목균형표 강한 신호
        if snap.ichimoku_signal == "strong_buy":
            score += 0.12
        elif snap.ichimoku_signal == "strong_sell":
            score -= 0.12

        # 거래량 급증 + 추세 방향 일치 = 강한 추가 보너스
        if snap.volume_ratio > 2.5:
            if snap.trend_score > 0.3:
                score += 0.10  # 상승 + 거래량 폭증
            elif snap.trend_score < -0.3:
                score -= 0.10  # 하락 + 거래량 폭증

        return max(-1.0, min(1.0, score))

    def _analyze_price_level(self, snap: TechnicalSnapshot, price: float) -> float:
        """가격 위치 분석 점수."""
        score = 0.0

        # 볼린저 밴드 위치
        if snap.bb_position < 0.1:
            score += 0.4  # 하단 터치 → 매수 기회
        elif snap.bb_position < 0.2:
            score += 0.2
        elif snap.bb_position > 0.9:
            score -= 0.4  # 상단 터치 → 매도 기회
        elif snap.bb_position > 0.8:
            score -= 0.2

        # VWAP 대비
        if snap.vwap > 0:
            vwap_diff = (price - snap.vwap) / snap.vwap * 100
            if vwap_diff < -2:
                score += 0.3  # VWAP 아래 → 저평가
            elif vwap_diff > 2:
                score -= 0.2  # VWAP 위 → 고평가

        # 지지선 근접
        if snap.support_levels:
            nearest_support = snap.support_levels[0]
            dist = (price - nearest_support) / price * 100
            if 0 < dist < 1:
                score += 0.3  # 지지선 바로 위 → 매수

        # 저항선 근접
        if snap.resistance_levels:
            nearest_resistance = snap.resistance_levels[0]
            dist = (nearest_resistance - price) / price * 100
            if 0 < dist < 1:
                score -= 0.2  # 저항선 바로 아래 → 주의

        return max(-1.0, min(1.0, score))

    def _analyze_value(self, result: ExpertAnalysis) -> float:
        """워렌 버핏 가치투자 분석 점수 (-1 ~ +1).

        버핏의 핵심 원칙:
        1. PER (주가수익비율): 낮을수록 저평가 → 매수 유리
        2. PBR (주가순자산비율): 낮을수록 자산 대비 저평가
        3. 52주 가격 위치: 저점 근처 → 안전마진 확보
        4. 시장 공포 시 매수: 하락장에서 저PER 종목 적극 매수

        "좋은 기업을 적정 가격에 사라" — 워렌 버핏
        """
        score = 0.0
        reasons = []

        # ── 1. PER 분석 (수익성 대비 가격) ──
        per = result.per
        if per > 0:
            if per < 8:
                score += 0.40    # 극저PER → 강한 저평가 (버핏이 좋아하는 영역)
                reasons.append(f"극저PER({per:.1f}) — 강한 저평가")
            elif per < 12:
                score += 0.25    # 저PER → 적정~저평가
                reasons.append(f"저PER({per:.1f}) — 매력적 가격")
            elif per < 20:
                score += 0.05    # 적정 PER
            elif per < 30:
                score -= 0.10    # 고PER → 다소 고평가
            elif per < 50:
                score -= 0.25    # 고PER → 고평가
                reasons.append(f"고PER({per:.1f}) — 고평가 경고")
            else:
                score -= 0.40    # 극고PER → 버블 의심
                reasons.append(f"극고PER({per:.1f}) — 버블 위험")
        elif per < 0:
            score -= 0.30        # 적자 기업 → 버핏은 절대 투자하지 않음
            reasons.append("적자기업(PER<0) — 버핏 투자 부적격")

        # ── 2. PBR 분석 (순자산 대비 가격) ──
        pbr = result.pbr
        if pbr > 0:
            if pbr < 0.7:
                score += 0.30    # 자산가치 이하 → 큰 안전마진
                reasons.append(f"극저PBR({pbr:.2f}) — 자산가치 이하")
            elif pbr < 1.0:
                score += 0.20    # 순자산 가격 이하 → 좋은 안전마진
                reasons.append(f"저PBR({pbr:.2f}) — 안전마진 확보")
            elif pbr < 1.5:
                score += 0.10    # 적정 PBR
            elif pbr < 3.0:
                score -= 0.05    # 다소 높음
            elif pbr < 5.0:
                score -= 0.15    # 높음
            else:
                score -= 0.25    # 매우 높음
                reasons.append(f"고PBR({pbr:.2f}) — 고평가")

        # ── 3. 52주 가격 위치 (안전마진 분석) ──
        price = result.price
        w52_high = result.w52_high
        w52_low = result.w52_low

        if w52_high > 0 and w52_low > 0 and price > 0:
            # 52주 범위 내 위치 (0=최저, 1=최고)
            price_range = w52_high - w52_low
            if price_range > 0:
                position = (price - w52_low) / price_range

                if position < 0.2:
                    score += 0.25    # 52주 바닥 근처 → 큰 안전마진
                    reasons.append(f"52주 바닥권({position:.0%}) — 안전마진 최대")
                elif position < 0.35:
                    score += 0.15    # 하단부 → 좋은 진입 구간
                    reasons.append(f"52주 하단부({position:.0%})")
                elif position < 0.5:
                    score += 0.05    # 중하단
                elif position > 0.9:
                    score -= 0.20    # 고점 근처 → 안전마진 없음
                    reasons.append(f"52주 고점권({position:.0%}) — 안전마진 부족")
                elif position > 0.75:
                    score -= 0.10    # 상단부

        # ── 4. 시장 공포 시 보너스 (버핏: "다른 사람이 두려워할 때 탐욕스러워라") ──
        if result.market_ctx and result.market_ctx.regime in ("trending_down", "volatile"):
            # 하락장/변동성 높은 장에서 저PER 종목 = 버핏이 좋아하는 상황
            if per > 0 and per < 15 and pbr > 0 and pbr < 2.0:
                score += 0.15
                reasons.append("공포 매수 기회! (하락장 + 저평가)")

        # 분석 근거를 result에 추가
        if reasons:
            result.reasons.extend(reasons)

        return max(-1.0, min(1.0, score))

    # ─────────── v3.3: 시장 심리 지수 (공포/탐욕) ───────────

    def _calc_fear_greed_index(self) -> float:
        """시장 심리 지수를 계산한다. -1.0(극공포) ~ +1.0(극탐욕).

        구성 요소:
        - KOSPI/KOSDAQ 등락률 (시장 추세)
        - 거래대금 변화 (탐욕 = 거래 폭증)
        - 상승/하락 종목 비율
        - VIX(변동성) 대용 지표
        """
        import time
        # 10분 캐시
        if time.time() - self._fear_greed_time < 600 and self._fear_greed_score != 0:
            return self._fear_greed_score

        score = 0.0
        components = 0

        if self._market_ctx:
            m = self._market_ctx
            # 1. 시장 등락률 (-3% ~ +3% → -1.0 ~ +1.0)
            change = getattr(m, 'kospi_change', 0)
            score += max(-1.0, min(1.0, change / 3.0))
            components += 1

            # 2. 레짐 기반 보정
            regime = getattr(m, 'regime', '')
            if regime == 'trending_up':
                score += 0.3
            elif regime == 'trending_down':
                score -= 0.3
            elif regime == 'volatile':
                score -= 0.2  # 변동성 = 공포
            components += 1

            # 3. 거래대금 변화 (있을 경우)
            volume_change = getattr(m, 'volume_change_pct', 0)
            if volume_change:
                # 거래대금 급증 = 탐욕 (50% 이상 증가 → +0.5)
                score += max(-0.5, min(0.5, volume_change / 100.0))
                components += 1

            # 4. 시장 점수 직접 사용
            market_score = getattr(m, 'market_score', 0)
            score += market_score * 0.5
            components += 1

        if components > 0:
            self._fear_greed_score = max(-1.0, min(1.0, score / components * 2))
        else:
            self._fear_greed_score = 0.0

        self._fear_greed_time = time.time()
        return self._fear_greed_score

    # ─────────── v3.4: 섹터 모멘텀 보정 ───────────

    def _get_sector_bias(self, stock_code: str) -> float:
        """종목의 섹터 모멘텀 보정값. 핫 섹터=양수, 콜드 섹터=음수."""
        if not self._sector_analyzer:
            return 0.0
        try:
            return self._sector_analyzer.get_sector_bias(stock_code)
        except Exception:
            return 0.0

    # ─────────── v3.3: 동적 비중 계산 ───────────

    def get_confidence_size_mult(self, result: 'ExpertAnalysis') -> float:
        """신뢰도 + 심리 지수 기반 투자 비중 승수 (0.5 ~ 1.5).

        - 높은 확신 + 공포장 = 더 크게 매수 (역발상)
        - 낮은 확신 + 탐욕장 = 작게 매수 (조심)
        """
        confidence = result.confidence
        fear_greed = self._calc_fear_greed_index()

        # 기본 비중: 확신도 기반 (0.6 ~ 1.2)
        base = 0.6 + confidence * 0.6

        # 심리 보정: 공포장에 매수하면 비중 ↑ (역발상)
        if result.total_score > 0:  # 매수 신호일 때
            if fear_greed < -0.3:
                # 시장 공포 → 매수 비중 확대 ("남들이 두려워할 때 탐욕")
                base *= 1.2
            elif fear_greed > 0.5:
                # 시장 탐욕 → 매수 비중 축소 (과열 경계)
                base *= 0.8

        return max(0.5, min(1.5, base))

    def _weighted_score(self, result: ExpertAnalysis) -> float:
        """가중 종합 점수 (기술+가치+감성+시장+섹터 융합)."""
        w = self.WEIGHTS
        score = (
            result.technical_score * w["technical"]
            + result.value_score * w.get("value", 0.20)
            + result.pattern_score * w["pattern"]
            + result.sentiment_score * w["sentiment"]
            + result.market_score * w["market"]
            + result.price_level_score * w["price_level"]
        )

        # v3.4: 섹터 모멘텀 보정 (±0.2)
        sector_bias = self._get_sector_bias(result.stock_code)
        if sector_bias != 0:
            score += sector_bias
            if abs(sector_bias) >= 0.1:
                result.reasons.append(
                    f"섹터 {'강세' if sector_bias > 0 else '약세'}({sector_bias:+.2f})")

        # v3.3: 시장 심리 보정
        fg = self._calc_fear_greed_index()
        if abs(fg) >= 0.3:
            # 공포장에서 매수 신호 → 보너스, 탐욕장에서 매수 → 패널티
            if score > 0 and fg < -0.3:
                score += 0.03  # 역발상 보너스
            elif score > 0 and fg > 0.5:
                score -= 0.02  # 과열 패널티

        return max(-1.0, min(1.0, score))

    def _calc_confidence(self, result: ExpertAnalysis) -> float:
        """신뢰도를 계산한다. 여러 지표가 같은 방향이면 높아진다."""
        # 활성화된 데이터 소스만으로 일치도 계산 (없는 데이터는 제외)
        active_scores = []
        if result.technical and result.technical.sma_20 > 0:
            active_scores.append(result.technical_score)
        if result.per != 0 or result.pbr != 0:
            active_scores.append(result.value_score)
        if result.patterns:
            active_scores.append(result.pattern_score)
        if result.sentiment and result.sentiment.news_count > 0:
            active_scores.append(result.sentiment_score)
        if result.market_ctx:
            active_scores.append(result.market_score)
        active_scores.append(result.price_level_score)

        # 방향 일치도 (활성 소스 기준)
        if active_scores:
            positive = sum(1 for s in active_scores if s > 0.05)
            negative = sum(1 for s in active_scores if s < -0.05)
            max_agreement = max(positive, negative)
            agreement_ratio = max_agreement / len(active_scores)
        else:
            agreement_ratio = 0

        # 점수 크기 (절대값이 클수록 확신 높음)
        magnitude = min(abs(result.total_score) * 1.5, 1.0)

        # 데이터 충분성
        data_quality = 0.35
        if result.technical and result.technical.sma_60 > 0:
            data_quality += 0.25
        if result.patterns:
            data_quality += 0.15
        if result.sentiment and result.sentiment.news_count > 0:
            data_quality += 0.1
        if result.market_ctx:
            data_quality += 0.1

        # 거래량 확인 보너스 (거래량이 평균 이상이면 신뢰도 상승)
        volume_bonus = 0
        if result.technical and result.technical.volume_ratio > 1.5:
            volume_bonus = 0.10
        elif result.technical and result.technical.volume_ratio > 1.2:
            volume_bonus = 0.05

        confidence = (
            agreement_ratio * 0.40
            + magnitude * 0.25
            + data_quality * 0.25
            + volume_bonus
        )
        # 핵심 지표(기술+가격위치) 모두 같은 방향이면 추가 보너스
        if result.technical_score * result.price_level_score > 0 and \
           abs(result.technical_score) > 0.1 and abs(result.price_level_score) > 0.1:
            confidence += 0.10

        # 버핏 보너스: 가치+기술 모두 매수 방향이면 확신 상승
        if result.value_score > 0.15 and result.technical_score > 0.1:
            confidence += 0.08  # 저평가 + 기술적 상승 = 최고의 매수 기회

        return min(1.0, confidence)

    def _make_decision(self, result: ExpertAnalysis) -> str:
        """최종 매매 결정 (종목별 동적 임계값 사용)."""
        score = result.total_score
        confidence = result.confidence

        # 종목별 동적 임계값 계산
        atr = result.technical.atr if result.technical else 0
        thresholds = self.get_stock_thresholds(
            result.stock_code, atr=atr, price=result.price)

        buy_thr = thresholds["buy"]
        strong_buy_thr = thresholds["strong_buy"]
        sell_thr = thresholds["sell"]
        strong_sell_thr = thresholds["strong_sell"]

        # 시장 컨텍스트에 따른 추가 조정
        buy_adj = 0
        sell_adj = 0
        if result.market_ctx:
            if result.market_ctx.regime == "trending_down":
                buy_adj = 0.12
                sell_adj = -0.05
            elif result.market_ctx.regime == "volatile":
                buy_adj = 0.08
                sell_adj = -0.03
            elif result.market_ctx.regime == "trending_up":
                buy_adj = -0.03
            if not result.market_ctx.trading_ok:
                return "HOLD"

        # 버핏 원칙: 적자 기업은 절대 매수하지 않는다
        if result.per < 0 and score > 0:
            return "HOLD"

        # 버핏 원칙: 극고PER(50+) 기업은 매수 매우 신중
        if result.per > 50 and score > 0:
            buy_adj += 0.10

        # 거래량 미달 시 매수 보류
        if result.technical and result.technical.volume_ratio < 0.8:
            if score > 0:
                return "HOLD"

        # 장 시작 직후 변동성 구간
        now_str = datetime.now().strftime("%H:%M")
        if "09:00" <= now_str <= "09:15" and score > 0:
            buy_adj += 0.10

        if score >= strong_buy_thr + buy_adj and confidence >= 0.35:
            return "STRONG_BUY"
        elif score >= buy_thr + buy_adj and confidence >= 0.25:
            return "BUY"
        elif score <= strong_sell_thr + sell_adj and confidence >= 0.25:
            return "STRONG_SELL"
        elif score <= sell_thr + sell_adj and confidence >= 0.15:
            return "SELL"
        return "HOLD"

    def _generate_reasons(self, result: ExpertAnalysis) -> list[str]:
        """분석 근거를 생성한다."""
        reasons = []

        # 기술적 분석 근거
        if result.technical:
            t = result.technical
            if t.trend_score > 0.5:
                reasons.append(f"강한 상승추세 (정배열, MACD양전)")
            elif t.trend_score < -0.5:
                reasons.append(f"강한 하락추세 (역배열)")

            if t.rsi < 30:
                reasons.append(f"RSI 과매도({t.rsi:.0f})")
            elif t.rsi > 70:
                reasons.append(f"RSI 과매수({t.rsi:.0f})")

            if t.macd_cross == "golden":
                reasons.append("MACD 골든크로스")
            elif t.macd_cross == "dead":
                reasons.append("MACD 데드크로스")

            if t.stoch_cross == "golden":
                reasons.append(f"스토캐스틱 골든크로스(K={t.stoch_k:.0f})")
            elif t.stoch_cross == "dead":
                reasons.append(f"스토캐스틱 데드크로스(K={t.stoch_k:.0f})")

            if t.bb_position < 0.1:
                reasons.append(f"볼린저 하단 터치(위치={t.bb_position:.2f})")
            elif t.bb_position > 0.9:
                reasons.append(f"볼린저 상단 터치(위치={t.bb_position:.2f})")

            if t.ichimoku_signal in ("strong_buy", "strong_sell"):
                reasons.append(f"일목균형표: {t.ichimoku_signal}")

            if t.volume_ratio > 2:
                reasons.append(f"거래량 급증(x{t.volume_ratio:.1f})")

            if t.vwap > 0:
                diff = (result.price - t.vwap) / t.vwap * 100
                if abs(diff) > 1.5:
                    reasons.append(f"VWAP 대비 {diff:+.1f}%")

        # 가치 분석 근거 (버핏)
        if result.per > 0:
            if result.per < 10:
                reasons.append(f"버핏가치: 저PER({result.per:.1f}) 매력적 가격")
            elif result.per > 40:
                reasons.append(f"버핏경고: 고PER({result.per:.1f}) 고평가 주의")
        if result.pbr > 0:
            if result.pbr < 1.0:
                reasons.append(f"버핏가치: 저PBR({result.pbr:.2f}) 안전마진 확보")
            elif result.pbr > 5.0:
                reasons.append(f"버핏경고: 고PBR({result.pbr:.2f})")

        # 패턴 근거
        if result.patterns:
            for p in result.patterns[:3]:
                direction = "▲" if p.direction == PatternDirection.BULLISH else "▼" if p.direction == PatternDirection.BEARISH else "─"
                reasons.append(f"패턴: {p.name_kr}({direction}, 신뢰도={p.reliability:.0%})")

        # 뉴스 근거
        if result.sentiment and result.sentiment.news_count > 0:
            s = result.sentiment
            reasons.append(f"뉴스감성: {s.signal}({s.overall_score:+.2f}, {s.news_count}건)")
            if s.key_headlines:
                reasons.append(f"  핵심뉴스: {s.key_headlines[0]}")

        # 시장 환경 근거
        if result.market_ctx:
            m = result.market_ctx
            reasons.append(f"시장: {m.regime}(KOSPI={m.kospi_change:+.2f}%)")

        return reasons

    def _calc_target_price(self, analysis: ExpertAnalysis) -> int:
        """목표가를 계산한다.

        여러 기술적 목표치를 종합하여 가장 현실적인 상방 목표가를 산출한다.
        - 볼린저 밴드 상단 (강한 매수 시)
        - 저항선 (다음 저항 가격대)
        - ATR 기반 목표 (ATR × 배수)
        - 추세 강도에 따른 동적 배수
        """
        if not analysis.technical or analysis.price <= 0:
            return 0

        t = analysis.technical
        price = analysis.price

        if analysis.decision in ("STRONG_BUY", "BUY"):
            targets = []

            # 1. 볼린저 밴드 상단 (강한 추세면 상단까지)
            if t.bb_upper and t.bb_upper > price:
                targets.append(int(t.bb_upper))

            # 2. 저항선들 (현재가 위의 모든 저항선)
            if t.resistance_levels:
                for r in t.resistance_levels:
                    if r > price:
                        targets.append(int(r))

            # 3. ATR 기반 목표: 추세 강도에 따라 배수 조정
            if t.atr > 0:
                # 강한 상승추세: ATR × 5~8배, 약한 추세: ATR × 3배
                if t.trend_score > 0.5:
                    atr_mult = 8.0
                elif t.trend_score > 0.2:
                    atr_mult = 5.0
                else:
                    atr_mult = 3.0
                targets.append(int(price + t.atr * atr_mult))

            # 4. 신뢰도/강도 기반 최소 목표 (버핏: 큰 수익 목표)
            if analysis.decision == "STRONG_BUY":
                targets.append(int(price * 1.10))  # 최소 10% (상향)
            else:
                targets.append(int(price * 1.07))  # 최소 7% (상향)

            # 5. 버핏 가치투자 보너스: 저평가 종목은 더 높은 목표
            if analysis.value_score > 0.3:
                targets.append(int(price * 1.15))  # 강한 저평가 → 15% 목표

            # 최종: 중간값 선택 (최소/최대 제외)
            if len(targets) >= 3:
                targets.sort()
                return targets[len(targets) // 2]
            return max(targets) if targets else int(price * 1.05)

        elif analysis.decision in ("STRONG_SELL", "SELL"):
            return int(price * 0.97)

        return 0

    def estimate_upside(self, stock_code: str, candles: list[dict],
                        current_price: dict) -> dict:
        """보유 종목의 추가 상승여력을 분석한다.

        매도 시점 판단에 사용된다. 현재가 기준으로 목표가까지의
        잔여 상승여력과 추세 지속 확률을 계산한다.

        Returns:
            dict with keys:
            - target_price: 분석 기반 목표가
            - upside_pct: 현재가 대비 상승여력 (%)
            - trend_alive: 추세가 살아있는지 (bool)
            - momentum_score: 모멘텀 점수 (-1~1)
            - should_hold: 계속 보유 추천 (bool)
            - reason: 판단 근거
        """
        price = current_price.get("price", 0)
        stock_name = current_price.get("stock_name", stock_code)
        if price <= 0:
            return {"target_price": 0, "upside_pct": 0, "trend_alive": False,
                    "momentum_score": 0, "should_hold": False, "reason": "가격 데이터 없음"}

        analysis = self.full_analysis(stock_code, stock_name, candles, current_price)
        t = analysis.technical

        if not t:
            return {"target_price": 0, "upside_pct": 0, "trend_alive": False,
                    "momentum_score": 0, "should_hold": False, "reason": "기술적 데이터 부족"}

        target = self._calc_target_price(analysis)
        upside_pct = ((target - price) / price * 100) if target > 0 else 0

        # ── 추세 생존 판단 ──
        trend_alive = True
        reasons = []

        # 1. 이동평균 정배열 확인
        ma_aligned = (t.sma_5 > t.sma_20 > t.sma_60) if (t.sma_5 > 0 and t.sma_20 > 0 and t.sma_60 > 0) else False
        if ma_aligned:
            reasons.append("이평선 정배열 유지")

        # 2. MACD 상태
        if t.macd_cross == "dead":
            trend_alive = False
            reasons.append("MACD 데드크로스 발생")
        elif t.macd_histogram > 0:
            reasons.append("MACD 히스토그램 양(+)")

        # 3. RSI 과열 확인
        if t.rsi > 80:
            trend_alive = False
            reasons.append(f"RSI 극과매수({t.rsi:.0f})")
        elif t.rsi > 70:
            reasons.append(f"RSI 과매수 경고({t.rsi:.0f})")

        # 4. 볼린저 밴드 위치
        if t.bb_position > 0.95:
            reasons.append("볼린저 상단 돌파 — 과열 주의")
        elif t.bb_position > 0.7:
            reasons.append("볼린저 상단 접근 중")

        # 5. 거래량 확인
        if t.volume_ratio < 0.5:
            trend_alive = False
            reasons.append("거래량 급감 — 추세 약화")
        elif t.volume_ratio > 1.5:
            reasons.append(f"거래량 증가(x{t.volume_ratio:.1f})")

        # 6. 스토캐스틱
        if t.stoch_cross == "dead" and t.stoch_k > 80:
            trend_alive = False
            reasons.append("스토캐스틱 데드크로스(고점)")

        # ── 모멘텀 종합 점수 ──
        momentum = analysis.technical_score * 0.5 + analysis.price_level_score * 0.3
        if ma_aligned:
            momentum += 0.2
        if t.volume_ratio > 1.2:
            momentum += 0.1
        momentum = max(-1.0, min(1.0, momentum))

        # ── 보유 판단 ──
        should_hold = trend_alive and upside_pct > 1.0 and momentum > -0.3

        # 추세가 죽었어도 목표가까지 여유가 많으면 (10%+) 좀 더 지켜봄
        if not trend_alive and upside_pct > 10.0 and momentum > 0:
            should_hold = True
            reasons.append("추세 약화이나 상승여력 충분 — 관찰 유지")

        return {
            "target_price": target,
            "upside_pct": round(upside_pct, 2),
            "trend_alive": trend_alive,
            "momentum_score": round(momentum, 3),
            "should_hold": should_hold,
            "reason": " | ".join(reasons) if reasons else "분석 데이터 부족",
        }

    # ──────────────────────────────────────────────
    # v2.9: 멀티 타임프레임 확인
    # ──────────────────────────────────────────────

    def multi_timeframe_confirm(self, stock_code: str,
                                 short_candles: list[dict],
                                 long_candles: list[dict],
                                 current_price: dict) -> dict:
        """분봉과 일봉의 추세를 동시에 확인하여 매매 신호를 강화/약화한다.

        - 분봉(단기) + 일봉(장기) 추세가 같은 방향 → 강화
        - 분봉과 일봉 추세가 반대 → 약화
        - 일봉이 강한 상승추세인데 분봉에서 눌림 → 최고의 매수 기회

        Returns:
            dict:
            - alignment: "aligned_up", "aligned_down", "divergent", "pullback_buy"
            - strength_adj: 신호 강도 조정값 (-0.2 ~ +0.2)
            - reason: 판단 근거
        """
        price = current_price.get("price", 0)
        if not short_candles or not long_candles or price <= 0:
            return {"alignment": "unknown", "strength_adj": 0, "reason": "데이터 부족"}

        short_sorted = self._ensure_ascending(short_candles)
        long_sorted = self._ensure_ascending(long_candles)

        short_snap = self.technical.analyze(short_sorted, price)
        long_snap = self.technical.analyze(long_sorted, price)

        short_trend = short_snap.trend_score
        long_trend = long_snap.trend_score

        result = {"alignment": "unknown", "strength_adj": 0, "reason": ""}

        # 장기 상승 + 단기 상승 = 강한 매수
        if long_trend > 0.3 and short_trend > 0.2:
            result.update(
                alignment="aligned_up",
                strength_adj=0.15,
                reason=f"장단기 동시 상승 (일봉={long_trend:+.2f} 분봉={short_trend:+.2f})",
            )

        # 장기 하락 + 단기 하락 = 강한 매도
        elif long_trend < -0.3 and short_trend < -0.2:
            result.update(
                alignment="aligned_down",
                strength_adj=-0.15,
                reason=f"장단기 동시 하락 (일봉={long_trend:+.2f} 분봉={short_trend:+.2f})",
            )

        # 장기 상승 + 단기 눌림(하락) = 최고의 매수 기회 (pullback)
        elif long_trend > 0.3 and short_trend < -0.1:
            # 눌림 매수: 일봉 상승추세에서 분봉 조정 → 반등 기대
            # 단, RSI 과매도 확인
            if short_snap.rsi < 40:
                result.update(
                    alignment="pullback_buy",
                    strength_adj=0.20,
                    reason=f"눌림 매수 기회! (일봉 상승={long_trend:+.2f}, 분봉 조정={short_trend:+.2f}, RSI={short_snap.rsi:.0f})",
                )
            else:
                result.update(
                    alignment="pullback_buy",
                    strength_adj=0.10,
                    reason=f"눌림 매수 (일봉={long_trend:+.2f}, 분봉 조정={short_trend:+.2f})",
                )

        # 장기 하락 + 단기 반등 = 데드캣 바운스 주의
        elif long_trend < -0.3 and short_trend > 0.2:
            result.update(
                alignment="divergent",
                strength_adj=-0.10,
                reason=f"데드캣 바운스 주의 (일봉 하락={long_trend:+.2f}, 분봉 반등={short_trend:+.2f})",
            )

        # 약한 추세 차이
        else:
            diff = abs(long_trend - short_trend)
            if diff < 0.2:
                result.update(alignment="neutral", strength_adj=0,
                              reason="장단기 중립")
            else:
                result.update(
                    alignment="divergent",
                    strength_adj=-0.05,
                    reason=f"장단기 불일치 (일봉={long_trend:+.2f} 분봉={short_trend:+.2f})",
                )

        logger.info(
            "[멀티TF] %s: %s | adj=%+.2f",
            stock_code, result["reason"], result["strength_adj"],
        )
        return result

    def _ensure_ascending(self, candles: list[dict]) -> list[dict]:
        """캔들을 과거순(oldest first)으로 정렬한다."""
        if len(candles) < 2:
            return candles

        # time 또는 date 필드로 판단
        first = candles[0]
        last = candles[-1]
        first_key = first.get("time", first.get("date", ""))
        last_key = last.get("time", last.get("date", ""))

        if first_key and last_key and first_key > last_key:
            return list(reversed(candles))
        return candles
