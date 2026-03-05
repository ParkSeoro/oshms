"""전략 앙상블: 여러 전략의 신호를 성과 기반 동적 가중치로 종합.

v3.2: 고정 가중치 → 성과 기반 동적 가중치 앙상블
- Expert + Scalping + Momentum 3전략 블렌딩
- 최근 거래 성과에 따라 가중치 자동 조절 (EMA 기반)
- 최소 가중치 0.1 보장으로 완전 비활성화 방지
"""

import json
from collections import defaultdict
from pathlib import Path

from strategy.base import BaseStrategy, Signal, SignalType
from strategy.scalping import ScalpingStrategy
from strategy.momentum import MomentumStrategy
from utils.logger import setup_logger

logger = setup_logger("oshms.strategy.combined")

PERFORMANCE_FILE = Path("data/ensemble_performance.json")


class CombinedStrategy(BaseStrategy):
    """성과 기반 동적 앙상블 전략 (Expert + Scalping + Momentum)."""

    name = "ensemble"

    # 전략 이름 → 매매 사유(reason)에서 탐지할 키워드
    STRATEGY_KEYWORDS = {
        "expert": ["기술분석", "가치투자", "상승여력", "골든크로스", "RSI", "MACD", "패턴"],
        "scalping": ["볼린저", "스캘핑", "과매도", "반등"],
        "momentum": ["모멘텀", "추세", "거래량", "돌파"],
    }

    MIN_WEIGHT = 0.1       # 최소 가중치 (완전 비활성화 방지)
    EMA_DECAY = 0.15       # EMA 감쇠율 (최근 거래에 더 높은 가중)
    PERFORMANCE_WINDOW = 30  # 최근 N건의 거래만 고려

    def __init__(
        self,
        buy_threshold: float = 0.3,
        sell_threshold: float = 0.3,
    ):
        self.scalping = ScalpingStrategy()
        self.momentum = MomentumStrategy()
        # Expert는 외부에서 주입받지 않고 독립 시그널만 사용
        self.strategies: list[tuple[BaseStrategy, str]] = [
            (self.scalping, "scalping"),
            (self.momentum, "momentum"),
        ]
        self.buy_threshold = buy_threshold
        self.sell_threshold = sell_threshold

        # 전략별 성과 추적
        self.performance: dict[str, dict] = {
            "expert": {"wins": 0, "losses": 0, "total_profit": 0.0, "ema_score": 0.5},
            "scalping": {"wins": 0, "losses": 0, "total_profit": 0.0, "ema_score": 0.5},
            "momentum": {"wins": 0, "losses": 0, "total_profit": 0.0, "ema_score": 0.5},
        }

        # 현재 동적 가중치
        self.weights: dict[str, float] = {
            "expert": 0.50,
            "scalping": 0.30,
            "momentum": 0.20,
        }

        self._load_performance()

    def analyze(self, stock_code: str, candles: list[dict], current_price: dict) -> Signal:
        """3전략의 신호를 동적 가중치로 블렌딩하여 최종 신호를 생성한다."""
        buy_score = 0.0
        sell_score = 0.0
        reasons = []

        # 동적 가중치 계산
        self._update_weights()

        # Scalping + Momentum 신호 수집
        for strategy, name in self.strategies:
            weight = self.weights.get(name, 0.2)
            signal = strategy.analyze(stock_code, candles, current_price)

            if signal.is_buy:
                buy_score += signal.strength * weight
                reasons.append(f"[{name}:매수 w={weight:.2f}] {signal.reason}")
            elif signal.is_sell:
                sell_score += signal.strength * weight
                reasons.append(f"[{name}:매도 w={weight:.2f}] {signal.reason}")
            else:
                reasons.append(f"[{name}:중립 w={weight:.2f}]")

        # Expert 가중치는 trader.py에서 full_analysis 결과와 합산될 때 적용
        # 여기서는 scalping + momentum 신호만 블렌딩

        reason_str = " | ".join(reasons)

        logger.debug(
            "[%s] 앙상블 점수 매수=%.2f 매도=%.2f | 가중치: expert=%.2f scalp=%.2f mom=%.2f",
            stock_code, buy_score, sell_score,
            self.weights["expert"], self.weights["scalping"], self.weights["momentum"],
        )

        if buy_score >= self.buy_threshold and buy_score > sell_score:
            return Signal(
                SignalType.BUY,
                stock_code,
                reason_str,
                strength=min(1.0, buy_score),
            )
        elif sell_score >= self.sell_threshold and sell_score > buy_score:
            return Signal(
                SignalType.SELL,
                stock_code,
                reason_str,
                strength=min(1.0, sell_score),
            )

        return Signal(SignalType.HOLD, stock_code, reason_str, strength=0)

    def update_performance(self, strategy_name: str, profit_loss: float, profit_rate: float):
        """거래 완료 후 전략 성과를 업데이트한다.

        Args:
            strategy_name: 전략 이름 (expert/scalping/momentum)
            profit_loss: 손익 (원)
            profit_rate: 수익률 (%)
        """
        if strategy_name not in self.performance:
            return

        perf = self.performance[strategy_name]
        is_win = profit_loss > 0

        if is_win:
            perf["wins"] += 1
        else:
            perf["losses"] += 1
        perf["total_profit"] += profit_loss

        # EMA 스코어 갱신: 최근 결과에 더 높은 가중
        reward = 1.0 if is_win else 0.0
        # 수익률 보너스 (큰 수익은 더 높은 보상)
        if profit_rate > 3.0:
            reward = min(1.5, 1.0 + profit_rate / 20)
        elif profit_rate < -2.0:
            reward = max(-0.5, profit_rate / 10)

        perf["ema_score"] = (
            perf["ema_score"] * (1 - self.EMA_DECAY) + reward * self.EMA_DECAY
        )

        self._save_performance()
        logger.debug(
            "앙상블 성과 갱신: %s → EMA=%.3f (승=%d 패=%d 총손익=%+,.0f원)",
            strategy_name, perf["ema_score"],
            perf["wins"], perf["losses"], perf["total_profit"],
        )

    def identify_strategy(self, reason: str) -> str:
        """매매 사유(reason)에서 어떤 전략의 기여가 큰지 식별한다."""
        scores = defaultdict(int)
        reason_lower = reason.lower()

        for strategy_name, keywords in self.STRATEGY_KEYWORDS.items():
            for kw in keywords:
                if kw in reason_lower or kw.lower() in reason_lower:
                    scores[strategy_name] += 1

        if not scores:
            return "expert"  # 기본값

        return max(scores, key=scores.get)

    def get_weight(self, strategy_name: str) -> float:
        """특정 전략의 현재 가중치를 반환한다."""
        return self.weights.get(strategy_name, 0.2)

    def get_ensemble_summary(self) -> dict:
        """앙상블 현황 요약을 반환한다."""
        self._update_weights()
        summary = {}
        for name in ("expert", "scalping", "momentum"):
            perf = self.performance[name]
            total = perf["wins"] + perf["losses"]
            win_rate = (perf["wins"] / total * 100) if total > 0 else 0
            summary[name] = {
                "weight": round(self.weights[name], 3),
                "ema_score": round(perf["ema_score"], 3),
                "win_rate": round(win_rate, 1),
                "total_trades": total,
                "total_profit": perf["total_profit"],
            }
        return summary

    def _update_weights(self):
        """EMA 스코어 기반으로 동적 가중치를 재계산한다."""
        scores = {
            name: max(0.01, self.performance[name]["ema_score"])
            for name in ("expert", "scalping", "momentum")
        }

        total_score = sum(scores.values())
        if total_score <= 0:
            return

        # 스코어 비율 → 가중치 (최소 MIN_WEIGHT 보장)
        raw_weights = {name: score / total_score for name, score in scores.items()}

        # 최소 가중치 보장 후 재정규화
        for name in raw_weights:
            raw_weights[name] = max(self.MIN_WEIGHT, raw_weights[name])

        total = sum(raw_weights.values())
        self.weights = {name: round(w / total, 3) for name, w in raw_weights.items()}

    def _load_performance(self):
        """저장된 성과 데이터를 로드한다."""
        if PERFORMANCE_FILE.exists():
            try:
                data = json.loads(PERFORMANCE_FILE.read_text(encoding="utf-8"))
                for name in ("expert", "scalping", "momentum"):
                    if name in data:
                        self.performance[name].update(data[name])
                self._update_weights()
                logger.info(
                    "앙상블 성과 로드: expert=%.2f scalp=%.2f mom=%.2f",
                    self.weights["expert"], self.weights["scalping"], self.weights["momentum"],
                )
            except (json.JSONDecodeError, TypeError):
                pass

        # trades.json에서 초기 성과 추정 (성과 파일이 없을 때)
        if self.performance["expert"]["wins"] + self.performance["expert"]["losses"] == 0:
            self._bootstrap_from_trades()

    def _bootstrap_from_trades(self):
        """기존 거래 기록에서 전략별 성과를 역추정한다."""
        trades_file = Path("logs/trades.json")
        if not trades_file.exists():
            return

        try:
            trades = json.loads(trades_file.read_text(encoding="utf-8"))
            sells = [t for t in trades if t.get("side") == "SELL"]
            recent = sells[-self.PERFORMANCE_WINDOW:]

            for trade in recent:
                reason = trade.get("reason", "")
                pl = trade.get("profit_loss", 0)
                pr = trade.get("profit_rate", 0)
                strategy = self.identify_strategy(reason)
                self.update_performance(strategy, pl, pr)

            if recent:
                logger.info("앙상블 부트스트랩: %d건 거래에서 성과 역추정 완료", len(recent))
        except Exception as e:
            logger.warning("앙상블 부트스트랩 실패: %s", e)

    def _save_performance(self):
        """성과 데이터를 저장한다."""
        PERFORMANCE_FILE.parent.mkdir(parents=True, exist_ok=True)
        PERFORMANCE_FILE.write_text(
            json.dumps(self.performance, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
