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

    # v2.9: 레짐별 전략 성과 기록
    regime_performance: dict = field(default_factory=dict)
    # v2.9: 자동 리스크 파라미터 진화
    risk_params: dict = field(default_factory=lambda: {
        "stop_loss_pct": -3.0,
        "trailing_base": 3.0,
        "take_profit_pct": 20.0,
        "cooldown_seconds": 900,
    })
    risk_evolution_history: list[dict] = field(default_factory=list)
    # v2.9: 패턴 메모리 DB
    pattern_memory: list[dict] = field(default_factory=list)
    # v3.2: 마지막 진화 시점 총 매도 수 (재시작 시 누적 카운팅용)
    last_evolve_sell_count: int = 0


class EvolutionEngine:
    """자동 진화 엔진.

    거래 결과를 분석하여 전략을 자동으로 개선한다.
    매 N건의 거래 후 진화 사이클을 실행한다.
    """

    EVOLUTION_INTERVAL = 3  # N건 거래마다 진화 (v3.2: 15→3 빠른 적응)
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

        # 5단계: 리스크 파라미터 자동 진화 (v2.9)
        risk_params = self.evolve_risk_params(trades)
        result["risk_params"] = risk_params

        # 6단계: 적합도(fitness) 평가
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
        if not sells:
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
            if total < 1:
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

    # ──────────────────────────────────────────────
    # v2.9: 레짐 적응형 전략 전환
    # ──────────────────────────────────────────────

    def record_regime_trade(self, regime: str, profit_rate: float,
                             strategy_mode: str = "expert"):
        """시장 레짐별 매매 성과를 기록한다.

        레짐(상승/하락/횡보/급변)에서 어떤 전략이 잘 먹혔는지 학습.
        """
        rp = self.state.regime_performance
        if regime not in rp:
            rp[regime] = {"trades": 0, "wins": 0, "total_profit": 0,
                          "strategies": {}}
        rp[regime]["trades"] += 1
        rp[regime]["total_profit"] += profit_rate
        if profit_rate > 0:
            rp[regime]["wins"] += 1

        # 전략별 성과
        strats = rp[regime]["strategies"]
        if strategy_mode not in strats:
            strats[strategy_mode] = {"trades": 0, "wins": 0, "profit": 0}
        strats[strategy_mode]["trades"] += 1
        strats[strategy_mode]["profit"] += profit_rate
        if profit_rate > 0:
            strats[strategy_mode]["wins"] += 1

    def get_regime_strategy(self, regime: str) -> dict:
        """현재 레짐에 최적화된 전략 조정값을 반환한다.

        학습된 레짐별 성과가 충분하면 공격/방어 모드를 자동 전환.

        Returns:
            dict with keys:
            - mode: "aggressive" / "defensive" / "scalping" / "normal"
            - buy_threshold_adj: 매수 임계값 조정
            - sell_threshold_adj: 매도 임계값 조정
            - position_size_mult: 포지션 크기 배수
            - reason: 판단 근거
        """
        rp = self.state.regime_performance.get(regime, {})
        total = rp.get("trades", 0)

        result = {"mode": "normal", "buy_threshold_adj": 0,
                  "sell_threshold_adj": 0, "position_size_mult": 1.0,
                  "reason": "기본 모드"}

        # 충분한 학습 데이터 없으면 기본 규칙 사용
        if total < 5:
            if regime == "trending_up":
                result.update(mode="aggressive", buy_threshold_adj=-0.05,
                              position_size_mult=1.2,
                              reason=f"상승장 기본 공격 (학습 데이터 부족, {total}건)")
            elif regime == "trending_down":
                result.update(mode="defensive", buy_threshold_adj=0.10,
                              sell_threshold_adj=-0.05, position_size_mult=0.6,
                              reason=f"하락장 기본 방어 (학습 데이터 부족, {total}건)")
            elif regime == "volatile":
                result.update(mode="scalping", buy_threshold_adj=0.08,
                              position_size_mult=0.5,
                              reason=f"급변장 기본 스캘핑 (학습 데이터 부족, {total}건)")
            return result

        # 학습된 성과 기반 전략 전환
        win_rate = (rp["wins"] / total * 100) if total > 0 else 50
        avg_profit = rp["total_profit"] / total if total > 0 else 0

        if regime == "trending_up":
            if win_rate > 60 and avg_profit > 1.0:
                result.update(mode="aggressive", buy_threshold_adj=-0.08,
                              position_size_mult=1.3,
                              reason=f"상승장 공격 (승률={win_rate:.0f}% 수익률={avg_profit:.1f}%)")
            elif win_rate < 40:
                result.update(mode="defensive", buy_threshold_adj=0.05,
                              position_size_mult=0.8,
                              reason=f"상승장이나 성과부진→방어 (승률={win_rate:.0f}%)")
            else:
                result.update(mode="normal",
                              reason=f"상승장 보통 (승률={win_rate:.0f}%)")

        elif regime == "trending_down":
            if win_rate > 50:
                result.update(mode="defensive", buy_threshold_adj=0.08,
                              sell_threshold_adj=-0.03, position_size_mult=0.7,
                              reason=f"하락장 방어 (승률={win_rate:.0f}% 양호)")
            else:
                result.update(mode="defensive", buy_threshold_adj=0.15,
                              sell_threshold_adj=-0.08, position_size_mult=0.4,
                              reason=f"하락장 강방어 (승률={win_rate:.0f}% 저조)")

        elif regime == "volatile":
            if avg_profit > 0:
                result.update(mode="scalping", buy_threshold_adj=0.05,
                              position_size_mult=0.6,
                              reason=f"급변장 스캘핑 (평균수익={avg_profit:.1f}%)")
            else:
                result.update(mode="defensive", buy_threshold_adj=0.12,
                              position_size_mult=0.3,
                              reason=f"급변장 손실→초방어 (평균수익={avg_profit:.1f}%)")

        elif regime == "ranging":
            result.update(mode="scalping", buy_threshold_adj=0.03,
                          position_size_mult=0.8,
                          reason=f"횡보장 스캘핑 (승률={win_rate:.0f}%)")

        logger.info("[레짐 전략] %s → %s | %s", regime, result["mode"], result["reason"])
        return result

    # ──────────────────────────────────────────────
    # v2.9: 자동 리스크 파라미터 진화
    # ──────────────────────────────────────────────

    def evolve_risk_params(self, trades: list[dict]) -> dict:
        """거래 결과를 분석하여 리스크 파라미터를 자동 진화시킨다.

        - 손절이 너무 자주 → 손절폭 확대
        - 손절이 너무 드물고 큰 손실 → 손절폭 축소
        - 트레일링이 너무 일찍 → 트레일링 확대
        - 익절이 너무 일찍 → 익절 상향
        """
        sells = [t for t in trades if t.get("side") == "SELL"]
        if not sells:
            return self.state.risk_params

        rp = self.state.risk_params
        changes = []

        # 손절 분석
        stop_losses = [t for t in sells if "손절" in t.get("reason", "")]
        stop_rate = len(stop_losses) / len(sells) * 100
        avg_stop_loss = (sum(t.get("profit_rate", 0) for t in stop_losses) / len(stop_losses)
                         if stop_losses else 0)

        if stop_rate > 30:
            # 손절 너무 자주 → 폭 확대 (더 참기)
            old = rp["stop_loss_pct"]
            rp["stop_loss_pct"] = max(-8.0, old - 0.5)
            changes.append(f"손절폭 확대: {old:.1f}%→{rp['stop_loss_pct']:.1f}% (빈도={stop_rate:.0f}%)")
        elif stop_rate < 5 and any(t.get("profit_rate", 0) < -5 for t in sells):
            # 손절 드문데 큰 손실 있음 → 폭 축소
            old = rp["stop_loss_pct"]
            rp["stop_loss_pct"] = min(-1.5, old + 0.5)
            changes.append(f"손절폭 축소: {old:.1f}%→{rp['stop_loss_pct']:.1f}%")

        # 트레일링 분석
        trailing_sells = [t for t in sells if "트레일링" in t.get("reason", "")]
        if trailing_sells:
            trailing_profits = [t.get("profit_rate", 0) for t in trailing_sells]
            avg_trail = sum(trailing_profits) / len(trailing_profits)
            if avg_trail < 2.0 and len(trailing_sells) > 3:
                # 트레일링이 너무 일찍 발동 → 확대
                old = rp["trailing_base"]
                rp["trailing_base"] = min(6.0, old + 0.5)
                changes.append(f"트레일링 확대: {old:.1f}%→{rp['trailing_base']:.1f}% (평균수익={avg_trail:.1f}%)")
            elif avg_trail > 8.0:
                # 트레일링 적절 — 약간 축소 가능
                old = rp["trailing_base"]
                rp["trailing_base"] = max(2.0, old - 0.3)
                changes.append(f"트레일링 축소: {old:.1f}%→{rp['trailing_base']:.1f}% (평균수익={avg_trail:.1f}%)")

        # 익절 분석
        profit_sells = [t for t in sells if t.get("profit_rate", 0) > 0]
        if len(profit_sells) >= 5:
            max_profit = max(t.get("profit_rate", 0) for t in profit_sells)
            avg_profit = sum(t.get("profit_rate", 0) for t in profit_sells) / len(profit_sells)
            # 최대 수익이 익절 라인의 2배 이상 → 익절 상향
            if max_profit > rp["take_profit_pct"] * 2:
                old = rp["take_profit_pct"]
                rp["take_profit_pct"] = min(40.0, old + 2.0)
                changes.append(f"익절 상향: {old:.0f}%→{rp['take_profit_pct']:.0f}% (최대수익={max_profit:.1f}%)")
            elif max_profit < rp["take_profit_pct"] * 0.5 and avg_profit < 3.0:
                old = rp["take_profit_pct"]
                rp["take_profit_pct"] = max(10.0, old - 2.0)
                changes.append(f"익절 하향: {old:.0f}%→{rp['take_profit_pct']:.0f}% (평균수익={avg_profit:.1f}%)")

        if changes:
            self.state.risk_evolution_history.append({
                "generation": self.state.generation,
                "changes": changes,
                "params": dict(rp),
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
            })
            if len(self.state.risk_evolution_history) > 30:
                self.state.risk_evolution_history = self.state.risk_evolution_history[-30:]
            logger.info("[리스크 진화] %s", " | ".join(changes))

        return rp

    # ──────────────────────────────────────────────
    # v2.9: 패턴 메모리 DB
    # ──────────────────────────────────────────────

    def memorize_pattern(self, stock_code: str, pattern_snapshot: dict,
                          outcome: dict):
        """매매 패턴과 결과를 메모리에 저장한다.

        나중에 유사한 패턴이 감지되면 과거 성공률로 매매 판단을 보강한다.

        Args:
            stock_code: 종목 코드
            pattern_snapshot: 매매 시점의 기술적 스냅샷
                {trend_score, momentum_score, rsi, bb_position, volume_ratio,
                 macd_cross, regime, ...}
            outcome: 매매 결과
                {profit_rate, hold_days, decision, reason}
        """
        memory = {
            "stock_code": stock_code,
            "snapshot": pattern_snapshot,
            "outcome": outcome,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
        self.state.pattern_memory.append(memory)

        # 최대 500개 유지 (FIFO)
        if len(self.state.pattern_memory) > 500:
            self.state.pattern_memory = self.state.pattern_memory[-500:]

    def recall_similar_patterns(self, current_snapshot: dict,
                                  top_k: int = 5) -> dict:
        """현재 기술적 스냅샷과 유사한 과거 패턴을 검색한다.

        유사도 기반 가중 투표로 예상 결과를 산출한다.

        Returns:
            dict with keys:
            - matches: 유사 패턴 수
            - avg_profit: 유사 패턴의 평균 수익률
            - win_rate: 유사 패턴의 승률
            - confidence: 신뢰도 (0~1)
            - bias: "bullish" / "bearish" / "neutral"
            - similar_patterns: 상위 유사 패턴 리스트
        """
        if len(self.state.pattern_memory) < 10:
            return {"matches": 0, "avg_profit": 0, "win_rate": 0,
                    "confidence": 0, "bias": "neutral", "similar_patterns": []}

        # 유사도 계산 (유클리드 거리 기반)
        scored = []
        keys = ["trend_score", "momentum_score", "rsi", "bb_position",
                "volume_ratio"]
        current_vals = [current_snapshot.get(k, 0) for k in keys]

        # 정규화 범위
        norms = {"trend_score": 2.0, "momentum_score": 2.0, "rsi": 100.0,
                 "bb_position": 1.0, "volume_ratio": 5.0}

        for mem in self.state.pattern_memory:
            snap = mem.get("snapshot", {})
            mem_vals = [snap.get(k, 0) for k in keys]

            # 정규화된 유클리드 거리
            dist = 0
            for i, key in enumerate(keys):
                norm = norms.get(key, 1.0)
                diff = (current_vals[i] - mem_vals[i]) / norm if norm > 0 else 0
                dist += diff ** 2
            dist = dist ** 0.5
            similarity = max(0, 1.0 - dist)
            scored.append((similarity, mem))

        # 상위 K개 추출
        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:top_k]

        if not top or top[0][0] < 0.3:
            return {"matches": 0, "avg_profit": 0, "win_rate": 0,
                    "confidence": 0, "bias": "neutral", "similar_patterns": []}

        # 가중 투표
        total_weight = sum(s for s, _ in top)
        weighted_profit = sum(s * m["outcome"].get("profit_rate", 0) for s, m in top)
        avg_profit = weighted_profit / total_weight if total_weight > 0 else 0

        wins = sum(1 for _, m in top if m["outcome"].get("profit_rate", 0) > 0)
        win_rate = wins / len(top) * 100

        # 유사도가 높을수록 신뢰도 상승
        confidence = min(1.0, top[0][0] * 0.6 + (len(top) / top_k) * 0.4)

        bias = "neutral"
        if avg_profit > 1.0 and win_rate > 55:
            bias = "bullish"
        elif avg_profit < -0.5 and win_rate < 45:
            bias = "bearish"

        similar = [{
            "similarity": round(s, 3),
            "profit_rate": m["outcome"].get("profit_rate", 0),
            "decision": m["outcome"].get("decision", ""),
            "timestamp": m.get("timestamp", ""),
        } for s, m in top]

        logger.info(
            "[패턴 메모리] %d개 유사 패턴: 평균수익=%.1f%% 승률=%.0f%% 편향=%s",
            len(top), avg_profit, win_rate, bias,
        )

        return {
            "matches": len(top),
            "avg_profit": round(avg_profit, 2),
            "win_rate": round(win_rate, 1),
            "confidence": round(confidence, 3),
            "bias": bias,
            "similar_patterns": similar,
        }

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
