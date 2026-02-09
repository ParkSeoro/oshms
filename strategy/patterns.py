"""캔들스틱 패턴 인식 모듈.

26가지 캔들스틱 패턴을 자동 인식하고 매매 신호를 생성한다.
각 패턴은 신뢰도(reliability)와 방향(bullish/bearish)을 포함한다.
"""

from dataclasses import dataclass
from enum import Enum
from utils.logger import setup_logger

logger = setup_logger("oshms.strategy.patterns")


class PatternDirection(Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


@dataclass
class CandlePattern:
    """인식된 캔들 패턴."""
    name: str
    name_kr: str
    direction: PatternDirection
    reliability: float  # 0.0 ~ 1.0
    index: int  # 패턴이 발생한 캔들 인덱스


class PatternRecognizer:
    """캔들스틱 패턴 인식기."""

    def recognize_all(self, candles: list[dict]) -> list[CandlePattern]:
        """모든 캔들 패턴을 인식한다.

        Args:
            candles: 과거순 정렬 (oldest first)

        Returns:
            최근 5봉 내에서 발견된 패턴 리스트
        """
        if len(candles) < 5:
            return []

        patterns = []

        # 단일 캔들 패턴 (마지막 3봉 검사)
        for i in range(max(len(candles) - 3, 1), len(candles)):
            patterns.extend(self._single_candle(candles, i))

        # 이중 캔들 패턴 (마지막 3봉 검사)
        for i in range(max(len(candles) - 3, 2), len(candles)):
            patterns.extend(self._double_candle(candles, i))

        # 삼중 캔들 패턴 (마지막 봉)
        if len(candles) >= 3:
            patterns.extend(self._triple_candle(candles, len(candles) - 1))

        return patterns

    def get_signal_score(self, patterns: list[CandlePattern]) -> float:
        """패턴들의 종합 신호 점수를 계산한다.

        Returns:
            -1.0(강한 하락) ~ +1.0(강한 상승)
        """
        if not patterns:
            return 0.0

        score = 0.0
        for p in patterns:
            weight = p.reliability
            # 최근 패턴에 더 높은 가중치
            recency = 1.0  # 최신이면 가중
            if p.direction == PatternDirection.BULLISH:
                score += weight * recency
            elif p.direction == PatternDirection.BEARISH:
                score -= weight * recency

        return max(-1.0, min(1.0, score))

    # ───────────── 유틸리티 ─────────────

    def _body(self, c: dict) -> float:
        return abs(c["close"] - c["open"])

    def _upper_shadow(self, c: dict) -> float:
        return c["high"] - max(c["close"], c["open"])

    def _lower_shadow(self, c: dict) -> float:
        return min(c["close"], c["open"]) - c["low"]

    def _is_bullish(self, c: dict) -> bool:
        return c["close"] > c["open"]

    def _is_bearish(self, c: dict) -> bool:
        return c["close"] < c["open"]

    def _range(self, c: dict) -> float:
        return c["high"] - c["low"]

    def _avg_body(self, candles: list[dict], end: int, period: int = 10) -> float:
        start = max(0, end - period)
        bodies = [self._body(candles[i]) for i in range(start, end)]
        return sum(bodies) / len(bodies) if bodies else 1

    def _trend(self, candles: list[dict], end: int, period: int = 5) -> str:
        """최근 추세를 판단한다."""
        start = max(0, end - period)
        if end - start < 2:
            return "flat"
        closes = [candles[i]["close"] for i in range(start, end)]
        diff = closes[-1] - closes[0]
        avg_price = sum(closes) / len(closes)
        pct = (diff / avg_price * 100) if avg_price > 0 else 0
        if pct > 1:
            return "up"
        elif pct < -1:
            return "down"
        return "flat"

    # ───────────── 단일 캔들 패턴 ─────────────

    def _single_candle(self, candles: list[dict], i: int) -> list[CandlePattern]:
        patterns = []
        c = candles[i]
        body = self._body(c)
        upper = self._upper_shadow(c)
        lower = self._lower_shadow(c)
        r = self._range(c)
        avg = self._avg_body(candles, i)
        trend = self._trend(candles, i)

        if r == 0:
            return patterns

        # 도지 (Doji) - 몸통이 매우 작음
        if body < r * 0.1:
            patterns.append(CandlePattern(
                "doji", "도지", PatternDirection.NEUTRAL, 0.3, i
            ))

            # 잠자리 도지 (Dragonfly Doji)
            if lower > body * 3 and upper < body * 0.5:
                direction = PatternDirection.BULLISH if trend == "down" else PatternDirection.NEUTRAL
                patterns.append(CandlePattern(
                    "dragonfly_doji", "잠자리도지", direction, 0.6, i
                ))

            # 비석 도지 (Gravestone Doji)
            if upper > body * 3 and lower < body * 0.5:
                direction = PatternDirection.BEARISH if trend == "up" else PatternDirection.NEUTRAL
                patterns.append(CandlePattern(
                    "gravestone_doji", "비석도지", direction, 0.6, i
                ))

        # 망치형 (Hammer) - 하락 추세 후, 아래꼬리 긺
        if trend == "down" and lower >= body * 2 and upper < body * 0.5 and body > 0:
            patterns.append(CandlePattern(
                "hammer", "망치형", PatternDirection.BULLISH, 0.7, i
            ))

        # 교수형 (Hanging Man) - 상승 추세 후 망치 모양
        if trend == "up" and lower >= body * 2 and upper < body * 0.5 and body > 0:
            patterns.append(CandlePattern(
                "hanging_man", "교수형", PatternDirection.BEARISH, 0.5, i
            ))

        # 역망치형 (Inverted Hammer)
        if trend == "down" and upper >= body * 2 and lower < body * 0.5 and body > 0:
            patterns.append(CandlePattern(
                "inverted_hammer", "역망치형", PatternDirection.BULLISH, 0.5, i
            ))

        # 슈팅스타 (Shooting Star)
        if trend == "up" and upper >= body * 2 and lower < body * 0.5 and body > 0:
            patterns.append(CandlePattern(
                "shooting_star", "슈팅스타", PatternDirection.BEARISH, 0.7, i
            ))

        # 마루보즈 (Marubozu) - 꼬리 거의 없는 강한 봉
        if body > avg * 1.5 and upper < body * 0.1 and lower < body * 0.1:
            if self._is_bullish(c):
                patterns.append(CandlePattern(
                    "bullish_marubozu", "양봉마루보즈", PatternDirection.BULLISH, 0.7, i
                ))
            else:
                patterns.append(CandlePattern(
                    "bearish_marubozu", "음봉마루보즈", PatternDirection.BEARISH, 0.7, i
                ))

        # 스피닝탑 (Spinning Top) - 작은 몸통, 양쪽 꼬리
        if body < r * 0.3 and upper > body * 0.5 and lower > body * 0.5 and body > r * 0.05:
            patterns.append(CandlePattern(
                "spinning_top", "스피닝탑", PatternDirection.NEUTRAL, 0.2, i
            ))

        return patterns

    # ───────────── 이중 캔들 패턴 ─────────────

    def _double_candle(self, candles: list[dict], i: int) -> list[CandlePattern]:
        if i < 1:
            return []
        patterns = []
        prev = candles[i - 1]
        curr = candles[i]
        trend = self._trend(candles, i - 1)

        prev_body = self._body(prev)
        curr_body = self._body(curr)

        # 장악형 (Engulfing)
        # 상승장악형
        if (trend == "down" and self._is_bearish(prev) and self._is_bullish(curr)
                and curr["open"] <= prev["close"] and curr["close"] >= prev["open"]
                and curr_body > prev_body):
            patterns.append(CandlePattern(
                "bullish_engulfing", "상승장악형", PatternDirection.BULLISH, 0.8, i
            ))

        # 하락장악형
        if (trend == "up" and self._is_bullish(prev) and self._is_bearish(curr)
                and curr["open"] >= prev["close"] and curr["close"] <= prev["open"]
                and curr_body > prev_body):
            patterns.append(CandlePattern(
                "bearish_engulfing", "하락장악형", PatternDirection.BEARISH, 0.8, i
            ))

        # 관통형 (Piercing Line) - 하락 후 갭다운 오픈, 전봉 50% 이상 회복
        if (trend == "down" and self._is_bearish(prev) and self._is_bullish(curr)
                and curr["open"] < prev["close"]
                and curr["close"] > (prev["open"] + prev["close"]) / 2):
            patterns.append(CandlePattern(
                "piercing_line", "관통형", PatternDirection.BULLISH, 0.6, i
            ))

        # 먹구름형 (Dark Cloud Cover)
        if (trend == "up" and self._is_bullish(prev) and self._is_bearish(curr)
                and curr["open"] > prev["close"]
                and curr["close"] < (prev["open"] + prev["close"]) / 2):
            patterns.append(CandlePattern(
                "dark_cloud", "먹구름형", PatternDirection.BEARISH, 0.6, i
            ))

        # 하라미 (Harami)
        if (self._is_bearish(prev) and self._is_bullish(curr)
                and curr["open"] > prev["close"] and curr["close"] < prev["open"]
                and curr_body < prev_body * 0.5):
            direction = PatternDirection.BULLISH if trend == "down" else PatternDirection.NEUTRAL
            patterns.append(CandlePattern(
                "bullish_harami", "상승하라미", direction, 0.5, i
            ))

        if (self._is_bullish(prev) and self._is_bearish(curr)
                and curr["open"] < prev["close"] and curr["close"] > prev["open"]
                and curr_body < prev_body * 0.5):
            direction = PatternDirection.BEARISH if trend == "up" else PatternDirection.NEUTRAL
            patterns.append(CandlePattern(
                "bearish_harami", "하락하라미", direction, 0.5, i
            ))

        # 트위저 탑/바텀 (Tweezer)
        high_diff = abs(prev["high"] - curr["high"])
        low_diff = abs(prev["low"] - curr["low"])
        avg_range = (self._range(prev) + self._range(curr)) / 2

        if avg_range > 0:
            if trend == "up" and high_diff < avg_range * 0.05:
                patterns.append(CandlePattern(
                    "tweezer_top", "트위저탑", PatternDirection.BEARISH, 0.5, i
                ))
            if trend == "down" and low_diff < avg_range * 0.05:
                patterns.append(CandlePattern(
                    "tweezer_bottom", "트위저바텀", PatternDirection.BULLISH, 0.5, i
                ))

        return patterns

    # ───────────── 삼중 캔들 패턴 ─────────────

    def _triple_candle(self, candles: list[dict], i: int) -> list[CandlePattern]:
        if i < 2:
            return []
        patterns = []
        c1, c2, c3 = candles[i - 2], candles[i - 1], candles[i]
        trend = self._trend(candles, i - 2)

        # 모닝스타 (Morning Star) - 하락 후 반전
        if (trend == "down"
                and self._is_bearish(c1) and self._body(c1) > self._avg_body(candles, i - 2) * 0.8
                and self._body(c2) < self._body(c1) * 0.3
                and self._is_bullish(c3) and c3["close"] > (c1["open"] + c1["close"]) / 2):
            patterns.append(CandlePattern(
                "morning_star", "모닝스타", PatternDirection.BULLISH, 0.85, i
            ))

        # 이브닝스타 (Evening Star) - 상승 후 반전
        if (trend == "up"
                and self._is_bullish(c1) and self._body(c1) > self._avg_body(candles, i - 2) * 0.8
                and self._body(c2) < self._body(c1) * 0.3
                and self._is_bearish(c3) and c3["close"] < (c1["open"] + c1["close"]) / 2):
            patterns.append(CandlePattern(
                "evening_star", "이브닝스타", PatternDirection.BEARISH, 0.85, i
            ))

        # 쓰리 화이트 솔져 (Three White Soldiers)
        if (self._is_bullish(c1) and self._is_bullish(c2) and self._is_bullish(c3)
                and c2["close"] > c1["close"] and c3["close"] > c2["close"]
                and c2["open"] > c1["open"] and c3["open"] > c2["open"]):
            patterns.append(CandlePattern(
                "three_white_soldiers", "적삼병", PatternDirection.BULLISH, 0.8, i
            ))

        # 쓰리 블랙 크로우즈 (Three Black Crows)
        if (self._is_bearish(c1) and self._is_bearish(c2) and self._is_bearish(c3)
                and c2["close"] < c1["close"] and c3["close"] < c2["close"]
                and c2["open"] < c1["open"] and c3["open"] < c2["open"]):
            patterns.append(CandlePattern(
                "three_black_crows", "흑삼병", PatternDirection.BEARISH, 0.8, i
            ))

        return patterns
