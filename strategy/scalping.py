"""볼린저 밴드 + RSI 기반 스캘핑 전략.

하단 밴드 터치 + RSI 과매도 → 매수
상단 밴드 터치 + RSI 과매수 → 매도
"""

from strategy.base import BaseStrategy, Signal, SignalType
from utils.logger import setup_logger

logger = setup_logger("oshms.strategy.scalping")


class ScalpingStrategy(BaseStrategy):
    """볼린저 밴드 + RSI 스캘핑 전략."""

    name = "scalping"

    def __init__(
        self,
        bb_period: int = 20,
        bb_std: float = 2.0,
        rsi_period: int = 14,
        rsi_oversold: float = 30.0,
        rsi_overbought: float = 70.0,
    ):
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.rsi_period = rsi_period
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought

    def analyze(self, stock_code: str, candles: list[dict], current_price: dict) -> Signal:
        if len(candles) < self.bb_period:
            return Signal(SignalType.HOLD, stock_code, "데이터 부족")

        closes = [c["close"] for c in reversed(candles) if c.get("close", 0) > 0]
        if len(closes) < self.bb_period:
            return Signal(SignalType.HOLD, stock_code, "유효 데이터 부족")

        price = current_price.get("price", closes[-1])

        # 볼린저 밴드
        upper, mid, lower = self.calc_bollinger_bands(closes, self.bb_period, self.bb_std)

        # RSI
        rsi = self.calc_rsi(closes, self.rsi_period)

        # 밴드 위치 (0=하단, 0.5=중간, 1=상단)
        band_width = upper - lower if upper != lower else 1
        band_position = (price - lower) / band_width

        logger.debug(
            "[%s] 가격=%d BB(%.0f/%.0f/%.0f) RSI=%.1f 밴드위치=%.2f",
            stock_code, price, upper, mid, lower, rsi, band_position,
        )

        # 매수 신호: 하단밴드 근처 + RSI 과매도
        if band_position <= 0.1 and rsi < self.rsi_oversold:
            strength = min(1.0, (self.rsi_oversold - rsi) / 30 + (0.1 - band_position) * 5)
            return Signal(
                SignalType.BUY,
                stock_code,
                f"볼린저하단터치(위치={band_position:.2f}) + RSI과매도({rsi:.1f})",
                strength=strength,
                target_price=int(mid),
            )

        # 매수 신호 (약): 하단밴드 근접
        if band_position <= 0.2 and rsi < 40:
            strength = 0.3 + (40 - rsi) / 100
            return Signal(
                SignalType.BUY,
                stock_code,
                f"볼린저하단접근(위치={band_position:.2f}) + RSI낮음({rsi:.1f})",
                strength=strength,
                target_price=int(mid),
            )

        # 매도 신호: 상단밴드 근처 + RSI 과매수
        if band_position >= 0.9 and rsi > self.rsi_overbought:
            strength = min(1.0, (rsi - self.rsi_overbought) / 30 + (band_position - 0.9) * 5)
            return Signal(
                SignalType.SELL,
                stock_code,
                f"볼린저상단터치(위치={band_position:.2f}) + RSI과매수({rsi:.1f})",
                strength=strength,
            )

        # 매도 신호 (약): 상단밴드 근접
        if band_position >= 0.8 and rsi > 60:
            strength = 0.3 + (rsi - 60) / 100
            return Signal(
                SignalType.SELL,
                stock_code,
                f"볼린저상단접근(위치={band_position:.2f}) + RSI높음({rsi:.1f})",
                strength=strength,
            )

        return Signal(SignalType.HOLD, stock_code, f"중립(밴드위치={band_position:.2f}, RSI={rsi:.1f})")
