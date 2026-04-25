"""고급 기술적 지표 모듈.

전문 트레이더 수준의 기술적 분석 지표를 제공한다.
- VWAP (거래량 가중 평균가)
- Stochastic Oscillator (스토캐스틱)
- ATR (평균 진폭)
- 일목균형표 (Ichimoku Cloud)
- 지지/저항선 자동 탐지
- 멀티 타임프레임 분석
"""

from dataclasses import dataclass


@dataclass
class TechnicalSnapshot:
    """기술적 지표 종합 스냅샷."""
    # 추세 지표
    sma_5: float = 0
    sma_20: float = 0
    sma_60: float = 0
    ema_12: float = 0
    ema_26: float = 0

    # MACD
    macd_line: float = 0
    macd_signal: float = 0
    macd_histogram: float = 0
    macd_cross: str = ""  # "golden", "dead", ""

    # 모멘텀
    rsi: float = 50
    stoch_k: float = 50
    stoch_d: float = 50
    stoch_cross: str = ""  # "golden", "dead", ""

    # 변동성
    atr: float = 0
    atr_pct: float = 0  # ATR / 현재가 %
    bb_upper: float = 0
    bb_middle: float = 0
    bb_lower: float = 0
    bb_width: float = 0  # 밴드 폭 (%)
    bb_position: float = 0.5  # 0=하단, 1=상단

    # 거래량
    vwap: float = 0
    volume_ratio: float = 1.0  # 현재 거래량 / 평균 거래량
    obv_trend: str = ""  # "up", "down", "flat"

    # 일목균형표
    ichimoku_tenkan: float = 0
    ichimoku_kijun: float = 0
    ichimoku_senkou_a: float = 0
    ichimoku_senkou_b: float = 0
    ichimoku_signal: str = ""  # "strong_buy", "buy", "sell", "strong_sell", "neutral"

    # 지지/저항
    support_levels: list = None
    resistance_levels: list = None

    # 종합 점수
    trend_score: float = 0  # -1(강한하락) ~ +1(강한상승)
    momentum_score: float = 0
    volatility_score: float = 0
    volume_score: float = 0

    def __post_init__(self):
        if self.support_levels is None:
            self.support_levels = []
        if self.resistance_levels is None:
            self.resistance_levels = []


