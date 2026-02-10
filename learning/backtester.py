"""백테스팅 엔진.

과거 데이터를 기반으로 전략의 성과를 시뮬레이션한다.
"""

from dataclasses import dataclass, field
from utils.logger import setup_logger

logger = setup_logger("oshms.learning.backtest")


@dataclass
class BacktestResult:
    """백테스트 결과."""
    strategy_name: str = ""
    params: dict = field(default_factory=dict)
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    total_profit: float = 0
    max_drawdown: float = 0
    win_rate: float = 0
    profit_factor: float = 0
    sharpe_ratio: float = 0
    avg_profit_per_trade: float = 0

    def score(self) -> float:
        """종합 점수 (높을수록 좋음)."""
        if self.total_trades < 5:
            return -999

        wr = self.win_rate / 100
        pf = min(self.profit_factor, 5) / 5
        dd = max(0, 1 - abs(self.max_drawdown) / 10)
        trades_norm = min(self.total_trades / 50, 1)

        return (wr * 0.3 + pf * 0.3 + dd * 0.2 + trades_norm * 0.2) * 100


class Backtester:
    """백테스팅 실행기."""

    def __init__(self, initial_capital: float = 10_000_000):
        self.initial_capital = initial_capital

    def run(self, strategy, candles: list[dict], stock_code: str = "TEST") -> BacktestResult:
        """전략을 과거 데이터로 백테스트한다.

        Args:
            strategy: analyze() 메서드를 가진 전략 객체
            candles: oldest-first 캔들 데이터
            stock_code: 종목 코드
        """
        if len(candles) < 30:
            return BacktestResult(strategy_name=strategy.name)

        result = BacktestResult(strategy_name=strategy.name)
        capital = self.initial_capital
        position = None  # {"price": int, "quantity": int}
        peak_capital = capital
        trades = []

        for i in range(30, len(candles)):
            window = candles[max(0, i - 60):i]
            current = candles[i]
            price = current["close"]

            current_price_data = {
                "price": price,
                "volume": current.get("volume", 0),
                "change_rate": 0,
            }

            from strategy.base import SignalType
            signal = strategy.analyze(stock_code, window, current_price_data)

            # 매수
            if signal.signal_type == SignalType.BUY and position is None:
                if signal.strength >= 0.3:
                    quantity = int(capital * 0.2 / price) if price > 0 else 0
                    if quantity > 0:
                        cost = price * quantity
                        capital -= cost
                        position = {"price": price, "quantity": quantity}

            # 매도
            elif signal.signal_type == SignalType.SELL and position is not None:
                revenue = price * position["quantity"]
                profit = revenue - position["price"] * position["quantity"]
                capital += revenue
                trades.append(profit)
                position = None

            # 손절/익절 체크
            elif position is not None:
                pnl_pct = (price - position["price"]) / position["price"] * 100
                if pnl_pct <= -2.0 or pnl_pct >= 3.0:
                    revenue = price * position["quantity"]
                    profit = revenue - position["price"] * position["quantity"]
                    capital += revenue
                    trades.append(profit)
                    position = None

            # 드로우다운 추적
            total_value = capital + (position["price"] * position["quantity"] if position else 0)
            peak_capital = max(peak_capital, total_value)
            drawdown = ((peak_capital - total_value) / peak_capital * 100) if peak_capital > 0 else 0
            result.max_drawdown = max(result.max_drawdown, drawdown)

        # 미청산 포지션 정리
        if position is not None:
            price = candles[-1]["close"]
            revenue = price * position["quantity"]
            profit = revenue - position["price"] * position["quantity"]
            capital += revenue
            trades.append(profit)

        # 결과 계산
        result.total_trades = len(trades)
        result.wins = sum(1 for t in trades if t > 0)
        result.losses = sum(1 for t in trades if t < 0)
        result.total_profit = sum(trades)
        result.win_rate = (result.wins / result.total_trades * 100) if result.total_trades > 0 else 0

        avg_win = sum(t for t in trades if t > 0) / result.wins if result.wins else 0
        avg_loss = abs(sum(t for t in trades if t < 0) / result.losses) if result.losses else 0
        result.profit_factor = avg_win / avg_loss if avg_loss > 0 else float("inf")
        result.avg_profit_per_trade = result.total_profit / result.total_trades if result.total_trades else 0

        if trades:
            mean = sum(trades) / len(trades)
            variance = sum((t - mean) ** 2 for t in trades) / len(trades)
            std = variance ** 0.5
            result.sharpe_ratio = (mean / std) if std > 0 else 0

        logger.info(
            "백테스트 [%s]: %d건 승률=%.1f%% 수익=%+.0f 점수=%.1f",
            result.strategy_name, result.total_trades,
            result.win_rate, result.total_profit, result.score(),
        )
        return result
