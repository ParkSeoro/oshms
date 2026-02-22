"""Enhanced backtesting engine.

Comprehensive backtesting system with realistic fee modeling, slippage simulation,
multiple concurrent positions, dynamic position sizing, Monte Carlo analysis,
and walk-forward testing for Korean stock market strategies.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime

from strategy.base import SignalType
from utils.logger import setup_logger

logger = setup_logger("oshms.analysis.backtester")

# ---------------------------------------------------------------------------
# Korean market fee constants
# ---------------------------------------------------------------------------
DEFAULT_BUY_COMMISSION = 0.00015     # 0.015 %
DEFAULT_SELL_COMMISSION = 0.00015    # 0.015 %
DEFAULT_SELL_TAX = 0.0025           # 0.25 % (securities transaction tax)
DEFAULT_SLIPPAGE = 0.001            # 0.1 %

TRADING_DAYS_PER_YEAR = 245         # KRX average


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class BacktestResult:
    """Comprehensive backtest result container."""

    strategy_name: str = ""

    # Return metrics
    total_return: float = 0.0            # %
    annualized_return: float = 0.0       # %

    # Risk metrics
    max_drawdown: float = 0.0            # %
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0

    # Trade statistics
    win_rate: float = 0.0                # %
    profit_factor: float = 0.0
    avg_win: float = 0.0                 # won
    avg_loss: float = 0.0                # won
    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0
    total_trades: int = 0
    avg_holding_period: float = 0.0      # in candles

    # Detailed data
    equity_curve: list[float] = field(default_factory=list)
    trades: list[dict] = field(default_factory=list)
    monthly_returns: dict[str, float] = field(default_factory=dict)

    def score(self) -> float:
        """Composite quality score (higher is better).

        Combines win rate, profit factor, drawdown control, and trade count
        into a single 0-100 metric.  Returns -999 if fewer than 5 trades.
        """
        if self.total_trades < 5:
            return -999.0

        wr = min(self.win_rate, 100.0) / 100.0
        pf = min(self.profit_factor, 5.0) / 5.0
        dd = max(0.0, 1.0 - abs(self.max_drawdown) / 20.0)
        sr = max(0.0, min(self.sharpe_ratio, 3.0)) / 3.0
        trades_norm = min(self.total_trades / 50.0, 1.0)

        return (wr * 0.25 + pf * 0.25 + dd * 0.2 + sr * 0.15 + trades_norm * 0.15) * 100.0

    def summary(self) -> str:
        """Human-readable one-line summary."""
        return (
            f"[{self.strategy_name}] "
            f"trades={self.total_trades} "
            f"return={self.total_return:+.2f}% "
            f"ann={self.annualized_return:+.2f}% "
            f"MDD={self.max_drawdown:.2f}% "
            f"sharpe={self.sharpe_ratio:.2f} "
            f"sortino={self.sortino_ratio:.2f} "
            f"win={self.win_rate:.1f}% "
            f"PF={self.profit_factor:.2f} "
            f"score={self.score():.1f}"
        )


@dataclass
class _OpenPosition:
    """Internal tracker for an open position during simulation."""

    stock_code: str
    entry_price: float
    quantity: int
    entry_index: int         # candle index at entry
    entry_date: str = ""
    signal_strength: float = 0.0


# ---------------------------------------------------------------------------
# Enhanced Backtester
# ---------------------------------------------------------------------------

class EnhancedBacktester:
    """Full-featured backtesting engine for Korean equities.

    Features beyond the basic ``learning.backtester.Backtester``:
    * Realistic commission and tax modelling (Korean market fees).
    * Configurable slippage.
    * Multiple concurrent positions.
    * Dynamic position sizing proportional to signal strength.
    * Equity curve, monthly returns, and detailed trade log.
    * Sharpe / Sortino / Calmar ratios.
    * Strategy comparison, Monte Carlo simulation, and walk-forward testing.
    """

    def __init__(
        self,
        initial_capital: float = 10_000_000,
        buy_commission: float = DEFAULT_BUY_COMMISSION,
        sell_commission: float = DEFAULT_SELL_COMMISSION,
        sell_tax: float = DEFAULT_SELL_TAX,
        slippage: float = DEFAULT_SLIPPAGE,
        max_positions: int = 5,
        base_position_pct: float = 0.20,
        stop_loss_pct: float = -3.0,
        take_profit_pct: float = 5.0,
        min_signal_strength: float = 0.3,
    ) -> None:
        self.initial_capital = initial_capital
        self.buy_commission = buy_commission
        self.sell_commission = sell_commission
        self.sell_tax = sell_tax
        self.slippage = slippage
        self.max_positions = max_positions
        self.base_position_pct = base_position_pct
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.min_signal_strength = min_signal_strength

    # ------------------------------------------------------------------
    # Fee / slippage helpers
    # ------------------------------------------------------------------

    def _apply_buy_slippage(self, price: float) -> float:
        """Return the effective buy price after adverse slippage."""
        return price * (1.0 + self.slippage)

    def _apply_sell_slippage(self, price: float) -> float:
        """Return the effective sell price after adverse slippage."""
        return price * (1.0 - self.slippage)

    def _buy_cost(self, price: float, quantity: int) -> float:
        """Total cash outflow for a buy (price * qty + commission)."""
        gross = price * quantity
        return gross * (1.0 + self.buy_commission)

    def _sell_proceeds(self, price: float, quantity: int) -> float:
        """Net cash inflow for a sell (price * qty - commission - tax)."""
        gross = price * quantity
        return gross * (1.0 - self.sell_commission - self.sell_tax)

    # ------------------------------------------------------------------
    # Position sizing
    # ------------------------------------------------------------------

    def _position_size(
        self, capital: float, price: float, signal_strength: float,
    ) -> int:
        """Compute quantity to buy based on capital, price, and signal strength.

        Position size scales linearly from ``base_position_pct * 0.5`` at the
        minimum signal strength up to ``base_position_pct * 1.5`` at strength 1.0.
        """
        if price <= 0 or capital <= 0:
            return 0

        # Scale factor: 0.5x at min strength, 1.5x at max strength
        strength_clamped = max(self.min_signal_strength, min(signal_strength, 1.0))
        scale = 0.5 + (strength_clamped - self.min_signal_strength) / max(
            1.0 - self.min_signal_strength, 1e-9
        )
        target_pct = self.base_position_pct * scale

        # Cap at remaining allocatable capital
        max_alloc = capital * min(target_pct, 1.0)
        effective_price = self._apply_buy_slippage(price)
        quantity = int(max_alloc / (effective_price * (1.0 + self.buy_commission)))
        return max(quantity, 0)

    # ------------------------------------------------------------------
    # Core simulation
    # ------------------------------------------------------------------

    def run(
        self,
        strategy,
        candles: list[dict],
        stock_code: str = "TEST",
    ) -> BacktestResult:
        """Run a backtest over *candles* using *strategy*.

        Args:
            strategy: Object with an ``analyze(stock_code, candles, current_price)``
                method that returns a ``Signal``.
            candles: Oldest-first list of OHLCV dicts.  Each dict must contain
                at least ``close`` and ``volume`` keys.  Optionally ``date`` or
                ``datetime`` for calendar-based metrics.
            stock_code: Identifier passed to strategy.analyze().

        Returns:
            A fully populated ``BacktestResult``.
        """
        result = BacktestResult(
            strategy_name=getattr(strategy, "name", strategy.__class__.__name__),
        )

        n = len(candles)
        warmup = 30  # candles needed before first signal

        if n < warmup + 1:
            logger.warning(
                "Not enough candles for backtest (%d < %d). Returning empty result.",
                n, warmup + 1,
            )
            return result

        capital = float(self.initial_capital)
        positions: list[_OpenPosition] = []
        peak_equity = capital
        equity_curve: list[float] = []
        trade_log: list[dict] = []
        period_returns: list[float] = []  # per-candle returns for ratio calcs
        monthly_equity: dict[str, float] = {}

        prev_equity = capital

        for i in range(warmup, n):
            window = candles[max(0, i - 60) : i]
            current = candles[i]
            price = current["close"]
            if price <= 0:
                equity_curve.append(prev_equity)
                continue

            current_date = current.get("date", current.get("datetime", ""))

            current_price_data = {
                "price": price,
                "volume": current.get("volume", 0),
                "change_rate": 0.0,
            }

            signal = strategy.analyze(stock_code, window, current_price_data)

            # --- Stop-loss / take-profit check on open positions ---------
            closed_indices: list[int] = []
            for idx, pos in enumerate(positions):
                pnl_pct = (price - pos.entry_price) / pos.entry_price * 100.0
                reason = ""
                if pnl_pct <= self.stop_loss_pct:
                    reason = f"stop-loss ({pnl_pct:+.2f}%)"
                elif pnl_pct >= self.take_profit_pct:
                    reason = f"take-profit ({pnl_pct:+.2f}%)"

                if reason:
                    sell_price = self._apply_sell_slippage(price)
                    proceeds = self._sell_proceeds(sell_price, pos.quantity)
                    gross_cost = pos.entry_price * pos.quantity
                    profit = proceeds - self._buy_cost(pos.entry_price, pos.quantity)
                    capital += proceeds
                    holding = i - pos.entry_index

                    trade_log.append({
                        "stock_code": pos.stock_code,
                        "entry_price": pos.entry_price,
                        "exit_price": sell_price,
                        "quantity": pos.quantity,
                        "profit": profit,
                        "profit_pct": (sell_price / pos.entry_price - 1.0) * 100.0,
                        "holding_period": holding,
                        "entry_index": pos.entry_index,
                        "exit_index": i,
                        "entry_date": pos.entry_date,
                        "exit_date": current_date,
                        "reason": reason,
                    })
                    closed_indices.append(idx)

            # Remove closed positions in reverse order to keep indices valid
            for idx in sorted(closed_indices, reverse=True):
                positions.pop(idx)

            # --- Strategy signal processing ------------------------------
            if signal.signal_type == SignalType.BUY:
                if (
                    signal.strength >= self.min_signal_strength
                    and len(positions) < self.max_positions
                ):
                    qty = self._position_size(capital, price, signal.strength)
                    if qty > 0:
                        buy_price = self._apply_buy_slippage(price)
                        cost = self._buy_cost(buy_price, qty)
                        if cost <= capital:
                            capital -= cost
                            positions.append(
                                _OpenPosition(
                                    stock_code=stock_code,
                                    entry_price=buy_price,
                                    quantity=qty,
                                    entry_index=i,
                                    entry_date=current_date,
                                    signal_strength=signal.strength,
                                )
                            )

            elif signal.signal_type == SignalType.SELL:
                # Sell all open positions for this stock
                sell_indices: list[int] = []
                for idx, pos in enumerate(positions):
                    if pos.stock_code == stock_code:
                        sell_price = self._apply_sell_slippage(price)
                        proceeds = self._sell_proceeds(sell_price, pos.quantity)
                        profit = proceeds - self._buy_cost(pos.entry_price, pos.quantity)
                        capital += proceeds
                        holding = i - pos.entry_index

                        trade_log.append({
                            "stock_code": pos.stock_code,
                            "entry_price": pos.entry_price,
                            "exit_price": sell_price,
                            "quantity": pos.quantity,
                            "profit": profit,
                            "profit_pct": (sell_price / pos.entry_price - 1.0) * 100.0,
                            "holding_period": holding,
                            "entry_index": pos.entry_index,
                            "exit_index": i,
                            "entry_date": pos.entry_date,
                            "exit_date": current_date,
                            "reason": f"signal-sell (strength={signal.strength:.2f})",
                        })
                        sell_indices.append(idx)

                for idx in sorted(sell_indices, reverse=True):
                    positions.pop(idx)

            # --- Equity tracking -----------------------------------------
            position_value = sum(
                self._sell_proceeds(price, pos.quantity) for pos in positions
            )
            equity = capital + position_value
            equity_curve.append(equity)

            # Peak / drawdown
            peak_equity = max(peak_equity, equity)
            drawdown = (peak_equity - equity) / peak_equity * 100.0 if peak_equity > 0 else 0.0
            result.max_drawdown = max(result.max_drawdown, drawdown)

            # Period return for ratio computation
            ret = (equity - prev_equity) / prev_equity if prev_equity > 0 else 0.0
            period_returns.append(ret)
            prev_equity = equity

            # Monthly bucket
            if current_date and len(current_date) >= 7:
                month_key = current_date[:7]
                monthly_equity[month_key] = equity

        # --- Close any remaining positions at last price -----------------
        if positions and n > 0:
            last_price = candles[-1]["close"]
            last_date = candles[-1].get("date", candles[-1].get("datetime", ""))
            for pos in positions:
                sell_price = self._apply_sell_slippage(last_price)
                proceeds = self._sell_proceeds(sell_price, pos.quantity)
                profit = proceeds - self._buy_cost(pos.entry_price, pos.quantity)
                capital += proceeds
                holding = (n - 1) - pos.entry_index

                trade_log.append({
                    "stock_code": pos.stock_code,
                    "entry_price": pos.entry_price,
                    "exit_price": sell_price,
                    "quantity": pos.quantity,
                    "profit": profit,
                    "profit_pct": (sell_price / pos.entry_price - 1.0) * 100.0,
                    "holding_period": holding,
                    "entry_index": pos.entry_index,
                    "exit_index": n - 1,
                    "entry_date": pos.entry_date,
                    "exit_date": last_date,
                    "reason": "end-of-data liquidation",
                })
            positions.clear()

            # Final equity point after liquidation
            final_equity = capital
            if equity_curve:
                equity_curve[-1] = final_equity

        # --- Compute result metrics --------------------------------------
        result.equity_curve = equity_curve
        result.trades = trade_log
        result.total_trades = len(trade_log)

        final_equity = equity_curve[-1] if equity_curve else self.initial_capital

        # Total return
        result.total_return = (
            (final_equity - self.initial_capital) / self.initial_capital * 100.0
        )

        # Annualized return
        n_periods = len(equity_curve)
        if n_periods > 0 and final_equity > 0 and self.initial_capital > 0:
            years = n_periods / TRADING_DAYS_PER_YEAR
            if years > 0:
                growth = final_equity / self.initial_capital
                if growth > 0:
                    result.annualized_return = (growth ** (1.0 / years) - 1.0) * 100.0
                else:
                    result.annualized_return = -100.0

        # Trade-level statistics
        profits = [t["profit"] for t in trade_log]
        wins = [p for p in profits if p > 0]
        losses = [p for p in profits if p < 0]

        result.win_rate = (len(wins) / len(profits) * 100.0) if profits else 0.0
        result.avg_win = (sum(wins) / len(wins)) if wins else 0.0
        result.avg_loss = (sum(losses) / len(losses)) if losses else 0.0

        total_win = sum(wins) if wins else 0.0
        total_loss = abs(sum(losses)) if losses else 0.0
        result.profit_factor = (total_win / total_loss) if total_loss > 0 else (
            float("inf") if total_win > 0 else 0.0
        )

        # Consecutive wins / losses
        result.max_consecutive_wins = self._max_consecutive(profits, positive=True)
        result.max_consecutive_losses = self._max_consecutive(profits, positive=False)

        # Average holding period
        if trade_log:
            result.avg_holding_period = (
                sum(t["holding_period"] for t in trade_log) / len(trade_log)
            )

        # Sharpe ratio (annualized, using period returns)
        result.sharpe_ratio = self._sharpe(period_returns)

        # Sortino ratio (annualized, penalises only downside deviation)
        result.sortino_ratio = self._sortino(period_returns)

        # Calmar ratio = annualized return / max drawdown
        if result.max_drawdown > 0:
            result.calmar_ratio = result.annualized_return / result.max_drawdown
        else:
            result.calmar_ratio = float("inf") if result.annualized_return > 0 else 0.0

        # Monthly returns
        result.monthly_returns = self._compute_monthly_returns(monthly_equity)

        logger.info("Backtest complete: %s", result.summary())
        return result

    # ------------------------------------------------------------------
    # Strategy comparison
    # ------------------------------------------------------------------

    def compare_strategies(
        self,
        strategies: list,
        candles: list[dict],
        stock_code: str = "TEST",
    ) -> list[dict]:
        """Run several strategies on the same candle data and return a comparison table.

        Args:
            strategies: List of strategy objects with ``analyze()`` method.
            candles: Shared candle data.
            stock_code: Identifier for the instrument.

        Returns:
            A list of dicts, one per strategy, sorted by score (descending).
            Each dict contains the key metrics from ``BacktestResult``.
        """
        rows: list[dict] = []
        for strat in strategies:
            name = getattr(strat, "name", strat.__class__.__name__)
            logger.info("Comparing strategy: %s", name)
            res = self.run(strat, candles, stock_code)
            rows.append({
                "strategy": res.strategy_name,
                "total_return": round(res.total_return, 2),
                "annualized_return": round(res.annualized_return, 2),
                "max_drawdown": round(res.max_drawdown, 2),
                "sharpe": round(res.sharpe_ratio, 3),
                "sortino": round(res.sortino_ratio, 3),
                "calmar": round(res.calmar_ratio, 3),
                "win_rate": round(res.win_rate, 1),
                "profit_factor": round(res.profit_factor, 2),
                "total_trades": res.total_trades,
                "avg_holding": round(res.avg_holding_period, 1),
                "score": round(res.score(), 1),
            })

        rows.sort(key=lambda r: r["score"], reverse=True)

        logger.info("Strategy comparison (%d strategies):", len(rows))
        for i, row in enumerate(rows, 1):
            logger.info(
                "  #%d  %-20s  return=%+.2f%%  MDD=%.2f%%  sharpe=%.2f  score=%.1f",
                i, row["strategy"], row["total_return"],
                row["max_drawdown"], row["sharpe"], row["score"],
            )

        return rows

    # ------------------------------------------------------------------
    # Monte Carlo simulation
    # ------------------------------------------------------------------

    def monte_carlo_simulation(
        self,
        strategy,
        candles: list[dict],
        stock_code: str = "TEST",
        n_simulations: int = 100,
        seed: int | None = None,
    ) -> dict:
        """Bootstrap Monte Carlo simulation by reshuffling trade outcomes.

        Runs the strategy once to collect trade P&L values, then resamples
        those values with replacement to build ``n_simulations`` synthetic
        equity curves.  This estimates the distribution of possible outcomes
        without needing new market data.

        Args:
            strategy: Strategy object.
            candles: Historical candle data.
            stock_code: Instrument identifier.
            n_simulations: Number of Monte Carlo paths.
            seed: Optional random seed for reproducibility.

        Returns:
            Dictionary with:
            - ``median_return``: Median total return across simulations.
            - ``mean_return``: Mean total return.
            - ``ci_5``, ``ci_95``: 5th and 95th percentile total returns.
            - ``ci_25``, ``ci_75``: 25th and 75th percentile total returns.
            - ``worst``, ``best``: Extreme outcomes.
            - ``median_max_drawdown``, ``ci_95_max_drawdown``: Drawdown stats.
            - ``prob_profit``: Fraction of simulations with positive return.
            - ``simulated_returns``: Full list of simulated total returns.
            - ``simulated_drawdowns``: Full list of simulated max drawdowns.
            - ``base_result``: The original ``BacktestResult``.
        """
        if seed is not None:
            random.seed(seed)

        # Run baseline
        base_result = self.run(strategy, candles, stock_code)
        if base_result.total_trades < 2:
            logger.warning(
                "Monte Carlo skipped: only %d trades in base run.",
                base_result.total_trades,
            )
            return {
                "median_return": base_result.total_return,
                "mean_return": base_result.total_return,
                "ci_5": base_result.total_return,
                "ci_95": base_result.total_return,
                "ci_25": base_result.total_return,
                "ci_75": base_result.total_return,
                "worst": base_result.total_return,
                "best": base_result.total_return,
                "median_max_drawdown": base_result.max_drawdown,
                "ci_95_max_drawdown": base_result.max_drawdown,
                "prob_profit": 1.0 if base_result.total_return > 0 else 0.0,
                "simulated_returns": [base_result.total_return],
                "simulated_drawdowns": [base_result.max_drawdown],
                "base_result": base_result,
            }

        trade_pnls = [t["profit"] for t in base_result.trades]
        n_trades = len(trade_pnls)

        sim_returns: list[float] = []
        sim_drawdowns: list[float] = []

        for _ in range(n_simulations):
            # Resample trade P&L with replacement
            resampled = random.choices(trade_pnls, k=n_trades)

            # Build synthetic equity curve
            equity = float(self.initial_capital)
            peak = equity
            max_dd = 0.0

            for pnl in resampled:
                equity += pnl
                if equity > peak:
                    peak = equity
                dd = (peak - equity) / peak * 100.0 if peak > 0 else 0.0
                max_dd = max(max_dd, dd)

            total_ret = (equity - self.initial_capital) / self.initial_capital * 100.0
            sim_returns.append(total_ret)
            sim_drawdowns.append(max_dd)

        sim_returns.sort()
        sim_drawdowns.sort()

        def _percentile(sorted_list: list[float], pct: float) -> float:
            idx = int(len(sorted_list) * pct / 100.0)
            idx = max(0, min(idx, len(sorted_list) - 1))
            return sorted_list[idx]

        result = {
            "median_return": _percentile(sim_returns, 50),
            "mean_return": sum(sim_returns) / len(sim_returns),
            "ci_5": _percentile(sim_returns, 5),
            "ci_95": _percentile(sim_returns, 95),
            "ci_25": _percentile(sim_returns, 25),
            "ci_75": _percentile(sim_returns, 75),
            "worst": sim_returns[0],
            "best": sim_returns[-1],
            "median_max_drawdown": _percentile(sim_drawdowns, 50),
            "ci_95_max_drawdown": _percentile(sim_drawdowns, 95),
            "prob_profit": sum(1 for r in sim_returns if r > 0) / len(sim_returns),
            "simulated_returns": sim_returns,
            "simulated_drawdowns": sim_drawdowns,
            "base_result": base_result,
        }

        logger.info(
            "Monte Carlo (%d sims): median=%.2f%% CI=[%.2f%%, %.2f%%] "
            "prob_profit=%.1f%% MDD_95=%.2f%%",
            n_simulations,
            result["median_return"],
            result["ci_5"],
            result["ci_95"],
            result["prob_profit"] * 100.0,
            result["ci_95_max_drawdown"],
        )

        return result

    # ------------------------------------------------------------------
    # Walk-forward testing
    # ------------------------------------------------------------------

    def walk_forward_test(
        self,
        strategy,
        candles: list[dict],
        stock_code: str = "TEST",
        train_pct: float = 0.70,
    ) -> dict:
        """Walk-forward (out-of-sample) test.

        Splits *candles* into a training (in-sample) window and a testing
        (out-of-sample) window.  Both windows are backtested independently
        so that in-sample over-fitting can be detected.

        Args:
            strategy: Strategy object.
            candles: Full historical candle data.
            stock_code: Instrument identifier.
            train_pct: Fraction of data for training (0.0 .. 1.0).

        Returns:
            Dictionary with ``train_result``, ``test_result``, ``overfit_ratio``,
            ``consistency_score``, and split metadata.
        """
        n = len(candles)
        split = int(n * train_pct)

        if split < 60 or (n - split) < 30:
            logger.warning(
                "Walk-forward split too small (train=%d, test=%d). Need >= 60/30.",
                split, n - split,
            )
            empty = BacktestResult(strategy_name=getattr(strategy, "name", ""))
            return {
                "train_result": empty,
                "test_result": empty,
                "overfit_ratio": 0.0,
                "consistency_score": 0.0,
                "train_candles": split,
                "test_candles": n - split,
                "total_candles": n,
            }

        train_candles = candles[:split]
        test_candles = candles[split:]

        logger.info(
            "Walk-forward split: train=%d candles, test=%d candles (%.0f%%/%.0f%%)",
            len(train_candles), len(test_candles),
            train_pct * 100, (1 - train_pct) * 100,
        )

        train_result = self.run(strategy, train_candles, stock_code)
        test_result = self.run(strategy, test_candles, stock_code)

        # Overfit ratio: how much worse is out-of-sample vs in-sample?
        # A ratio near 1.0 means the strategy generalises well.
        if train_result.total_return != 0:
            overfit_ratio = test_result.total_return / train_result.total_return
        else:
            overfit_ratio = 0.0 if test_result.total_return == 0 else float("inf")

        # Consistency score (0-100): penalises large gap between IS and OOS
        # and rewards profitable OOS.
        consistency = 0.0
        if test_result.total_return > 0:
            consistency += 40.0  # OOS is profitable
        if overfit_ratio > 0:
            # Closer to 1.0 is better; cap contribution at 40
            ratio_quality = max(0.0, 1.0 - abs(1.0 - min(overfit_ratio, 2.0)))
            consistency += ratio_quality * 40.0
        if test_result.sharpe_ratio > 0:
            consistency += min(test_result.sharpe_ratio / 2.0, 1.0) * 20.0

        result = {
            "train_result": train_result,
            "test_result": test_result,
            "overfit_ratio": round(overfit_ratio, 4),
            "consistency_score": round(consistency, 1),
            "train_candles": len(train_candles),
            "test_candles": len(test_candles),
            "total_candles": n,
        }

        logger.info(
            "Walk-forward: train=%.2f%% test=%.2f%% overfit=%.4f consistency=%.1f",
            train_result.total_return, test_result.total_return,
            result["overfit_ratio"], result["consistency_score"],
        )

        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _max_consecutive(profits: list[float], positive: bool) -> int:
        """Count the longest winning or losing streak."""
        max_streak = 0
        current = 0
        for p in profits:
            if (positive and p > 0) or (not positive and p < 0):
                current += 1
                max_streak = max(max_streak, current)
            else:
                current = 0
        return max_streak

    @staticmethod
    def _sharpe(returns: list[float], risk_free: float = 0.0) -> float:
        """Annualised Sharpe ratio from a list of period returns."""
        if len(returns) < 2:
            return 0.0
        mean = sum(returns) / len(returns) - risk_free / TRADING_DAYS_PER_YEAR
        variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
        std = math.sqrt(variance) if variance > 0 else 0.0
        if std == 0:
            return 0.0
        return (mean / std) * math.sqrt(TRADING_DAYS_PER_YEAR)

    @staticmethod
    def _sortino(returns: list[float], risk_free: float = 0.0) -> float:
        """Annualised Sortino ratio (downside deviation only)."""
        if len(returns) < 2:
            return 0.0
        mean = sum(returns) / len(returns) - risk_free / TRADING_DAYS_PER_YEAR
        downside = [r for r in returns if r < 0]
        if not downside:
            return float("inf") if mean > 0 else 0.0
        down_var = sum(r ** 2 for r in downside) / len(returns)
        down_std = math.sqrt(down_var) if down_var > 0 else 0.0
        if down_std == 0:
            return 0.0
        return (mean / down_std) * math.sqrt(TRADING_DAYS_PER_YEAR)

    @staticmethod
    def _compute_monthly_returns(monthly_equity: dict[str, float]) -> dict[str, float]:
        """Compute month-over-month percentage returns from equity snapshots."""
        if len(monthly_equity) < 2:
            return {}

        months = sorted(monthly_equity.keys())
        result: dict[str, float] = {}
        for i in range(1, len(months)):
            prev = monthly_equity[months[i - 1]]
            curr = monthly_equity[months[i]]
            if prev > 0:
                result[months[i]] = (curr - prev) / prev * 100.0
        return result
