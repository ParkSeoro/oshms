"""전문가 전략 엔진.

기술적 분석, 캔들 패턴, 뉴스 감성, 시장 컨텍스트를
종합하여 전문가 수준의 매매 판단을 수행한다.

분석 가중치:
- 고급 기술적 분석 (추세/모멘텀/변동성/거래량): 35%
- 캔들스틱 패턴: 15%
- 뉴스 감성 분석: 20%
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
    pattern_score: float = 0
    sentiment_score: float = 0
    market_score: float = 0
    price_level_score: float = 0

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
            f"══ {self.stock_name}({self.stock_code}) 전문가 분석 ══",
            f"현재가: {self.price:,}원 | 판정: {self.decision}",
            f"종합점수: {self.total_score:+.3f} (신뢰도: {self.confidence:.0%})",
            f"  기술분석: {self.technical_score:+.3f}",
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

    # 분석 가중치 (기술적 분석 비중 상향, 뉴스 비중 하향 - 뉴스는 지연되므로)
    WEIGHTS = {
        "technical": 0.40,
        "pattern": 0.15,
        "sentiment": 0.10,
        "market": 0.15,
        "price_level": 0.20,
    }

    # 기본 매매 임계값 (종목별 동적으로 조정됨)
    STRONG_BUY_THRESHOLD = 0.25
    BUY_THRESHOLD = 0.15
    SELL_THRESHOLD = -0.08
    STRONG_SELL_THRESHOLD = -0.20

    # 종목 프로필 파일
    STOCK_PROFILES_FILE = Path("data/stock_profiles.json")

    def __init__(self, api: KISApi | None = None, settings: Settings | None = None):
        self.technical = TechnicalAnalyzer()
        self.pattern_recognizer = PatternRecognizer()
        self.news_analyzer = NewsSentimentAnalyzer()
        self.market_analyzer = MarketContextAnalyzer(api) if api else None
        self._market_ctx: MarketContext | None = None
        self._market_ctx_time: float = 0

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
        for key in ("technical", "pattern", "sentiment", "market", "price_level"):
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

        # ── 2. 캔들스틱 패턴 ──
        patterns = self.pattern_recognizer.recognize_all(sorted_candles)
        result.patterns = patterns
        result.pattern_score = self.pattern_recognizer.get_signal_score(patterns)

        # ── 3. 뉴스 감성 분석 ──
        try:
            sentiment = self.news_analyzer.analyze(stock_code, stock_name)
            result.sentiment = sentiment
            result.sentiment_score = sentiment.overall_score
        except Exception as e:
            logger.warning("[%s] 뉴스 분석 실패: %s", stock_code, e)
            result.sentiment_score = 0

        # ── 4. 시장 컨텍스트 ──
        if self._market_ctx:
            result.market_ctx = self._market_ctx
            result.market_score = self._market_ctx.market_score
        else:
            result.market_score = 0

        # ── 5. 가격 위치 분석 ──
        result.price_level_score = self._analyze_price_level(tech_snap, result.price)

        # ── 종합 점수 계산 ──
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

    def _weighted_score(self, result: ExpertAnalysis) -> float:
        """가중 종합 점수."""
        w = self.WEIGHTS
        score = (
            result.technical_score * w["technical"]
            + result.pattern_score * w["pattern"]
            + result.sentiment_score * w["sentiment"]
            + result.market_score * w["market"]
            + result.price_level_score * w["price_level"]
        )
        return max(-1.0, min(1.0, score))

    def _calc_confidence(self, result: ExpertAnalysis) -> float:
        """신뢰도를 계산한다. 여러 지표가 같은 방향이면 높아진다."""
        # 활성화된 데이터 소스만으로 일치도 계산 (없는 데이터는 제외)
        active_scores = []
        if result.technical and result.technical.sma_20 > 0:
            active_scores.append(result.technical_score)
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
        """목표가를 계산한다."""
        if not analysis.technical or analysis.price <= 0:
            return 0

        t = analysis.technical

        if analysis.decision in ("STRONG_BUY", "BUY"):
            # 매수 시 목표가: 볼린저 중간선 또는 저항선
            targets = []
            if t.bb_middle > analysis.price:
                targets.append(int(t.bb_middle))
            if t.resistance_levels:
                targets.append(int(t.resistance_levels[0]))
            if t.vwap > analysis.price:
                targets.append(int(t.vwap))
            return min(targets) if targets else int(analysis.price * 1.03)

        elif analysis.decision in ("STRONG_SELL", "SELL"):
            return int(analysis.price * 0.98)

        return 0

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
