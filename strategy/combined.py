"""복합 전략: 여러 전략의 신호를 종합하여 최종 판단.

각 전략의 신호 강도를 가중 평균하여 최종 매매 신호를 생성한다.
"""

from strategy.base import BaseStrategy, Signal, SignalType
from strategy.scalping import ScalpingStrategy
from strategy.momentum import MomentumStrategy
from utils.logger import setup_logger

logger = setup_logger("oshms.strategy.combined")


class CombinedStrategy(BaseStrategy):
    """복합 전략 (스캘핑 + 모멘텀 종합)."""

    name = "combined"

    def __init__(
        self,
        scalping_weight: float = 0.6,
        momentum_weight: float = 0.4,
        buy_threshold: float = 0.3,
        sell_threshold: float = 0.3,
    ):
        self.strategies: list[tuple[BaseStrategy, float]] = [
            (ScalpingStrategy(), scalping_weight),
            (MomentumStrategy(), momentum_weight),
        ]
        self.buy_threshold = buy_threshold
        self.sell_threshold = sell_threshold

    def analyze(self, stock_code: str, candles: list[dict], current_price: dict) -> Signal:
        buy_score = 0.0
        sell_score = 0.0
        reasons = []
        total_weight = sum(w for _, w in self.strategies)

        for strategy, weight in self.strategies:
            signal = strategy.analyze(stock_code, candles, current_price)
            normalized_weight = weight / total_weight

            if signal.is_buy:
                buy_score += signal.strength * normalized_weight
                reasons.append(f"[{strategy.name}:매수] {signal.reason}")
            elif signal.is_sell:
                sell_score += signal.strength * normalized_weight
                reasons.append(f"[{strategy.name}:매도] {signal.reason}")
            else:
                reasons.append(f"[{strategy.name}:중립] {signal.reason}")

        reason_str = " | ".join(reasons)

        logger.debug(
            "[%s] 종합점수 매수=%.2f 매도=%.2f", stock_code, buy_score, sell_score
        )

        if buy_score >= self.buy_threshold and buy_score > sell_score:
            return Signal(
                SignalType.BUY,
                stock_code,
                reason_str,
                strength=buy_score,
            )
        elif sell_score >= self.sell_threshold and sell_score > buy_score:
            return Signal(
                SignalType.SELL,
                stock_code,
                reason_str,
                strength=sell_score,
            )

        return Signal(SignalType.HOLD, stock_code, reason_str, strength=0)
