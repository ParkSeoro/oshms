"""매매 전략 기본 클래스."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum


class SignalType(Enum):
    """매매 신호 유형."""
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass
class Signal:
    """매매 신호."""
    signal_type: SignalType
    stock_code: str
    reason: str
    strength: float = 0.0  # 0.0 ~ 1.0, 신호 강도
    target_price: int = 0

    @property
    def is_buy(self) -> bool:
        return self.signal_type == SignalType.BUY

    @property
    def is_sell(self) -> bool:
        return self.signal_type == SignalType.SELL


class BaseStrategy(ABC):
    """매매 전략 기본 클래스."""

    name: str = "base"

    @abstractmethod
    def analyze(self, stock_code: str, candles: list[dict], current_price: dict) -> Signal:
        """시세 데이터를 분석하여 매매 신호를 생성한다.

        Args:
            stock_code: 종목 코드
            candles: 분봉/일봉 데이터 리스트
            current_price: 현재가 정보 dict

        Returns:
            Signal 객체
        """

    @staticmethod
    def calc_sma(prices: list[float], period: int) -> list[float]:
        """단순 이동평균을 계산한다."""
        if len(prices) < period:
            return []
        sma = []
        for i in range(len(prices) - period + 1):
            avg = sum(prices[i : i + period]) / period
            sma.append(avg)
        return sma

    @staticmethod
    def calc_ema(prices: list[float], period: int) -> list[float]:
        """지수 이동평균을 계산한다."""
        if len(prices) < period:
            return []
        k = 2 / (period + 1)
        ema = [sum(prices[:period]) / period]
        for price in prices[period:]:
            ema.append(price * k + ema[-1] * (1 - k))
        return ema

    @staticmethod
    def calc_rsi(prices: list[float], period: int = 14) -> float:
        """RSI를 계산한다."""
        if len(prices) < period + 1:
            return 50.0

        deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
        recent = deltas[-(period):]

        gains = [d for d in recent if d > 0]
        losses = [-d for d in recent if d < 0]

        avg_gain = sum(gains) / period if gains else 0
        avg_loss = sum(losses) / period if losses else 0

        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    @staticmethod
    def calc_bollinger_bands(
        prices: list[float], period: int = 20, num_std: float = 2.0
    ) -> tuple[float, float, float]:
        """볼린저 밴드를 계산한다. (상단, 중간, 하단)"""
        if len(prices) < period:
            mid = prices[-1] if prices else 0
            return mid, mid, mid

        recent = prices[-period:]
        mid = sum(recent) / period
        variance = sum((p - mid) ** 2 for p in recent) / period
        std = variance**0.5
        upper = mid + num_std * std
        lower = mid - num_std * std
        return upper, mid, lower

    @staticmethod
    def calc_macd(
        prices: list[float], fast: int = 12, slow: int = 26, signal: int = 9
    ) -> tuple[float, float, float]:
        """MACD를 계산한다. (MACD선, 시그널선, 히스토그램)"""
        if len(prices) < slow:
            return 0, 0, 0

        k_fast = 2 / (fast + 1)
        k_slow = 2 / (slow + 1)

        ema_fast = sum(prices[:fast]) / fast
        ema_slow = sum(prices[:slow]) / slow

        for p in prices[fast:]:
            ema_fast = p * k_fast + ema_fast * (1 - k_fast)
        for p in prices[slow:]:
            ema_slow = p * k_slow + ema_slow * (1 - k_slow)

        macd_line = ema_fast - ema_slow

        # 간이 시그널 (최근 MACD 값이 없으므로 단순화)
        signal_line = macd_line * 0.8
        histogram = macd_line - signal_line

        return macd_line, signal_line, histogram
