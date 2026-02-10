"""전략 파라미터 최적화기.

과거 거래 성과를 분석하여 전략 파라미터를 자동 조정한다.
"""

import json
import random
from dataclasses import dataclass, field
from pathlib import Path

from learning.backtester import Backtester, BacktestResult
from utils.logger import setup_logger

logger = setup_logger("oshms.learning.optimizer")


@dataclass
class OptimizationResult:
    """최적화 결과."""
    best_params: dict = field(default_factory=dict)
    best_score: float = 0
    iterations: int = 0
    all_results: list = field(default_factory=list)


class StrategyOptimizer:
    """전략 파라미터 최적화기.

    그리드 서치 + 랜덤 서치를 결합하여 최적 파라미터를 탐색한다.
    """

    PARAMS_FILE = Path("config/optimized_params.json")

    # 탐색 범위
    PARAM_RANGES = {
        "scalping": {
            "bb_period": [15, 20, 25],
            "bb_std": [1.5, 2.0, 2.5],
            "rsi_period": [10, 14, 20],
            "rsi_oversold": [25, 30, 35],
            "rsi_overbought": [65, 70, 75],
        },
        "momentum": {
            "fast_period": [3, 5, 7],
            "slow_period": [15, 20, 25],
            "volume_multiplier": [1.5, 2.0, 2.5, 3.0],
        },
        "expert": {
            "technical_weight": [0.25, 0.30, 0.35, 0.40],
            "pattern_weight": [0.10, 0.15, 0.20],
            "sentiment_weight": [0.15, 0.20, 0.25],
            "buy_threshold": [0.20, 0.25, 0.30, 0.35],
            "sell_threshold": [-0.15, -0.20, -0.25],
        },
    }

    def __init__(self):
        self.backtester = Backtester()

    def optimize(
        self, strategy_name: str, candles: list[dict], max_iterations: int = 50,
    ) -> OptimizationResult:
        """전략 파라미터를 최적화한다."""
        ranges = self.PARAM_RANGES.get(strategy_name, {})
        if not ranges:
            logger.warning("최적화 범위 없음: %s", strategy_name)
            return OptimizationResult()

        result = OptimizationResult()
        best_score = -999
        best_params = {}

        # 그리드 + 랜덤 서치
        param_combos = self._generate_combinations(ranges, max_iterations)

        for i, params in enumerate(param_combos):
            strategy = self._create_strategy(strategy_name, params)
            if strategy is None:
                continue

            bt_result = self.backtester.run(strategy, candles)
            score = bt_result.score()

            result.all_results.append({"params": params, "score": score})

            if score > best_score:
                best_score = score
                best_params = params
                logger.info(
                    "최적화 [%d/%d] 새로운 최고: 점수=%.1f 파라미터=%s",
                    i + 1, len(param_combos), score, params,
                )

        result.best_params = best_params
        result.best_score = best_score
        result.iterations = len(param_combos)

        # 결과 저장
        self._save_params(strategy_name, best_params, best_score)

        logger.info(
            "최적화 완료 [%s]: 최고점수=%.1f 탐색=%d회",
            strategy_name, best_score, result.iterations,
        )
        return result

    def load_optimized_params(self, strategy_name: str) -> dict:
        """저장된 최적 파라미터를 로드한다."""
        if self.PARAMS_FILE.exists():
            try:
                data = json.loads(self.PARAMS_FILE.read_text(encoding="utf-8"))
                return data.get(strategy_name, {}).get("params", {})
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _generate_combinations(self, ranges: dict, max_count: int) -> list[dict]:
        """파라미터 조합을 생성한다."""
        keys = list(ranges.keys())
        values = list(ranges.values())

        # 전체 그리드 크기 계산
        total = 1
        for v in values:
            total *= len(v)

        combos = []
        if total <= max_count:
            # 전수 탐색
            self._grid_recursive(keys, values, 0, {}, combos)
        else:
            # 랜덤 샘플링
            for _ in range(max_count):
                combo = {}
                for k, v in zip(keys, values):
                    combo[k] = random.choice(v)
                combos.append(combo)

        return combos

    def _grid_recursive(self, keys, values, idx, current, results):
        if idx == len(keys):
            results.append(dict(current))
            return
        for v in values[idx]:
            current[keys[idx]] = v
            self._grid_recursive(keys, values, idx + 1, current, results)

    def _create_strategy(self, name: str, params: dict):
        """파라미터로 전략 객체를 생성한다."""
        try:
            if name == "scalping":
                from strategy.scalping import ScalpingStrategy
                return ScalpingStrategy(**{k: v for k, v in params.items() if k in (
                    "bb_period", "bb_std", "rsi_period", "rsi_oversold", "rsi_overbought",
                )})
            elif name == "momentum":
                from strategy.momentum import MomentumStrategy
                return MomentumStrategy(**{k: v for k, v in params.items() if k in (
                    "fast_period", "slow_period", "volume_multiplier",
                )})
            elif name == "expert":
                from strategy.expert import ExpertStrategy
                strategy = ExpertStrategy()
                if "technical_weight" in params:
                    strategy.WEIGHTS["technical"] = params["technical_weight"]
                if "pattern_weight" in params:
                    strategy.WEIGHTS["pattern"] = params["pattern_weight"]
                if "sentiment_weight" in params:
                    strategy.WEIGHTS["sentiment"] = params["sentiment_weight"]
                if "buy_threshold" in params:
                    strategy.BUY_THRESHOLD = params["buy_threshold"]
                if "sell_threshold" in params:
                    strategy.SELL_THRESHOLD = params["sell_threshold"]
                return strategy
        except Exception as e:
            logger.debug("전략 생성 실패: %s", e)
        return None

    def _save_params(self, strategy_name: str, params: dict, score: float):
        data = {}
        if self.PARAMS_FILE.exists():
            try:
                data = json.loads(self.PARAMS_FILE.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass

        data[strategy_name] = {"params": params, "score": score}
        self.PARAMS_FILE.parent.mkdir(parents=True, exist_ok=True)
        self.PARAMS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