class TechnicalAnalyzer:
    """고급 기술적 분석기."""

    def analyze(self, candles: list[dict], current_price: int = 0) -> TechnicalSnapshot:
        """캔들 데이터를 종합 분석하여 TechnicalSnapshot을 반환한다.

        Args:
            candles: 캔들 데이터 (과거순: oldest first)
            current_price: 현재가 (0이면 마지막 캔들 종가 사용)
        """
        if len(candles) < 5:
            return TechnicalSnapshot()

        closes = [c["close"] for c in candles if c.get("close", 0) > 0]
        highs = [c["high"] for c in candles if c.get("high", 0) > 0]
        lows = [c["low"] for c in candles if c.get("low", 0) > 0]
        volumes = [c["volume"] for c in candles if c.get("volume", 0) >= 0]

        if len(closes) < 5:
            return TechnicalSnapshot()

        price = current_price if current_price > 0 else closes[-1]
        snap = TechnicalSnapshot()

        # ── 이동평균 ──
        snap.sma_5 = self._sma(closes, 5)
        snap.sma_20 = self._sma(closes, 20)
        snap.sma_60 = self._sma(closes, 60)
        snap.ema_12 = self._ema(closes, 12)
        snap.ema_26 = self._ema(closes, 26)

        # ── MACD ──
        snap.macd_line, snap.macd_signal, snap.macd_histogram = self._macd(closes)
        snap.macd_cross = self._detect_macd_cross(closes)

        # ── RSI ──
        snap.rsi = self._rsi(closes, 14)

        # ── Stochastic ──
        snap.stoch_k, snap.stoch_d = self._stochastic(highs, lows, closes)
        snap.stoch_cross = self._detect_stoch_cross(highs, lows, closes)

        # ── ATR ──
        snap.atr = self._atr(highs, lows, closes, 14)
        snap.atr_pct = (snap.atr / price * 100) if price > 0 else 0

        # ── Bollinger Bands ──
        snap.bb_upper, snap.bb_middle, snap.bb_lower = self._bollinger(closes, 20, 2.0)
        bw = snap.bb_upper - snap.bb_lower
        snap.bb_width = (bw / snap.bb_middle * 100) if snap.bb_middle > 0 else 0
        snap.bb_position = (price - snap.bb_lower) / bw if bw > 0 else 0.5

        # ── VWAP ──
        snap.vwap = self._vwap(candles)

        # ── 거래량 분석 ──
        snap.volume_ratio = self._volume_ratio(volumes, 20)
        snap.obv_trend = self._obv_trend(closes, volumes)

        # ── 일목균형표 ──
        ichi = self._ichimoku(highs, lows, closes)
        snap.ichimoku_tenkan = ichi["tenkan"]
        snap.ichimoku_kijun = ichi["kijun"]
        snap.ichimoku_senkou_a = ichi["senkou_a"]
        snap.ichimoku_senkou_b = ichi["senkou_b"]
        snap.ichimoku_signal = self._ichimoku_signal(price, ichi)

        # ── 지지/저항선 ──
        snap.support_levels = self._find_support(lows, price)
        snap.resistance_levels = self._find_resistance(highs, price)

        # ── 종합 점수 계산 ──
        snap.trend_score = self._calc_trend_score(snap, price)
        snap.momentum_score = self._calc_momentum_score(snap)
        snap.volatility_score = self._calc_volatility_score(snap)
        snap.volume_score = self._calc_volume_score(snap, price)

        return snap

    # ─────────────────── 이동평균 ───────────────────

    def _sma(self, data: list[float], period: int) -> float:
        if len(data) < period:
            return data[-1] if data else 0
        return sum(data[-period:]) / period

    def _ema(self, data: list[float], period: int) -> float:
        if len(data) < period:
            return data[-1] if data else 0
        k = 2 / (period + 1)
        ema = sum(data[:period]) / period
        for val in data[period:]:
            ema = val * k + ema * (1 - k)
        return ema

    # ─────────────────── MACD ───────────────────

    def _macd(self, closes: list[float]) -> tuple[float, float, float]:
        if len(closes) < 26:
            return 0, 0, 0

        ema12_vals = self._ema_series(closes, 12)
        ema26_vals = self._ema_series(closes, 26)

        min_len = min(len(ema12_vals), len(ema26_vals))
        macd_vals = [ema12_vals[-(min_len - i)] - ema26_vals[-(min_len - i)] for i in range(min_len)]

        if len(macd_vals) < 9:
            return macd_vals[-1] if macd_vals else 0, 0, 0

        signal = self._ema(macd_vals, 9)
        histogram = macd_vals[-1] - signal
        return macd_vals[-1], signal, histogram

    def _ema_series(self, data: list[float], period: int) -> list[float]:
        if len(data) < period:
            return list(data)
        k = 2 / (period + 1)
        result = [sum(data[:period]) / period]
        for val in data[period:]:
            result.append(val * k + result[-1] * (1 - k))
        return result

    def _detect_macd_cross(self, closes: list[float]) -> str:
        if len(closes) < 30:
            return ""
        recent = closes[-30:]
        prev = closes[-31:-1] if len(closes) > 30 else closes[-30:]

        m1, s1, _ = self._macd(recent)
        m0, s0, _ = self._macd(prev)

        if m0 <= s0 and m1 > s1:
            return "golden"
        elif m0 >= s0 and m1 < s1:
            return "dead"
        return ""

    # ─────────────────── RSI ───────────────────

    def _rsi(self, closes: list[float], period: int = 14) -> float:
        if len(closes) < period + 1:
            return 50.0

        deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]

        gains = []
        losses = []
        for d in deltas[:period]:
            gains.append(d if d > 0 else 0)
            losses.append(-d if d < 0 else 0)

        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period

        for d in deltas[period:]:
            gain = d if d > 0 else 0
            loss = -d if d < 0 else 0
            avg_gain = (avg_gain * (period - 1) + gain) / period
            avg_loss = (avg_loss * (period - 1) + loss) / period

        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    # ─────────────────── Stochastic ───────────────────

    def _stochastic(
        self, highs: list[float], lows: list[float], closes: list[float],
        k_period: int = 14, d_period: int = 3,
    ) -> tuple[float, float]:
        if len(closes) < k_period:
            return 50, 50

        k_vals = []
        for i in range(max(0, len(closes) - k_period - d_period + 1), len(closes)):
            start = max(0, i - k_period + 1)
            h = max(highs[start:i + 1])
            low = min(lows[start:i + 1])
            if h == low:
                k_vals.append(50)
            else:
                k_vals.append((closes[i] - low) / (h - low) * 100)

        k = k_vals[-1] if k_vals else 50
        d = sum(k_vals[-d_period:]) / min(d_period, len(k_vals)) if k_vals else 50

        return k, d

    def _detect_stoch_cross(
        self, highs: list[float], lows: list[float], closes: list[float]
    ) -> str:
        if len(closes) < 16:
            return ""
        k_now, d_now = self._stochastic(highs, lows, closes)
        k_prev, d_prev = self._stochastic(highs[:-1], lows[:-1], closes[:-1])

        if k_prev <= d_prev and k_now > d_now and k_now < 30:
            return "golden"
        elif k_prev >= d_prev and k_now < d_now and k_now > 70:
            return "dead"
        return ""

    # ─────────────────── ATR ───────────────────

    def _atr(self, highs: list, lows: list, closes: list, period: int = 14) -> float:
        if len(closes) < 2:
            return 0
        trs = []
        for i in range(1, len(closes)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
            trs.append(tr)
        if not trs:
            return 0
        return sum(trs[-period:]) / min(period, len(trs))

    # ─────────────────── Bollinger Bands ───────────────────

    def _bollinger(self, closes: list, period: int = 20, std: float = 2.0):
        if len(closes) < period:
            mid = closes[-1]
            return mid, mid, mid
        recent = closes[-period:]
        mid = sum(recent) / period
        variance = sum((p - mid) ** 2 for p in recent) / period
        sd = variance ** 0.5
        return mid + std * sd, mid, mid - std * sd

    # ─────────────────── VWAP ───────────────────

    def _vwap(self, candles: list[dict]) -> float:
        total_pv = 0
        total_vol = 0
        for c in candles:
            typical = (c.get("high", 0) + c.get("low", 0) + c.get("close", 0)) / 3
            vol = c.get("volume", 0)
            total_pv += typical * vol
            total_vol += vol
        return total_pv / total_vol if total_vol > 0 else 0

    # ─────────────────── 거래량 분석 ───────────────────

    def _volume_ratio(self, volumes: list, period: int = 20) -> float:
        if not volumes or len(volumes) < 2:
            return 1.0
        avg = sum(volumes[-period:]) / min(period, len(volumes))
        return volumes[-1] / avg if avg > 0 else 1.0

    def _obv_trend(self, closes: list, volumes: list, period: int = 10) -> str:
        if len(closes) < period + 1 or len(volumes) < period + 1:
            return "flat"
        obv = 0
        obv_vals = []
        for i in range(1, len(closes)):
            if closes[i] > closes[i - 1]:
                obv += volumes[i] if i < len(volumes) else 0
            elif closes[i] < closes[i - 1]:
                obv -= volumes[i] if i < len(volumes) else 0
            obv_vals.append(obv)

        if len(obv_vals) < period:
            return "flat"
        recent = obv_vals[-period:]
        slope = (recent[-1] - recent[0]) / period
        if slope > 0:
            return "up"
        elif slope < 0:
            return "down"
        return "flat"

    # ─────────────────── 일목균형표 ───────────────────

    def _ichimoku(self, highs: list, lows: list, closes: list) -> dict:
        def midpoint(h: list, lo: list, period: int) -> float:
            if len(h) < period:
                return (h[-1] + lo[-1]) / 2 if h else 0
            return (max(h[-period:]) + min(lo[-period:])) / 2

        tenkan = midpoint(highs, lows, 9)   # 전환선 (9일)
        kijun = midpoint(highs, lows, 26)   # 기준선 (26일)
        senkou_a = (tenkan + kijun) / 2     # 선행스팬A
        senkou_b = midpoint(highs, lows, 52)  # 선행스팬B (52일)

        return {
            "tenkan": tenkan,
            "kijun": kijun,
            "senkou_a": senkou_a,
            "senkou_b": senkou_b,
        }

    def _ichimoku_signal(self, price: float, ichi: dict) -> str:
        tenkan = ichi["tenkan"]
        kijun = ichi["kijun"]
        cloud_top = max(ichi["senkou_a"], ichi["senkou_b"])
        cloud_bottom = min(ichi["senkou_a"], ichi["senkou_b"])

        above_cloud = price > cloud_top
        below_cloud = price < cloud_bottom
        tenkan_above = tenkan > kijun

        if above_cloud and tenkan_above:
            return "strong_buy"
        elif above_cloud:
            return "buy"
        elif below_cloud and not tenkan_above:
            return "strong_sell"
        elif below_cloud:
            return "sell"
        return "neutral"

    # ─────────────────── 지지/저항선 ───────────────────

    def _find_support(self, lows: list[float], current: float, n: int = 3) -> list[float]:
        if len(lows) < 5:
            return []
        pivots = []
        for i in range(2, len(lows) - 2):
            if lows[i] <= lows[i - 1] and lows[i] <= lows[i - 2] and \
               lows[i] <= lows[i + 1] and lows[i] <= lows[i + 2]:
                if lows[i] < current:
                    pivots.append(lows[i])
        # 가격에 가까운 순으로 정렬
        pivots.sort(key=lambda x: current - x)
        return pivots[:n]

    def _find_resistance(self, highs: list[float], current: float, n: int = 3) -> list[float]:
        if len(highs) < 5:
            return []
        pivots = []
        for i in range(2, len(highs) - 2):
            if highs[i] >= highs[i - 1] and highs[i] >= highs[i - 2] and \
               highs[i] >= highs[i + 1] and highs[i] >= highs[i + 2]:
                if highs[i] > current:
                    pivots.append(highs[i])
        pivots.sort(key=lambda x: x - current)
        return pivots[:n]

    # ─────────────────── 종합 점수 ───────────────────

    def _calc_trend_score(self, snap: TechnicalSnapshot, price: float) -> float:
        """추세 점수: -1.0(강한 하락) ~ +1.0(강한 상승)."""
        score = 0.0
        count = 0

        # 이동평균 배열 (정배열/역배열)
        if snap.sma_5 > 0 and snap.sma_20 > 0:
            if price > snap.sma_5 > snap.sma_20:
                score += 1.0  # 정배열
            elif price < snap.sma_5 < snap.sma_20:
                score -= 1.0  # 역배열
            elif price > snap.sma_5:
                score += 0.3
            elif price < snap.sma_5:
                score -= 0.3
            count += 1

        if snap.sma_60 > 0:
            if price > snap.sma_60:
                score += 0.5
            else:
                score -= 0.5
            count += 1

        # MACD
        if snap.macd_histogram > 0:
            score += 0.5
        elif snap.macd_histogram < 0:
            score -= 0.5
        count += 1

        if snap.macd_cross == "golden":
            score += 0.8
        elif snap.macd_cross == "dead":
            score -= 0.8

        # 일목균형표
        ichi_scores = {"strong_buy": 1.0, "buy": 0.5, "sell": -0.5, "strong_sell": -1.0, "neutral": 0}
        score += ichi_scores.get(snap.ichimoku_signal, 0)
        count += 1

        return max(-1.0, min(1.0, score / max(count, 1)))

    def _calc_momentum_score(self, snap: TechnicalSnapshot) -> float:
        """모멘텀 점수: -1.0(과매도) ~ +1.0(과매수)."""
        score = 0.0

        # RSI
        if snap.rsi > 70:
            score += 0.5 + (snap.rsi - 70) / 60
        elif snap.rsi < 30:
            score -= 0.5 + (30 - snap.rsi) / 60
        else:
            score += (snap.rsi - 50) / 40

        # Stochastic
        if snap.stoch_k > 80:
            score += 0.3
        elif snap.stoch_k < 20:
            score -= 0.3

        if snap.stoch_cross == "golden":
            score -= 0.5  # 반전 신호 (매수 기회)
        elif snap.stoch_cross == "dead":
            score += 0.5  # 반전 신호 (매도 기회)

        return max(-1.0, min(1.0, score))

    def _calc_volatility_score(self, snap: TechnicalSnapshot) -> float:
        """변동성 점수: 0(안정) ~ 1.0(고변동)."""
        score = 0.0
        # ATR 기반
        if snap.atr_pct > 3:
            score += 0.8
        elif snap.atr_pct > 2:
            score += 0.5
        elif snap.atr_pct > 1:
            score += 0.3

        # BB 폭
        if snap.bb_width > 6:
            score += 0.2
        elif snap.bb_width < 2:
            score -= 0.2  # 스퀴즈 (폭발 직전)

        return max(0, min(1.0, score))

    def _calc_volume_score(self, snap: TechnicalSnapshot, price: float) -> float:
        """거래량 점수: -1.0(매도압력) ~ +1.0(매수압력)."""
        score = 0.0

        # 거래량 비율
        if snap.volume_ratio > 3:
            score += 0.5
        elif snap.volume_ratio > 2:
            score += 0.3
        elif snap.volume_ratio > 1.5:
            score += 0.1

        # OBV 추세
        if snap.obv_trend == "up":
            score += 0.3
        elif snap.obv_trend == "down":
            score -= 0.3

        # VWAP 대비 가격
        if snap.vwap > 0:
            if price > snap.vwap:
                score += 0.2
            else:
                score -= 0.2

        return max(-1.0, min(1.0, score))
