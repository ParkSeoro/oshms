"""자가 학습 모듈.

거래 결과를 지속적으로 분석하고, 전략을 자동으로 개선한다.

학습 사이클:
1. 거래 기록 분석 → 어떤 조건에서 성공/실패했는지 파악
2. 시간대별/종목별/패턴별 승률 분석
3. 전략 파라미터 자동 조정
4. 백테스트로 검증
5. 검증 통과 시 실제 적용
"""

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from learning.backtester import Backtester
from learning.optimizer import StrategyOptimizer
from utils.logger import setup_logger

logger = setup_logger("oshms.learning")


@dataclass
class LearningInsight:
    """학습으로 발견한 인사이트."""
    category: str  # "time", "pattern", "indicator", "stock"
    key: str
    finding: str
    confidence: float
    action: str  # 적용할 조정
    timestamp: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M"))


class SelfLearner:
    """자가 학습 시스템."""

    INSIGHTS_FILE = Path("learning/insights.json")
    LEARNING_STATE_FILE = Path("learning/state.json")

    def __init__(self):
        self.optimizer = StrategyOptimizer()
        self.backtester = Backtester()
        self.insights: list[LearningInsight] = []
        self._load_state()

    def learn_from_trades(self, trades: list[dict]) -> list[LearningInsight]:
        """거래 기록에서 학습한다.

        Args:
            trades: TradeRecord를 dict로 변환한 거래 기록 리스트
        """
        if len(trades) < 10:
            logger.info("학습 데이터 부족 (%d건, 최소 10건 필요)", len(trades))
            return []

        sells = [t for t in trades if t.get("side") == "SELL"]
        if len(sells) < 5:
            return []

        new_insights = []

        # 1. 시간대별 분석
        new_insights.extend(self._analyze_time_patterns(sells))

        # 2. 승패 사유 분석
        new_insights.extend(self._analyze_reason_patterns(sells))

        # 3. 수익률 분포 분석
        new_insights.extend(self._analyze_profit_distribution(sells))

        # 4. 연속 손실 패턴 감지
        new_insights.extend(self._detect_losing_streaks(sells))

        self.insights.extend(new_insights)
        self._save_state()

        for insight in new_insights:
            logger.info(
                "학습 인사이트 [%s]: %s (신뢰도=%.0f%%)",
                insight.category, insight.finding, insight.confidence * 100,
            )

        return new_insights

    def get_adjustments(self) -> dict:
        """학습 결과에 기반한 전략 조정값을 반환한다."""
        adjustments = {
            "buy_threshold_adj": 0.0,
            "sell_threshold_adj": 0.0,
            "stop_loss_adj": 0.0,
            "take_profit_adj": 0.0,
            "avoid_times": [],
            "prefer_times": [],
            "avoid_patterns": [],
        }

        for insight in self.insights:
            if insight.confidence < 0.5:
                continue

            if insight.category == "time":
                if "회피" in insight.action:
                    adjustments["avoid_times"].append(insight.key)
                elif "선호" in insight.action:
                    adjustments["prefer_times"].append(insight.key)

            elif insight.category == "risk":
                if "손절" in insight.action:
                    if "타이트" in insight.action:
                        adjustments["stop_loss_adj"] += 0.5
                    elif "여유" in insight.action:
                        adjustments["stop_loss_adj"] -= 0.3

                if "익절" in insight.action:
                    if "상향" in insight.action:
                        adjustments["take_profit_adj"] += 0.5

            elif insight.category == "threshold":
                if "매수기준상향" in insight.action:
                    adjustments["buy_threshold_adj"] += 0.05
                elif "매수기준하향" in insight.action:
                    adjustments["buy_threshold_adj"] -= 0.03

        return adjustments

    def auto_optimize(self, candles: list[dict], strategy_name: str = "scalping") -> dict:
        """전략 파라미터를 자동 최적화하고 결과를 반환한다."""
        result = self.optimizer.optimize(strategy_name, candles, max_iterations=30)

        if result.best_score > 0:
            logger.info(
                "자동 최적화 완료: %s → 점수=%.1f 파라미터=%s",
                strategy_name, result.best_score, result.best_params,
            )
            return {
                "strategy": strategy_name,
                "params": result.best_params,
                "score": result.best_score,
                "applied": True,
            }

        return {"strategy": strategy_name, "applied": False, "reason": "개선 없음"}

    # ─────────── 분석 메서드 ───────────

    def _analyze_time_patterns(self, sells: list[dict]) -> list[LearningInsight]:
        """시간대별 매매 성과를 분석한다."""
        insights = []
        by_hour: dict[str, list] = defaultdict(list)

        for t in sells:
            hour = t.get("timestamp", "")[:13]  # YYYY-MM-DD HH
            if len(hour) >= 13:
                by_hour[hour[-2:]].append(t.get("profit_loss", 0))

        for hour, profits in by_hour.items():
            if len(profits) < 3:
                continue

            win_rate = sum(1 for p in profits if p > 0) / len(profits) * 100
            avg_profit = sum(profits) / len(profits)

            if win_rate < 30 and len(profits) >= 5:
                insights.append(LearningInsight(
                    category="time",
                    key=f"{hour}시",
                    finding=f"{hour}시 매매 승률 {win_rate:.0f}% (평균손익 {avg_profit:+,.0f}원)",
                    confidence=min(0.9, len(profits) / 10),
                    action=f"{hour}시 매매 회피",
                ))
            elif win_rate > 70 and len(profits) >= 5:
                insights.append(LearningInsight(
                    category="time",
                    key=f"{hour}시",
                    finding=f"{hour}시 매매 승률 {win_rate:.0f}% (평균손익 {avg_profit:+,.0f}원)",
                    confidence=min(0.9, len(profits) / 10),
                    action=f"{hour}시 매매 선호",
                ))

        return insights

    def _analyze_reason_patterns(self, sells: list[dict]) -> list[LearningInsight]:
        """매매 사유별 성과를 분석한다."""
        insights = []
        by_reason: dict[str, list] = defaultdict(list)

        for t in sells:
            reason = t.get("reason", "unknown")
            # 핵심 키워드 추출
            for keyword in ["골든크로스", "데드크로스", "볼린저", "RSI", "모멘텀", "패턴", "뉴스"]:
                if keyword in reason:
                    by_reason[keyword].append(t.get("profit_loss", 0))

        for keyword, profits in by_reason.items():
            if len(profits) < 3:
                continue
            win_rate = sum(1 for p in profits if p > 0) / len(profits) * 100
            avg = sum(profits) / len(profits)

            if win_rate < 35:
                insights.append(LearningInsight(
                    category="pattern",
                    key=keyword,
                    finding=f"'{keyword}' 기반 매매 승률 {win_rate:.0f}% → 해당 신호 가중치 축소 권장",
                    confidence=min(0.8, len(profits) / 8),
                    action=f"{keyword} 가중치 축소",
                ))

        return insights

    def _analyze_profit_distribution(self, sells: list[dict]) -> list[LearningInsight]:
        """수익률 분포를 분석한다."""
        insights = []
        profits = [t.get("profit_loss", 0) for t in sells]

        if not profits:
            return insights

        avg_win = sum(p for p in profits if p > 0) / max(1, sum(1 for p in profits if p > 0))
        avg_loss = sum(p for p in profits if p < 0) / max(1, sum(1 for p in profits if p < 0))

        # 손절이 너무 늦은 경우
        if avg_loss != 0 and abs(avg_loss) > avg_win * 2:
            insights.append(LearningInsight(
                category="risk",
                key="stop_loss",
                finding=f"평균 손실({avg_loss:,.0f}원)이 평균 수익({avg_win:,.0f}원)의 2배 이상 → 손절 타이트하게",
                confidence=0.7,
                action="손절 타이트",
            ))

        # 익절이 너무 빠른 경우
        if avg_win > 0 and avg_win < abs(avg_loss) * 0.5:
            insights.append(LearningInsight(
                category="risk",
                key="take_profit",
                finding=f"평균 수익({avg_win:,.0f}원)이 평균 손실의 절반 미만 → 익절 상향 권장",
                confidence=0.6,
                action="익절 상향",
            ))

        return insights

    def _detect_losing_streaks(self, sells: list[dict]) -> list[LearningInsight]:
        """연속 손실 패턴을 감지한다."""
        insights = []
        max_streak = 0
        current_streak = 0

        for t in sells:
            if t.get("profit_loss", 0) < 0:
                current_streak += 1
                max_streak = max(max_streak, current_streak)
            else:
                current_streak = 0

        if max_streak >= 5:
            insights.append(LearningInsight(
                category="threshold",
                key="losing_streak",
                finding=f"최대 연속 {max_streak}회 손실 → 매수 기준 상향 필요",
                confidence=0.8,
                action="매수기준상향",
            ))

        return insights

    # ─────────── 저장/로드 ───────────

    def _save_state(self):
        self.INSIGHTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = [
            {
                "category": i.category,
                "key": i.key,
                "finding": i.finding,
                "confidence": i.confidence,
                "action": i.action,
                "timestamp": i.timestamp,
            }
            for i in self.insights
        ]
        self.INSIGHTS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load_state(self):
        if self.INSIGHTS_FILE.exists():
            try:
                data = json.loads(self.INSIGHTS_FILE.read_text(encoding="utf-8"))
                self.insights = [LearningInsight(**d) for d in data]
                logger.info("학습 인사이트 %d건 로드", len(self.insights))
            except (json.JSONDecodeError, TypeError, OSError):
                self.insights = []
