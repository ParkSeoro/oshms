"""실시간 차트 시각화 모듈.

matplotlib 기반 전문 주식 차트를 Tkinter 윈도우에 임베딩한다.
캔들스틱, 거래량, 이동평균선, 볼린저 밴드, RSI, MACD,
지지/저항선, 매매 마커를 포함하는 프리미엄 다크 테마 차트를 제공한다.

사용 예시::

    import tkinter as tk
    from analysis.chart import StockChart

    root = tk.Tk()
    chart = StockChart(root)
    chart.pack(fill="both", expand=True)
    chart.update_chart(candles, trades=trades, support=[48000], resistance=[52000])
    root.mainloop()
"""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass, field
from typing import Any

from utils.logger import setup_logger

logger = setup_logger("oshms.analysis.chart")

try:
    import matplotlib

    matplotlib.use("Agg")  # 백엔드를 먼저 설정 (Tk 임베딩 전 필수)

    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    import matplotlib.ticker as mticker
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    from matplotlib.figure import Figure
    from matplotlib.patches import FancyBboxPatch
    import matplotlib.patheffects as pe

    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False
    logger.warning("matplotlib 미설치 — 차트 기능이 비활성화됩니다. pip install matplotlib")


# ──────────────────────────────────────────────────────────────────
# 프리미엄 다크 테마 컬러 팔레트
# ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ChartColors:
    """차트 전용 색상 팔레트 (OSHMS 다크 테마)."""

    bg: str = "#13151c"
    bg2: str = "#1a1d28"
    surface: str = "#222637"
    card: str = "#282d42"
    border: str = "#3a4060"

    fg: str = "#f0f2f8"
    fg2: str = "#c0c6dc"
    dim: str = "#8890ac"
    dim2: str = "#5e6484"

    accent: str = "#6c8cff"
    accent2: str = "#a78bfa"
    accent_soft: str = "#2e3a68"

    green: str = "#22c55e"
    green_dim: str = "#16a34a"
    green_bg: str = "#162d20"
    red: str = "#ef4444"
    red_dim: str = "#dc2626"
    red_bg: str = "#2d1616"
    yellow: str = "#fbbf24"
    orange: str = "#f97316"

    # 이동평균선
    ma5: str = "#fbbf24"       # 5일 — 골드
    ma20: str = "#6c8cff"      # 20일 — 블루
    ma60: str = "#a78bfa"      # 60일 — 퍼플

    # 볼린저 밴드
    bb_edge: str = "#3a4060"
    bb_fill: str = "#2e3a68"

    # MACD
    macd_line: str = "#6c8cff"
    macd_signal: str = "#f97316"
    macd_hist_pos: str = "#22c55e"
    macd_hist_neg: str = "#ef4444"

    # 거래량 (양봉/음봉)
    vol_up: str = "#1e5631"
    vol_down: str = "#5e1a1a"

    # 지지/저항
    support: str = "#22c55e"
    resistance: str = "#ef4444"

    # 그리드
    grid: str = "#282d42"


COLORS = ChartColors()


# ──────────────────────────────────────────────────────────────────
# 데이터 클래스
# ──────────────────────────────────────────────────────────────────

@dataclass
class TradeMarker:
    """차트에 표시할 매매 마커.

    Attributes:
        index: 캔들 데이터 내 인덱스 (0-based, 시간순)
        price: 체결 가격
        side: ``"BUY"`` 또는 ``"SELL"``
        label: 툴팁/라벨 텍스트 (선택)
    """

    index: int
    price: float
    side: str  # "BUY" | "SELL"
    label: str = ""


# ──────────────────────────────────────────────────────────────────
# 헬퍼 함수
# ──────────────────────────────────────────────────────────────────

