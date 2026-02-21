"""자동 진화 엔진.

프로그램이 스스로 학습하고 진화하여 점점 더 강력해진다.

진화 사이클:
1. 거래 결과 분석 → 패턴 발견
2. 전략 파라미터 자동 최적화
3. 새로운 매매 규칙 발견 및 적용
4. 성과 기반 가중치 자동 조정
5. 세대(Generation) 관리로 진화 추적
"""

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from learning.learner import SelfLearner
from learning.optimizer import StrategyOptimizer
from utils.logger import setup_logger

logger = setup_logger("oshms.learning.evolution")

EVOLUTION_FILE = Path("data/evolution_state.json")


@dataclass
class EvolutionState:
    """진화 상태."""
    generation: int = 0
    fitness_history: list[dict] = field(default_factory=list)
    active_rules: list[dict] = field(default_factory=list)
    weight_history: list[dict] = field(default_factory=list)
    last_evolution: str = ""
    best_fitness: float = 0
    best_generation: int = 0


class EvolutionEngine:
    """자동 진화 엔진.

    거래 결과를 분석하여 전략을 자동으로 개선한다.
    매 N건의 거래 후 진화 사이클을 실행한다.
    """

    EVOLUTION_INTERVAL = 15  # N건 거래마다 진화
    MAX_RULES = 20

    def __init__(self):
        self.learner = SelfLearner()
        self.optimizer = StrategyOptimizer()
        self.state = self._load_state()

    def evolve(self, trades: list[dict], candles: list[dict] = None) -> dict:
        """진화 사이클을 실행한다.

        Args:
            trades: 거래 기록 리스트
            candles: 백테스트용 캔들 데이터 (없으면 최적화 스킵)

        Returns:
            진화 결과 딕셔너리
        """
        self.state.generation += 1
        gen = self.state.generation
        logger.info("══ 진화 세대 #%d 시작 ══", gen)

        result = {
            "generation": gen,
            "insights": [],
            "rules_added": 0,
            "rules_removed": 0,
            "weight_adjustments": {},
            "optimization": None,
            "fitness": 0,
        }

        # 1단계: 거래 패턴 학습
        insights = self.learner.learn_from_trades(trades)
        result["insights"] = [
            {"category": i.category, "finding": i.finding, "confidence": i.confidence}
            for i in insights
        ]
        logger.info("[진화 #%d] %d개 인사이트 발견", gen, len(insights))

        # 2단계: 매매 규칙 업데이트
        new_rules, removed = self._update_rules(insights, trades)
        result["rules_added"] = new_rules
        result["rules_removed"] = removed

        # 3단계: 가중치 자동 조정
        weight_adj = self._adjust_weights(trades)
        result["weight_adjustments"] = weight_adj

        # 4단계: 전략 파라미터 최적화 (캔들 데이터 있을 때만)
        if candles and len(candles) >= 60:
            opt_result = self._optimize_strategy(candles)
            result["optimization"] = opt_result

            # 최적화 결과를 가중치에 반영
            if opt_result and opt_result.get("params"):
                params = opt_result["params"]
                param_to_weight = {
                    "technical_weight": "technical",
                    "pattern_weight": "pattern",
                    "sentiment_weight": "sentiment",
                    "market_weight": "market",
                    "price_level_weight": "price_level",
                }
                for param_key, weight_key in param_to_weight.items():
                    if param_key in params:
                        weight_adj[weight_key] = params[param_key] - 0.20  # 차이값을 조정으로

        # 5단계: 적합도(fitness) 평가
        fitness = self._evaluate_fitness(trades)
        result["fitness"] = fitness

        self.state.fitness_history.append({
            "generation": gen,
            "fitness": fitness,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "trade_count": len(trades),
        })
        # 최근 50세대만 유지
        if len(self.state.fitness_history) > 50:
            self.state.fitness_history = self.state.fitness_history[-50:]

        if fitness > self.state.best_fitness:
            self.state.best_fitness = fitness
            self.state.best_generation = gen
            logger.info("[진화 #%d] 신기록! 적합도=%.1f", gen, fitness)

        self.state.last_evolution = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._save_state()

        logger.info(
            "══ 진화 세대 #%d 완료: 적합도=%.1f (최고=%.1f @#%d) ══",
            gen, fitness, self.state.best_fitness, self.state.best_generation,
        )
        return result

    def get_active_rules(self) -> list[dict]:
        """현재 활성화된 매매 규칙을 반환한다."""
        return self.state.active_rules

    def get_strategy_adjustments(self) -> dict:
        """진화 결과에 기반한 전략 조정값을 반환한다.

        Returns:
            dict with keys matching ExpertStrategy.WEIGHTS
            (technical, pattern, sentiment, market, price_level)
            plus threshold/risk keys for apply_adjustments()
        """
        # 1. 학습기 조정값 가져오기
        raw_adj = self.learner.get_adjustments()

        # 2. WEIGHTS 호환 형태로 변환
        adjustments = {}

        # 학습기의 threshold/risk 값도 포함
        for key in ("buy_threshold_adj", "sell_threshold_adj",
                     "stop_loss_adj", "take_profit_adj",
                     "avoid_times", "prefer_times"):
            if key in raw_adj and raw_adj[key]:
                adjustments[key] = raw_adj[key]

        # 3. 활성 규칙에서 조정값 수집 (모든 타입 처리)
        for rule in self.state.active_rules:
            if not rule.get("active", True):
                continue
            target = rule.get("target", "")
            adj = rule.get("adjustment", 0)

            if isinstance(adj, (int, float)) and target:
                adjustments[target] = adjustments.get(target, 0) + adj

        # 4. 가중치 조정 (weight_history의 최신 조정)
        if self.state.weight_history:
            latest = self.state.weight_history[-1]
            for key, val in latest.get("adjustments", {}).items():
                if key in ("technical", "pattern", "sentiment", "market", "price_level"):
                    adjustments[key] = adjustments.get(key, 0) + val

        return adjustments

    def should_evolve(self, trade_count_since_last: int) -> bool:
        """진화 실행 시점인지 확인한다."""
        return trade_count_since_last >= self.EVOLUTION_INTERVAL

    # ─────────── 내부 메서드 ───────────

    def _update_rules(self, insights, trades: list[dict]) -> tuple[int, int]:
        """인사이트 기반으로 매매 규칙을 업데이트한다."""
        added = 0
        removed = 0

        # 높은 신뢰도 인사이트를 규칙으로 변환
        for insight in insights:
            if insight.confidence < 0.6:
                continue

            rule_key = f"{insight.category}:{insight.key}"

            # 중복 확인
            existing = [r for r in self.state.active_rules if r.get("key") == rule_key]
            if existing:
                # 기존 규칙 업데이트
                existing[0]["confidence"] = insight.confidence
                existing[0]["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                continue

            new_rule = {
                "key": rule_key,
                "type": insight.category,
                "action": insight.action,
                "confidence": insight.confidence,
                "created": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "active": True,
                "generation": self.state.generation,
            }

            # 조정값 결정
            if insight.category == "time":
                if "회피" in insight.action:
                    new_rule["target"] = "avoid_times"
                    new_rule["adjustment"] = insight.key
                elif "선호" in insight.action:
                    new_rule["target"] = "prefer_times"
                    new_rule["adjustment"] = insight.key
            elif insight.category == "risk":
                if "손절" in insight.action and "타이트" in insight.action:
                    new_rule["target"] = "stop_loss_adj"
                    new_rule["adjustment"] = 0.5
                elif "익절" in insight.action and "상향" in insight.action:
                    new_rule["target"] = "take_profit_adj"
                    new_rule["adjustment"] = 0.5
            elif insight.category == "threshold":
                if "매수기준상향" in insight.action:
                    new_rule["target"] = "buy_threshold_adj"
                    new_rule["adjustment"] = 0.05

            self.state.active_rules.append(new_rule)
            added += 1

        # 오래되고 성과 없는 규칙 제거
        if len(self.state.active_rules) > self.MAX_RULES:
            # 신뢰도 낮은 순으로 제거
            self.state.active_rules.sort(key=lambda r: r.get("confidence", 0), reverse=True)
            removed = len(self.state.active_rules) - self.MAX_RULES
            self.state.active_rules = self.state.active_rules[:self.MAX_RULES]

        if added or removed:
            logger.info("[진화] 규칙 변경: +%d / -%d (총 %d개)", added, removed, len(self.state.active_rules))

        return added, removed

    def _adjust_weights(self, trades: list[dict]) -> dict:
        """거래 성과 기반으로 전략 가중치를 조정한다."""
        adjustments = {}

        sells = [t for t in trades if t.get("side") == "SELL"]
        if len(sells) < 5:
            return adjustments

        # 사유별 성과 분석
        reason_performance = {}
        for t in sells:
            reason = t.get("reason", "")
            profit = t.get("profit_loss", 0)
            for keyword in ["기술", "패턴", "뉴스", "모멘텀", "볼린저", "RSI", "MACD"]:
                if keyword in reason:
                    if keyword not in reason_performance:
                        reason_performance[keyword] = {"wins": 0, "losses": 0, "profit": 0}
                    reason_performance[keyword]["profit"] += profit
                    if profit > 0:
                        reason_performance[keyword]["wins"] += 1
                    else:
                        reason_performance[keyword]["losses"] += 1

        # 성과 좋은 지표 가중치 상향, 나쁜 지표 하향
        keyword_to_weight = {
            "기술": "technical",
            "RSI": "technical",
            "MACD": "technical",
            "볼린저": "technical",
            "패턴": "pattern",
            "뉴스": "sentiment",
            "모멘텀": "technical",
        }

        for keyword, perf in reason_performance.items():
            total = perf["wins"] + perf["losses"]
            if total < 3:
                continue
            win_rate = perf["wins"] / total
            weight_key = keyword_to_weight.get(keyword, "")
            if not weight_key:
                continue

            if win_rate > 0.6:
                adjustments[weight_key] = adjustments.get(weight_key, 0) + 0.02
            elif win_rate < 0.35:
                adjustments[weight_key] = adjustments.get(weight_key, 0) - 0.02

        if adjustments:
            self.state.weight_history.append({
                "generation": self.state.generation,
                "adjustments": adjustments,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
            })
            if len(self.state.weight_history) > 30:
                self.state.weight_history = self.state.weight_history[-30:]
            logger.info("[진화] 가중치 조정: %s", adjustments)

        return adjustments

    def _optimize_strategy(self, candles: list[dict]) -> dict:
        """전략 파라미터를 최적화한다."""
        try:
            result = self.optimizer.optimize("expert", candles, max_iterations=20)
            if result.best_score > 0:
                logger.info("[진화] 최적화 완료: 점수=%.1f", result.best_score)
                return {"score": result.best_score, "params": result.best_params}
        except Exception as e:
            logger.warning("[진화] 최적화 실패: %s", e)
        return None

    def _evaluate_fitness(self, trades: list[dict]) -> float:
        """현재 전략의 적합도를 평가한다."""
        sells = [t for t in trades if t.get("side") == "SELL"]
        if not sells:
            return 0

        # 승률 (40%)
        wins = sum(1 for t in sells if t.get("profit_loss", 0) > 0)
        win_rate = wins / len(sells) * 100

        # 수익 팩터 (30%)
        total_win = sum(t.get("profit_loss", 0) for t in sells if t.get("profit_loss", 0) > 0)
        total_loss = abs(sum(t.get("profit_loss", 0) for t in sells if t.get("profit_loss", 0) < 0))
        profit_factor = (total_win / total_loss) if total_loss > 0 else 5.0
        profit_factor = min(profit_factor, 5.0)

        # 거래 빈도 (15%)
        trade_score = min(len(sells) / 20, 1.0) * 100

        # 연속 손실 리스크 (15%)
        max_streak = 0
        streak = 0
        for t in sells:
            if t.get("profit_loss", 0) < 0:
                streak += 1
                max_streak = max(max_streak, streak)
            else:
                streak = 0
        risk_score = max(0, 100 - max_streak * 15)

        fitness = (
            win_rate * 0.4
            + (profit_factor / 5 * 100) * 0.3
            + trade_score * 0.15
            + risk_score * 0.15
        )
        return round(fitness, 1)

    def _load_state(self) -> EvolutionState:
        if EVOLUTION_FILE.exists():
            try:
                data = json.loads(EVOLUTION_FILE.read_text(encoding="utf-8"))
                state = EvolutionState(**{k: v for k, v in data.items() if k in EvolutionState.__dataclass_fields__})
                logger.info("진화 상태 복원: 세대 #%d, 최고적합도=%.1f", state.generation, state.best_fitness)
                return state
            except (json.JSONDecodeError, TypeError, OSError):
                pass
        return EvolutionState()

    def _save_state(self):
        EVOLUTION_FILE.parent.mkdir(parents=True, exist_ok=True)
        from dataclasses import asdict
        EVOLUTION_FILE.write_text(
            json.dumps(asdict(self.state), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
