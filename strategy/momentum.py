"""이동평균선 + 거래량 기반 모멘텀 단타 전략.

5일선이 20일선 골든크로스 + 거래량 급증 → 매수
5일선이 20일선 데드크로스 → 매도
"""

from strategy.base import BaseStrategy, Signal, SignalType
from utils.logger import setup_logger

logger = setup_logger("oshms.strategy.momentum")


class MomentumStrategy(BaseStrategy):
    """이동평균 + 거래량 모멘텀 전략."""

    name = "momentum"

    def __init__(
        self,
        fast_period: int = 5,
        slow_period: int = 20,
        volume_multiplier: float = 2.0,
    ):
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.volume_multiplier = volume_multiplier

    def analyze(self, stock_code: str, candles: list[dict], current_price: dict) -> Signal:
        if len(candles) < self.slow_period + 2:
            return Signal(SignalType.HOLD, stock_code, "데이터 부족")

        # 최신순 → 과거순으로 정렬
        sorted_candles = list(reversed(candles))
        closes = [c["close"] for c in sorted_candles if c.get("close", 0) > 0]
        volumes = [c["volume"] for c in sorted_candles if c.get("volume", 0) >= 0]

        if len(closes) < self.slow_period + 2:
            return Signal(SignalType.HOLD, stock_code, "유효 데이터 부족")

        price = current_price.get("price", closes[-1])

        # 이동평균 계산
        fast_ma = self.calc_sma(closes, self.fast_period)
        slow_ma = self.calc_sma(closes, self.slow_period)

        if len(fast_ma) < 2 or len(slow_ma) < 2:
            return Signal(SignalType.HOLD, stock_code, "이동평균 계산 불가")

        # 현재와 이전 이동평균
        fast_now, fast_prev = fast_ma[-1], fast_ma[-2]
        slow_now, slow_prev = slow_ma[-1], slow_ma[-2]

        # 거래량 분석
        avg_volume = sum(volumes[-self.slow_period:]) / self.slow_period if volumes else 0
        current_volume = current_price.get("volume", volumes[-1] if volumes else 0)
        volume_ratio = current_volume / avg_volume if avg_volume > 0 else 1.0

        # MACD 보조 지표
        macd_line, signal_line, histogram = self.calc_macd(closes)

        logger.debug(
            "[%s] 가격=%d 단기MA=%.0f 장기MA=%.0f 거래량비=%.1f MACD=%.1f",
            stock_code, price, fast_now, slow_now, volume_ratio, macd_line,
        )

        # 골든크로스 감지: 단기가 장기를 상향 돌파
        golden_cross = fast_prev <= slow_prev and fast_now > slow_now
        # 데드크로스 감지: 단기가 장기를 하향 돌파
        dead_cross = fast_prev >= slow_prev and fast_now < slow_now

        # 매수 조건: 골든크로스 + 거래량 증가
        if golden_cross and volume_ratio >= self.volume_multiplier:
            strength = min(1.0, 0.5 + volume_ratio / 10)
            return Signal(
                SignalType.BUY,
                stock_code,
                f"골든크로스({self.fast_period}/{self.slow_period}) + 거래량급증(x{volume_ratio:.1f})",
                strength=strength,
            )

        # 매수 조건 (약): 단기MA 위에서 거래량 급증
        if (
            fast_now > slow_now
            and price > fast_now
            and volume_ratio >= self.volume_multiplier * 1.5
            and macd_line > 0
        ):
            strength = 0.4 + volume_ratio / 20
            return Signal(
                SignalType.BUY,
                stock_code,
                f"상승추세+거래량폭증(x{volume_ratio:.1f})+MACD양전({macd_line:.0f})",
                strength=min(1.0, strength),
            )

        # 매도 조건: 데드크로스
        if dead_cross:
            strength = min(1.0, 0.6 + volume_ratio / 10)
            return Signal(
                SignalType.SELL,
                stock_code,
                f"데드크로스({self.fast_period}/{self.slow_period})",
                strength=strength,
            )

        # 매도 조건: 단기MA 이탈 + MACD 음전
        if price < fast_now and fast_now < slow_now and macd_line < 0:
            return Signal(
                SignalType.SELL,
                stock_code,
                f"하락추세(가격<단기MA<장기MA)+MACD음전({macd_line:.0f})",
                strength=0.5,
            )

        return Signal(
            SignalType.HOLD,
            stock_code,
            f"중립(단기MA={fast_now:.0f}, 장기MA={slow_now:.0f}, 거래량비={volume_ratio:.1f})",
        )
