"""강화학습 보상 시스템 (Q-Learning).

매매 결과를 state-action Q-value로 학습하여 매수/매도 결정에 반영한다.

State = (시장 레짐, RSI 수준, 추세 방향, 거래량 상태) 이산화
Action = BUY / HOLD / SELL
Reward = 수익률 정규화 값

Q(s,a) ← Q(s,a) + α[reward + γ·max_Q(s',a') - Q(s,a)]

v3.2 신규
"""

import json
import math
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from utils.logger import setup_logger

logger = setup_logger("oshms.learning.qlearning")

Q_STATE_FILE = Path("data/qlearning_state.json")


@dataclass(frozen=True)
class QState:
    """이산화된 시장 상태."""
    regime: str       # trending_up / trending_down / volatile / ranging
    rsi_level: str    # oversold / neutral / overbought
    trend_dir: str    # up / down / flat
    volume_state: str  # high / normal / low

    def to_key(self) -> str:
        return f"{self.regime}|{self.rsi_level}|{self.trend_dir}|{self.volume_state}"

    @classmethod
    def from_key(cls, key: str) -> "QState":
        parts = key.split("|")
        if len(parts) == 4:
            return cls(*parts)
        return cls("ranging", "neutral", "flat", "normal")


class QLearningAgent:
    """Q-Learning 기반 매매 보조 에이전트.

    단독으로 매매 결정을 하지 않고, 기존 전략의 신호 강도에
    confidence modifier (-0.2 ~ +0.2)를 더하는 보조 역할.

    사용법:
        agent = QLearningAgent()
        # 매수 판단 시
        modifier = agent.get_confidence_modifier(snapshot)
        adjusted_strength = signal.strength + modifier
        # 매도 완료 시
        agent.record_action(snapshot, "BUY", profit_rate)
    """

    ACTIONS = ("BUY", "HOLD", "SELL")

    def __init__(
        self,
        alpha: float = 0.1,    # 학습률
        gamma: float = 0.9,    # 할인율
        epsilon: float = 0.15,  # 탐험률 (unused — 보조 모드이므로)
    ):
        self.alpha = alpha
        self.gamma = gamma
        self.epsilon = epsilon

        # Q-table: state_key → {action: q_value}
        self.q_table: dict[str, dict[str, float]] = {}

        # 최근 매수 상태 기록 (매도 시 학습용)
        # stock_code → (state_key, action)
        self.pending_states: dict[str, tuple[str, str]] = {}

        # 통계
        self.total_updates = 0
        self.total_states = 0

        self._load()

    def discretize_state(self, snapshot: dict) -> QState:
        """기술적 분석 스냅샷을 이산 상태로 변환한다.

        Args:
            snapshot: {regime, rsi, trend_score, momentum_score, volume_ratio, bb_position}
        """
        # 레짐
        regime = snapshot.get("regime", "ranging")
        if regime not in ("trending_up", "trending_down", "volatile", "ranging"):
            regime = "ranging"

        # RSI 수준
        rsi = snapshot.get("rsi", 50)
        if rsi < 30:
            rsi_level = "oversold"
        elif rsi > 70:
            rsi_level = "overbought"
        else:
            rsi_level = "neutral"

        # 추세 방향
        trend = snapshot.get("trend_score", 0)
        if trend > 0.3:
            trend_dir = "up"
        elif trend < -0.3:
            trend_dir = "down"
        else:
            trend_dir = "flat"

        # 거래량 상태
        vol = snapshot.get("volume_ratio", 1.0)
        if vol > 2.0:
            volume_state = "high"
        elif vol < 0.5:
            volume_state = "low"
        else:
            volume_state = "normal"

        return QState(regime, rsi_level, trend_dir, volume_state)

    def get_q_value(self, state_key: str, action: str) -> float:
        """특정 상태-행동 쌍의 Q-value를 반환한다."""
        return self.q_table.get(state_key, {}).get(action, 0.0)

    def get_max_q(self, state_key: str) -> float:
        """상태의 최대 Q-value를 반환한다."""
        actions = self.q_table.get(state_key, {})
        if not actions:
            return 0.0
        return max(actions.values())

    def get_confidence_modifier(self, snapshot: dict) -> float:
        """현재 시장 상태에 대한 신뢰도 보정값을 반환한다.

        Returns:
            -0.2 ~ +0.2 범위의 보정값
        """
        state = self.discretize_state(snapshot)
        state_key = state.to_key()

        buy_q = self.get_q_value(state_key, "BUY")
        hold_q = self.get_q_value(state_key, "HOLD")
        sell_q = self.get_q_value(state_key, "SELL")

        # Q-value가 없으면 보정 없음
        if buy_q == 0 and hold_q == 0 and sell_q == 0:
            return 0.0

        # BUY Q가 높으면 양수 보정, SELL Q가 높으면 음수 보정
        if buy_q > hold_q and buy_q > sell_q:
            # 매수 유리한 상태
            modifier = min(0.2, buy_q * 0.1)
        elif sell_q > hold_q and sell_q > buy_q:
            # 매도 유리한 상태 → 매수 억제
            modifier = max(-0.2, -sell_q * 0.1)
        else:
            modifier = 0.0

        return round(modifier, 3)

    def record_buy(self, stock_code: str, snapshot: dict):
        """매수 시 상태를 기록한다 (나중에 매도 시 학습용)."""
        state = self.discretize_state(snapshot)
        self.pending_states[stock_code] = (state.to_key(), "BUY")
        logger.debug("Q-Learning 매수 기록: %s → 상태=%s", stock_code, state.to_key())

    def record_sell(self, stock_code: str, profit_rate: float, snapshot: dict | None = None):
        """매도 완료 시 Q-value를 업데이트한다.

        Args:
            stock_code: 종목 코드
            profit_rate: 수익률 (%)
            snapshot: 매도 시 시장 상태 (있으면 next_state로 사용)
        """
        if stock_code not in self.pending_states:
            return

        state_key, action = self.pending_states.pop(stock_code)

        # 보상 계산: 수익률을 -1 ~ +1 범위로 정규화
        reward = self._calc_reward(profit_rate)

        # 다음 상태의 최대 Q-value
        next_max_q = 0.0
        if snapshot:
            next_state = self.discretize_state(snapshot)
            next_max_q = self.get_max_q(next_state.to_key())

        # Q-value 업데이트
        self._update_q(state_key, action, reward, next_max_q)

        # HOLD 액션도 간접 학습 (매수한 상태에서 HOLD = 수익의 일부)
        hold_reward = reward * 0.3  # 보유 기간 중 기여분
        self._update_q(state_key, "HOLD", hold_reward, next_max_q)

        self.total_updates += 1
        self._save()

        logger.info(
            "Q-Learning 업데이트: 상태=%s 행동=%s 보상=%.3f 수익률=%.2f%% (총 %d회)",
            state_key, action, reward, profit_rate, self.total_updates,
        )

    def _calc_reward(self, profit_rate: float) -> float:
        """수익률을 보상으로 변환한다."""
        # tanh로 정규화 → -1 ~ +1
        return math.tanh(profit_rate / 5.0)

    def _update_q(self, state_key: str, action: str, reward: float, next_max_q: float):
        """Q-value Bellman 업데이트."""
        if state_key not in self.q_table:
            self.q_table[state_key] = {a: 0.0 for a in self.ACTIONS}
            self.total_states += 1

        old_q = self.q_table[state_key].get(action, 0.0)
        new_q = old_q + self.alpha * (reward + self.gamma * next_max_q - old_q)
        self.q_table[state_key][action] = round(new_q, 4)

    def get_summary(self) -> dict:
        """Q-Learning 현황 요약."""
        total_entries = sum(len(v) for v in self.q_table.values())
        return {
            "total_states": self.total_states,
            "total_updates": self.total_updates,
            "q_table_entries": total_entries,
            "pending_positions": len(self.pending_states),
            "top_buy_states": self._get_top_states("BUY", 5),
            "top_sell_states": self._get_top_states("SELL", 5),
        }

    def _get_top_states(self, action: str, n: int) -> list[dict]:
        """특정 행동의 Q-value가 가장 높은 상태 N개."""
        scored = []
        for state_key, actions in self.q_table.items():
            q = actions.get(action, 0)
            if q != 0:
                scored.append({"state": state_key, "q_value": q})
        scored.sort(key=lambda x: x["q_value"], reverse=True)
        return scored[:n]

    def _load(self):
        """저장된 Q-table을 로드한다."""
        if Q_STATE_FILE.exists():
            try:
                data = json.loads(Q_STATE_FILE.read_text(encoding="utf-8"))
                self.q_table = data.get("q_table", {})
                self.total_updates = data.get("total_updates", 0)
                self.total_states = data.get("total_states", len(self.q_table))
                pending = data.get("pending_states", {})
                self.pending_states = {
                    k: tuple(v) if isinstance(v, list) else v
                    for k, v in pending.items()
                }
                logger.info(
                    "Q-Learning 상태 복원: %d개 상태, %d회 학습",
                    self.total_states, self.total_updates,
                )
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning("Q-Learning 상태 로드 실패: %s", e)

    def _save(self):
        """Q-table을 저장한다."""
        Q_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "q_table": self.q_table,
            "total_updates": self.total_updates,
            "total_states": self.total_states,
            "pending_states": {
                k: list(v) for k, v in self.pending_states.items()
            },
        }
        Q_STATE_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