def _safe_float(value: Any, default: float = 0.0) -> float:
    """값을 안전하게 float으로 변환한다."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _compute_rsi(closes: list[float], period: int = 14) -> list[float]:
    """RSI를 계산한다.

    데이터가 부족하면 빈 리스트를 반환한다.
    """
    if len(closes) < period + 1:
        return []

    rsi_values: list[float] = []
    gains: list[float] = []
    losses: list[float] = []

    for i in range(1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gains.append(max(delta, 0))
        losses.append(max(-delta, 0))

    # 초기 평균
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    # 첫 period 개는 None 대신 50으로 채움
    rsi_values.extend([50.0] * period)

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        if avg_loss == 0:
            rsi_values.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsi_values.append(100.0 - (100.0 / (1.0 + rs)))

    return rsi_values


def _compute_sma(data: list[float], period: int) -> list[float]:
    """단순 이동평균(SMA)을 계산한다.

    데이터가 period보다 짧은 구간은 ``None``으로 채운다.
    """
    result: list[float | None] = [None] * (period - 1)
    for i in range(period - 1, len(data)):
        window = data[i - period + 1 : i + 1]
        result.append(sum(window) / period)
    return result


def _compute_ema(data: list[float], period: int) -> list[float]:
    """지수 이동평균(EMA)을 계산한다."""
    if len(data) < period:
        return [None] * len(data)

    multiplier = 2.0 / (period + 1)
    ema_values: list[float | None] = [None] * (period - 1)
    # 첫 EMA = SMA
    ema_values.append(sum(data[:period]) / period)

    for i in range(period, len(data)):
        prev = ema_values[-1]
        ema_values.append((data[i] - prev) * multiplier + prev)

    return ema_values


def _compute_macd(
    closes: list[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """MACD (line, signal, histogram)를 계산한다."""
    ema_fast = _compute_ema(closes, fast)
    ema_slow = _compute_ema(closes, slow)

    macd_line: list[float | None] = []
    for f, s in zip(ema_fast, ema_slow):
        if f is not None and s is not None:
            macd_line.append(f - s)
        else:
            macd_line.append(None)

    # MACD signal (MACD line의 EMA)
    valid_macd = [v for v in macd_line if v is not None]
    if len(valid_macd) < signal:
        return macd_line, [None] * len(macd_line), [None] * len(macd_line)

    macd_signal_vals = _compute_ema(valid_macd, signal)
    # 앞쪽 None 채우기
    none_count = len(macd_line) - len(valid_macd)
    signal_full: list[float | None] = [None] * none_count + macd_signal_vals

    histogram: list[float | None] = []
    for m, s in zip(macd_line, signal_full):
        if m is not None and s is not None:
            histogram.append(m - s)
        else:
            histogram.append(None)

    return macd_line, signal_full, histogram


def _compute_bollinger(
    closes: list[float], period: int = 20, num_std: float = 2.0
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """볼린저 밴드 (upper, middle, lower)를 계산한다."""
    middle = _compute_sma(closes, period)
    upper: list[float | None] = []
    lower: list[float | None] = []

    for i, mid in enumerate(middle):
        if mid is None:
            upper.append(None)
            lower.append(None)
        else:
            window = closes[max(0, i - period + 1) : i + 1]
            if len(window) < period:
                upper.append(None)
                lower.append(None)
            else:
                mean = sum(window) / len(window)
                variance = sum((x - mean) ** 2 for x in window) / len(window)
                std = variance ** 0.5
                upper.append(mid + num_std * std)
                lower.append(mid - num_std * std)

    return upper, middle, lower


# ──────────────────────────────────────────────────────────────────
# StockChart 위젯
# ──────────────────────────────────────────────────────────────────

class StockChart(tk.Frame):
    """Tkinter에 임베딩 가능한 프리미엄 주식 차트 위젯.

    matplotlib ``FigureCanvasTkAgg``를 사용하여 캔들스틱, 거래량,
    이동평균, 볼린저 밴드, RSI, MACD, 지지/저항, 매매 마커를 표시한다.

    Args:
        master: 부모 Tkinter 위젯.
        figsize: 그림 크기 ``(width, height)`` (인치).
        dpi: 해상도 (dots per inch).
        colors: 색상 팔레트. ``None``이면 기본 다크 테마 사용.
    """

    def __init__(
        self,
        master: tk.Widget,
        figsize: tuple[float, float] = (14, 9),
        dpi: int = 100,
        colors: ChartColors | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(master, bg=COLORS.bg, **kwargs)
        self._colors = colors or COLORS

        if not HAS_MATPLOTLIB:
            self._show_fallback_message()
            return

        self._fig: Figure | None = None
        self._canvas: FigureCanvasTkAgg | None = None
        self._axes: dict[str, Any] = {}

        self._create_figure(figsize, dpi)

    # ── 초기화 ──────────────────────────────────────────────────

    def _show_fallback_message(self) -> None:
        """matplotlib가 없을 때 대체 메시지를 표시한다."""
        c = self._colors
        lbl = tk.Label(
            self,
            text="차트를 표시하려면 matplotlib를 설치하세요.\n\npip install matplotlib",
            bg=c.bg,
            fg=c.dim,
            font=("Segoe UI", 13),
            justify="center",
        )
        lbl.pack(expand=True)

    def _create_figure(self, figsize: tuple[float, float], dpi: int) -> None:
        """matplotlib Figure와 서브플롯을 생성한다."""
        c = self._colors

        self._fig = Figure(figsize=figsize, dpi=dpi, facecolor=c.bg)

        # 서브플롯 레이아웃: 메인(캔들+볼린저+MA) | 거래량 | RSI | MACD
        # gridspec 비율: 메인 5, 거래량 1.2, RSI 1.5, MACD 1.5
        gs = self._fig.add_gridspec(
            4, 1,
            height_ratios=[5, 1.2, 1.5, 1.5],
            hspace=0.06,
        )

        ax_main = self._fig.add_subplot(gs[0])
        ax_vol = self._fig.add_subplot(gs[1], sharex=ax_main)
        ax_rsi = self._fig.add_subplot(gs[2], sharex=ax_main)
        ax_macd = self._fig.add_subplot(gs[3], sharex=ax_main)

        self._axes = {
            "main": ax_main,
            "volume": ax_vol,
            "rsi": ax_rsi,
            "macd": ax_macd,
        }

        # 모든 축에 다크 테마 적용
        for name, ax in self._axes.items():
            ax.set_facecolor(c.bg)
            ax.tick_params(colors=c.dim, labelsize=8)
            ax.spines["top"].set_visible(False)
            ax.spines["right"].set_visible(False)
            ax.spines["bottom"].set_color(c.border)
            ax.spines["left"].set_color(c.border)
            ax.yaxis.label.set_color(c.fg2)
            ax.xaxis.label.set_color(c.fg2)
            ax.grid(True, color=c.grid, linewidth=0.4, alpha=0.5)

            # x축 라벨은 MACD(맨 아래)에만 표시
            if name != "macd":
                plt.setp(ax.get_xticklabels(), visible=False)

        self._fig.subplots_adjust(left=0.07, right=0.95, top=0.95, bottom=0.06)

        # Tk 캔버스에 임베딩
        self._canvas = FigureCanvasTkAgg(self._fig, master=self)
        widget = self._canvas.get_tk_widget()
        widget.configure(bg=c.bg, highlightthickness=0)
        widget.pack(fill="both", expand=True)

    # ── 공개 API ────────────────────────────────────────────────

    def get_figure(self) -> "Figure | None":
        """내부 matplotlib Figure 객체를 반환한다."""
        return self._fig

    def update_chart(
        self,
        candles: list[dict],
        *,
        trades: list[TradeMarker] | None = None,
        support: list[float] | None = None,
        resistance: list[float] | None = None,
        title: str = "",
    ) -> None:
        """차트를 새로운 데이터로 갱신한다.

        Args:
            candles: OHLCV 캔들 리스트 (시간순, oldest-first).
                각 dict는 ``open``, ``high``, ``low``, ``close``,
                ``volume`` 키를 포함해야 한다.
                ``date`` 또는 ``time`` 키가 있으면 x축 라벨로 사용한다.
            trades: 매매 마커 리스트 (선택).
            support: 지지선 가격 리스트 (선택).
            resistance: 저항선 가격 리스트 (선택).
            title: 차트 제목 (선택).
        """
        if not HAS_MATPLOTLIB or self._fig is None:
            return

        if not candles:
            logger.debug("빈 캔들 데이터 — 차트 갱신 건너뜀")
            return

        # 데이터가 newest-first이면 reverse (date/time으로 판단)
        candles = self._ensure_oldest_first(candles)

        # 데이터 추출
        opens = [_safe_float(c.get("open", 0)) for c in candles]
        highs = [_safe_float(c.get("high", 0)) for c in candles]
        lows = [_safe_float(c.get("low", 0)) for c in candles]
        closes = [_safe_float(c.get("close", 0)) for c in candles]
        volumes = [_safe_float(c.get("volume", 0)) for c in candles]
        x = list(range(len(candles)))

        # 라벨 (date 또는 time)
        labels = []
        for c in candles:
            lbl = c.get("date", c.get("time", ""))
            labels.append(str(lbl))

        # 지표 계산
        sma5 = _compute_sma(closes, 5)
        sma20 = _compute_sma(closes, 20)
        sma60 = _compute_sma(closes, 60)
        bb_upper, bb_middle, bb_lower = _compute_bollinger(closes, 20)
        rsi_values = _compute_rsi(closes, 14)
        macd_line, macd_signal, macd_hist = _compute_macd(closes)

        # 차트 초기화
        for ax in self._axes.values():
            ax.clear()

        c = self._colors

        # ── 1) 캔들스틱 (메인) ──────────────────────────────────
        self._draw_candlesticks(x, opens, highs, lows, closes)

        # ── 2) 이동평균선 ───────────────────────────────────────
        self._draw_line(x, sma5, c.ma5, "MA5", linewidth=1.0, alpha=0.9)
        self._draw_line(x, sma20, c.ma20, "MA20", linewidth=1.1, alpha=0.9)
        self._draw_line(x, sma60, c.ma60, "MA60", linewidth=1.2, alpha=0.8)

        # ── 3) 볼린저 밴드 ──────────────────────────────────────
        self._draw_bollinger(x, bb_upper, bb_middle, bb_lower)

        # ── 4) 지지/저항선 ──────────────────────────────────────
        self._draw_levels(x, support or [], resistance or [])

        # ── 5) 매매 마커 ────────────────────────────────────────
        self._draw_trade_markers(trades or [])

        # 메인 축 설정
        ax_main = self._axes["main"]
        ax_main.set_ylabel("가격", fontsize=9, color=c.fg2)
        if title:
            ax_main.set_title(
                title, fontsize=12, fontweight="bold", color=c.fg, pad=8
            )
        ax_main.legend(
            loc="upper left",
            fontsize=7,
            frameon=True,
            facecolor=c.surface,
            edgecolor=c.border,
            labelcolor=c.fg2,
            framealpha=0.85,
        )
        ax_main.yaxis.set_major_formatter(
            mticker.FuncFormatter(lambda v, _: f"{v:,.0f}")
        )

        # ── 6) 거래량 ──────────────────────────────────────────
        self._draw_volume(x, opens, closes, volumes)

        # ── 7) RSI ──────────────────────────────────────────────
        self._draw_rsi(x, rsi_values)

        # ── 8) MACD ─────────────────────────────────────────────
        self._draw_macd(x, macd_line, macd_signal, macd_hist)

        # x축 라벨 (MACD 축)
        ax_macd = self._axes["macd"]
        if labels:
            step = max(1, len(labels) // 10)
            tick_pos = list(range(0, len(labels), step))
            tick_labels = [labels[i] for i in tick_pos]
            ax_macd.set_xticks(tick_pos)
            ax_macd.set_xticklabels(tick_labels, rotation=30, fontsize=7, color=c.dim)

        # 모든 축 다크 테마 재적용 (clear 이후)
        for name, ax in self._axes.items():
            ax.set_facecolor(c.bg)
            ax.tick_params(colors=c.dim, labelsize=8)
            ax.spines["top"].set_visible(False)
            ax.spines["right"].set_visible(False)
            ax.spines["bottom"].set_color(c.border)
            ax.spines["left"].set_color(c.border)
            ax.grid(True, color=c.grid, linewidth=0.4, alpha=0.5)
            if name != "macd":
                plt.setp(ax.get_xticklabels(), visible=False)

        self._fig.subplots_adjust(left=0.07, right=0.95, top=0.95, bottom=0.06)
        self._canvas.draw_idle()

    def clear_chart(self) -> None:
        """차트를 모두 지운다."""
        if not HAS_MATPLOTLIB or self._fig is None:
            return
        for ax in self._axes.values():
            ax.clear()
        self._canvas.draw_idle()

    def destroy(self) -> None:
        """위젯을 파괴하고 matplotlib 리소스를 해제한다."""
        if self._fig is not None:
            plt.close(self._fig)
            self._fig = None
        super().destroy()

    # ── 내부: 캔들스틱 ──────────────────────────────────────────

    def _draw_candlesticks(
        self,
        x: list[int],
        opens: list[float],
        highs: list[float],
        lows: list[float],
        closes: list[float],
    ) -> None:
        """캔들스틱 차트를 그린다."""
        c = self._colors
        ax = self._axes["main"]

        body_width = 0.6
        wick_width = 0.15

        for i in range(len(x)):
            o, h, l, cl = opens[i], highs[i], lows[i], closes[i]
            is_up = cl >= o

            color = c.green if is_up else c.red
            edge_color = c.green_dim if is_up else c.red_dim

            # 심지 (wick)
            ax.plot(
                [x[i], x[i]], [l, h],
                color=edge_color, linewidth=wick_width * 2, solid_capstyle="round",
            )

            # 몸통 (body)
            body_bottom = min(o, cl)
            body_height = abs(cl - o)
            if body_height < (h - l) * 0.01:
                # 도지(doji) — 얇은 가로선
                body_height = (h - l) * 0.01 or 0.5

            rect = plt.Rectangle(
                (x[i] - body_width / 2, body_bottom),
                body_width,
                body_height,
                facecolor=color,
                edgecolor=edge_color,
                linewidth=0.5,
                alpha=0.95,
                zorder=3,
            )
            ax.add_patch(rect)

    # ── 내부: 라인 플롯 ─────────────────────────────────────────

    def _draw_line(
        self,
        x: list[int],
        values: list[float | None],
        color: str,
        label: str,
        linewidth: float = 1.0,
        alpha: float = 1.0,
        ax_key: str = "main",
    ) -> None:
        """None이 포함된 데이터를 구간별로 나눠서 라인을 그린다."""
        ax = self._axes[ax_key]

        seg_x: list[int] = []
        seg_y: list[float] = []

        for i, v in enumerate(values):
            if v is not None:
                seg_x.append(x[i] if i < len(x) else i)
                seg_y.append(v)
            else:
                if seg_x:
                    ax.plot(
                        seg_x, seg_y,
                        color=color, linewidth=linewidth, alpha=alpha,
                        label=label if i == len(values) - 1 or not seg_x else None,
                    )
                    seg_x, seg_y = [], []

        if seg_x:
            ax.plot(seg_x, seg_y, color=color, linewidth=linewidth, alpha=alpha, label=label)

    # ── 내부: 볼린저 밴드 ───────────────────────────────────────

    def _draw_bollinger(
        self,
        x: list[int],
        upper: list[float | None],
        middle: list[float | None],
        lower: list[float | None],
    ) -> None:
        """볼린저 밴드를 음영 영역으로 그린다."""
        c = self._colors
        ax = self._axes["main"]

        # 유효 구간만 추출
        valid_x, valid_upper, valid_lower = [], [], []
        for i in range(len(x)):
            if (
                i < len(upper)
                and i < len(lower)
                and upper[i] is not None
                and lower[i] is not None
            ):
                valid_x.append(x[i])
                valid_upper.append(upper[i])
                valid_lower.append(lower[i])

        if valid_x:
            ax.fill_between(
                valid_x, valid_lower, valid_upper,
                color=c.bb_fill, alpha=0.18, zorder=1,
            )
            ax.plot(valid_x, valid_upper, color=c.bb_edge, linewidth=0.7, alpha=0.5, linestyle="--")
            ax.plot(valid_x, valid_lower, color=c.bb_edge, linewidth=0.7, alpha=0.5, linestyle="--")

        # 중간선 (SMA20과 동일하므로 레이블 생략)
        valid_mx, valid_mid = [], []
        for i in range(len(x)):
            if i < len(middle) and middle[i] is not None:
                valid_mx.append(x[i])
                valid_mid.append(middle[i])
        if valid_mx:
            ax.plot(
                valid_mx, valid_mid,
                color=c.bb_edge, linewidth=0.6, alpha=0.4, linestyle=":",
            )

    # ── 내부: 지지/저항선 ───────────────────────────────────────

    def _draw_levels(
        self,
        x: list[int],
        support: list[float],
        resistance: list[float],
    ) -> None:
        """지지/저항선을 수평 점선으로 그린다."""
        c = self._colors
        ax = self._axes["main"]

        if not x:
            return

        x_min, x_max = x[0], x[-1]

        for level in support:
            ax.hlines(
                level, x_min, x_max,
                colors=c.support, linestyles="dashed", linewidth=0.9, alpha=0.7,
                zorder=2,
            )
            ax.text(
                x_max + 0.5, level, f"S {level:,.0f}",
                color=c.support, fontsize=7, va="center", alpha=0.8,
            )

        for level in resistance:
            ax.hlines(
                level, x_min, x_max,
                colors=c.resistance, linestyles="dashed", linewidth=0.9, alpha=0.7,
                zorder=2,
            )
            ax.text(
                x_max + 0.5, level, f"R {level:,.0f}",
                color=c.resistance, fontsize=7, va="center", alpha=0.8,
            )

    # ── 내부: 매매 마커 ─────────────────────────────────────────

    def _draw_trade_markers(self, trades: list[TradeMarker]) -> None:
        """매수/매도 마커를 캔들 위에 표시한다."""
        c = self._colors
        ax = self._axes["main"]

        for t in trades:
            is_buy = t.side.upper() == "BUY"
            marker = "^" if is_buy else "v"
            color = c.green if is_buy else c.red
            y_offset_factor = 0.997 if is_buy else 1.003

            ax.scatter(
                t.index, t.price * y_offset_factor,
                marker=marker,
                color=color,
                s=90,
                zorder=5,
                edgecolors="white",
                linewidths=0.5,
            )

            if t.label:
                ax.annotate(
                    t.label,
                    xy=(t.index, t.price),
                    xytext=(t.index + 0.5, t.price * (1.015 if is_buy else 0.985)),
                    fontsize=6,
                    color=color,
                    alpha=0.85,
                    arrowprops=dict(arrowstyle="-", color=c.dim2, lw=0.5),
                )

    # ── 내부: 거래량 ────────────────────────────────────────────

    def _draw_volume(
        self,
        x: list[int],
        opens: list[float],
        closes: list[float],
        volumes: list[float],
    ) -> None:
        """거래량 막대를 그린다."""
        c = self._colors
        ax = self._axes["volume"]

        bar_colors = [
            c.vol_up if closes[i] >= opens[i] else c.vol_down
            for i in range(len(x))
        ]
        edge_colors = [
            c.green_dim if closes[i] >= opens[i] else c.red_dim
            for i in range(len(x))
        ]

        ax.bar(
            x, volumes,
            width=0.6,
            color=bar_colors,
            edgecolor=edge_colors,
            linewidth=0.3,
            alpha=0.85,
        )

        ax.set_ylabel("거래량", fontsize=8, color=c.fg2)
        ax.yaxis.set_major_formatter(
            mticker.FuncFormatter(self._format_volume)
        )

    @staticmethod
    def _format_volume(value: float, _pos: int) -> str:
        """거래량을 축약 표기한다 (예: 1.2M, 500K)."""
        if value >= 1_000_000:
            return f"{value / 1_000_000:.1f}M"
        if value >= 1_000:
            return f"{value / 1_000:.0f}K"
        return f"{value:.0f}"

    # ── 내부: RSI ───────────────────────────────────────────────

    def _draw_rsi(self, x: list[int], rsi_values: list[float]) -> None:
        """RSI 차트를 그린다."""
        c = self._colors
        ax = self._axes["rsi"]

        if not rsi_values:
            return

        # RSI 데이터를 x와 정렬
        rsi_x = x[: len(rsi_values)]

        ax.plot(rsi_x, rsi_values, color=c.accent, linewidth=1.0, alpha=0.9)

        # 과매수/과매도 영역
        ax.axhline(70, color=c.red, linewidth=0.6, linestyle="--", alpha=0.5)
        ax.axhline(30, color=c.green, linewidth=0.6, linestyle="--", alpha=0.5)
        ax.axhline(50, color=c.dim2, linewidth=0.4, linestyle=":", alpha=0.4)

        # 과매수/과매도 배경 음영
        if rsi_x:
            ax.fill_between(
                rsi_x, 70, 100,
                color=c.red, alpha=0.06, zorder=0,
            )
            ax.fill_between(
                rsi_x, 0, 30,
                color=c.green, alpha=0.06, zorder=0,
            )

        ax.set_ylabel("RSI", fontsize=8, color=c.fg2)
        ax.set_ylim(0, 100)

        # 현재 RSI 값 표시
        if rsi_values:
            current_rsi = rsi_values[-1]
            rsi_color = c.red if current_rsi > 70 else c.green if current_rsi < 30 else c.accent
            ax.text(
                rsi_x[-1] + 0.5, current_rsi,
                f"{current_rsi:.1f}",
                color=rsi_color, fontsize=7, va="center", fontweight="bold",
            )

    # ── 내부: MACD ──────────────────────────────────────────────

    def _draw_macd(
        self,
        x: list[int],
        macd_line: list[float | None],
        macd_signal: list[float | None],
        macd_hist: list[float | None],
    ) -> None:
        """MACD 차트를 그린다."""
        c = self._colors
        ax = self._axes["macd"]

        # MACD 히스토그램 (양/음 색상)
        for i in range(len(x)):
            if i < len(macd_hist) and macd_hist[i] is not None:
                color = c.macd_hist_pos if macd_hist[i] >= 0 else c.macd_hist_neg
                ax.bar(
                    x[i], macd_hist[i],
                    width=0.5, color=color, alpha=0.6, zorder=1,
                )

        # MACD line
        self._draw_line(x, macd_line, c.macd_line, "MACD", linewidth=1.0, ax_key="macd")
        # Signal line
        self._draw_line(x, macd_signal, c.macd_signal, "Signal", linewidth=0.9, ax_key="macd")

        ax.axhline(0, color=c.dim2, linewidth=0.4, linestyle=":", alpha=0.5)
        ax.set_ylabel("MACD", fontsize=8, color=c.fg2)

        ax.legend(
            loc="upper left",
            fontsize=6,
            frameon=True,
            facecolor=c.surface,
            edgecolor=c.border,
            labelcolor=c.fg2,
            framealpha=0.8,
        )

    # ── 내부: 유틸리티 ──────────────────────────────────────────

    @staticmethod
    def _ensure_oldest_first(candles: list[dict]) -> list[dict]:
        """캔들 리스트가 시간순(oldest-first)인지 확인하고, 아니면 뒤집는다.

        KIS API는 newest-first로 반환하므로 필요시 reverse한다.
        """
        if len(candles) < 2:
            return candles

        first_time = candles[0].get("date", candles[0].get("time", ""))
        last_time = candles[-1].get("date", candles[-1].get("time", ""))

        if first_time and last_time and str(first_time) > str(last_time):
            return list(reversed(candles))

        return candles
