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

from dataclasses import dataclass, field
from datetime import datetime

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

    # 분석 가중치
    WEIGHTS = {
        "technical": 0.35,
        "pattern": 0.15,
        "sentiment": 0.20,
        "market": 0.15,
        "price_level": 0.15,
    }

    # 매매 임계값
    STRONG_BUY_THRESHOLD = 0.45
    BUY_THRESHOLD = 0.25
    SELL_THRESHOLD = -0.20
    STRONG_SELL_THRESHOLD = -0.40

    def __init__(self, api: KISApi | None = None, settings: Settings | None = None):
        self.technical = TechnicalAnalyzer()
        self.pattern_recognizer = PatternRecognizer()
        self.news_analyzer = NewsSentimentAnalyzer()
        self.market_analyzer = MarketContextAnalyzer(api) if api else None
        self._market_ctx: MarketContext | None = None
        self._market_ctx_time: float = 0

    def set_market_context(self, ctx: MarketContext) -> None:
        """시장 컨텍스트를 외부에서 설정한다."""
        self._market_ctx = ctx

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
        """기술적 지표 종합 점수."""
        # 추세(40%) + 모멘텀(30%) + 거래량(20%) + 변동성 영향(10%)
        trend_weight = 0.4
        momentum_weight = 0.3
        volume_weight = 0.2
        volatility_weight = 0.1

        # 모멘텀 반전: 과매도면 매수 기회 → 점수를 반전
        # RSI 30 이하 = 매수기회, RSI 70 이상 = 매도기회
        momentum_adjusted = -snap.momentum_score  # 역발상

        # 변동성이 높으면 신호 감쇠
        volatility_factor = 1.0 - snap.volatility_score * 0.3

        score = (
            snap.trend_score * trend_weight
            + momentum_adjusted * momentum_weight
            + snap.volume_score * volume_weight
        ) * volatility_factor

        # 특수 상황 보너스
        # MACD 골든크로스
        if snap.macd_cross == "golden":
            score += 0.15
        elif snap.macd_cross == "dead":
            score -= 0.15

        # 스토캐스틱 반전
        if snap.stoch_cross == "golden":
            score += 0.1
        elif snap.stoch_cross == "dead":
            score -= 0.1

        # 일목균형표 강한 신호
        if snap.ichimoku_signal == "strong_buy":
            score += 0.1
        elif snap.ichimoku_signal == "strong_sell":
            score -= 0.1

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
        scores = [
            result.technical_score,
            result.pattern_score,
            result.sentiment_score,
            result.market_score,
            result.price_level_score,
        ]

        # 방향 일치도
        positive = sum(1 for s in scores if s > 0.1)
        negative = sum(1 for s in scores if s < -0.1)
        max_agreement = max(positive, negative)
        agreement_ratio = max_agreement / len(scores)

        # 점수 크기
        magnitude = abs(result.total_score)

        # 데이터 충분성
        data_quality = 0.5
        if result.technical and result.technical.sma_60 > 0:
            data_quality += 0.2
        if result.patterns:
            data_quality += 0.1
        if result.sentiment and result.sentiment.news_count > 3:
            data_quality += 0.2

        confidence = agreement_ratio * 0.4 + magnitude * 0.3 + data_quality * 0.3
        return min(1.0, confidence)

    def _make_decision(self, result: ExpertAnalysis) -> str:
        """최종 매매 결정."""
        score = result.total_score
        confidence = result.confidence

        # 시장 컨텍스트에 따른 임계값 조정
        buy_adj = 0
        sell_adj = 0
        if result.market_ctx:
            if result.market_ctx.regime == "trending_down":
                buy_adj = 0.1  # 하락장에서 매수 기준 상향
            elif result.market_ctx.regime == "volatile":
                buy_adj = 0.05
                sell_adj = -0.05
            if not result.market_ctx.trading_ok:
                return "HOLD"

        if score >= self.STRONG_BUY_THRESHOLD + buy_adj and confidence >= 0.5:
            return "STRONG_BUY"
        elif score >= self.BUY_THRESHOLD + buy_adj and confidence >= 0.35:
            return "BUY"
        elif score <= self.STRONG_SELL_THRESHOLD + sell_adj and confidence >= 0.4:
            return "STRONG_SELL"
        elif score <= self.SELL_THRESHOLD + sell_adj and confidence >= 0.3:
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
