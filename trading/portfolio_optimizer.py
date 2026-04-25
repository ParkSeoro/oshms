"""실시간 포트폴리오 최적화.

보유 종목 간 상관관계 분석 + 샤프비율 기반 포지션 사이징 최적화.
과도한 집중 투자를 방지하고, 분산 효과를 극대화한다.

v3.2 신규
"""

import json
import math
from collections import defaultdict
from pathlib import Path

from utils.logger import setup_logger

logger = setup_logger("oshms.trading.portfolio")

PORTFOLIO_STATE_FILE = Path("data/portfolio_state.json")


class PortfolioOptimizer:
    """실시간 포트폴리오 최적화 엔진.

    사용법:
        optimizer = PortfolioOptimizer()
        # 매 사이클마다
        optimizer.update(positions, trade_history)
        # 매수 전 포지션 크기 조정
        mult = optimizer.get_position_size_multiplier(stock_code)
    """

    MAX_CORRELATION = 0.7      # 상관계수 경고 임계값
    RISK_FREE_RATE = 0.035     # 무위험이자율 (연 3.5%)
    MAX_SINGLE_WEIGHT = 0.40   # 단일 종목 최대 비중 (40%)
    MIN_SIZE_MULT = 0.5        # 최소 포지션 승수
    MAX_SIZE_MULT = 1.3        # 최대 포지션 승수

    def __init__(self):
        # 종목별 일간 수익률 기록
        self.daily_returns: dict[str, list[float]] = {}
        # 종목 간 상관계수
        self.correlations: dict[str, float] = {}
        # 포트폴리오 샤프비율
        self.sharpe_ratio: float = 0.0
        # 포지션 사이즈 승수 캐시
        self._size_multipliers: dict[str, float] = {}
        # 집중도 경고
        self._concentration_warnings: list[str] = []

        self._load()

    def update(self, positions: dict, trade_history: list) -> None:
        """포트폴리오 상태를 갱신한다.

        Args:
            positions: OrderManager.positions (code → Position)
            trade_history: OrderManager.trade_history (TradeRecord 리스트)
        """
        if not positions:
            self._size_multipliers = {}
            return

        # 1. 종목별 수익률 업데이트
        self._update_returns(positions, trade_history)

        # 2. 상관관계 계산
        self._calc_correlations()

        # 3. 샤프비율 계산
        self._calc_sharpe(positions)

        # 4. 집중도 확인
        self._check_concentration(positions)

        # 5. 포지션 사이즈 승수 계산
        self._calc_size_multipliers(positions)

        self._save()

    def get_position_size_multiplier(self, stock_code: str) -> float:
        """해당 종목의 포지션 사이즈 승수를 반환한다.

        Returns:
            0.5 ~ 1.3 범위의 승수
        """
        return self._size_multipliers.get(stock_code, 1.0)

    def should_block_buy(self, stock_code: str, positions: dict) -> tuple[bool, str]:
        """포트폴리오 관점에서 매수를 차단해야 하는지 판단한다.

        Returns:
            (차단 여부, 사유)
        """
        if not positions:
            return False, ""

        # 1. 이미 보유 중인 종목
        if stock_code in positions:
            return True, "이미 보유 중"

        # 2. 기존 보유 종목과 높은 상관관계
        for held_code in positions:
            pair_key = self._pair_key(stock_code, held_code)
            corr = self.correlations.get(pair_key, 0)
            if corr > self.MAX_CORRELATION:
                return True, f"{held_code}와 높은 상관관계 ({corr:.2f})"

        # 3. 포트폴리오 집중도 과다
        total_value = sum(
            p.current_price * p.quantity for p in positions.values()
            if hasattr(p, 'current_price')
        )
        if total_value > 0:
            max_pos_value = max(
                p.current_price * p.quantity for p in positions.values()
                if hasattr(p, 'current_price')
            )
            if max_pos_value / total_value > self.MAX_SINGLE_WEIGHT and len(positions) >= 3:
                return False, ""  # 집중도 높지만 차단까지는 아님

        return False, ""

    def get_portfolio_report(self) -> dict:
        """대시보드용 포트폴리오 분석 리포트."""
        return {
            "sharpe_ratio": round(self.sharpe_ratio, 3),
            "correlations": {k: round(v, 3) for k, v in self.correlations.items()},
            "size_multipliers": {k: round(v, 3) for k, v in self._size_multipliers.items()},
            "concentration_warnings": self._concentration_warnings,
            "tracked_stocks": len(self.daily_returns),
        }

    def _update_returns(self, positions: dict, trade_history: list) -> None:
        """종목별 일간 수익률을 업데이트한다."""
        # 현재 보유 중인 종목의 수익률 기록
        for code, pos in positions.items():
            if code not in self.daily_returns:
                self.daily_returns[code] = []
            if hasattr(pos, 'profit_rate'):
                self.daily_returns[code].append(pos.profit_rate)
                # 최근 60건만 유지
                if len(self.daily_returns[code]) > 60:
                    self.daily_returns[code] = self.daily_returns[code][-60:]

        # 과거 거래 기록에서도 수익률 추출
        for trade in trade_history:
            code = trade.stock_code if hasattr(trade, 'stock_code') else trade.get("stock_code", "")
            rate = trade.profit_rate if hasattr(trade, 'profit_rate') else trade.get("profit_rate", 0)
            side = trade.side if hasattr(trade, 'side') else trade.get("side", "")

            if side == "SELL" and code and rate != 0:
                if code not in self.daily_returns:
                    self.daily_returns[code] = []
                if rate not in self.daily_returns[code]:
                    self.daily_returns[code].append(rate)

        # 보유 안 하고 거래 기록도 없는 종목 정리
        stale = [k for k in self.daily_returns if len(self.daily_returns[k]) == 0]
        for k in stale:
            del self.daily_returns[k]

    def _calc_correlations(self) -> None:
        """종목 간 피어슨 상관계수를 계산한다."""
        self.correlations = {}
        codes = [c for c, r in self.daily_returns.items() if len(r) >= 5]

        for i in range(len(codes)):
            for j in range(i + 1, len(codes)):
                code_a, code_b = codes[i], codes[j]
                returns_a = self.daily_returns[code_a]
                returns_b = self.daily_returns[code_b]

                # 길이 맞추기 (짧은 쪽에 맞춤)
                min_len = min(len(returns_a), len(returns_b))
                if min_len < 3:
                    continue

                ra = returns_a[-min_len:]
                rb = returns_b[-min_len:]

                corr = self._pearson_correlation(ra, rb)
                pair_key = self._pair_key(code_a, code_b)
                self.correlations[pair_key] = corr

                if abs(corr) > self.MAX_CORRELATION:
                    logger.info(
                        "포트폴리오 상관관계 경고: %s ↔ %s = %.3f",
                        code_a, code_b, corr,
                    )

    def _calc_sharpe(self, positions: dict) -> None:
        """포트폴리오 샤프비율을 계산한다."""
        if not positions:
            self.sharpe_ratio = 0.0
            return

        # 포트폴리오 가중 수익률
        total_value = sum(
            p.current_price * p.quantity for p in positions.values()
            if hasattr(p, 'current_price') and p.current_price > 0
        )
        if total_value <= 0:
            self.sharpe_ratio = 0.0
            return

        weighted_returns = []
        for code, pos in positions.items():
            if not hasattr(pos, 'current_price') or pos.current_price <= 0:
                continue
            weight = (pos.current_price * pos.quantity) / total_value
            returns = self.daily_returns.get(code, [])
            if returns:
                weighted_returns.append(sum(returns) / len(returns) * weight)

        if not weighted_returns:
            self.sharpe_ratio = 0.0
            return

        portfolio_return = sum(weighted_returns)
        # 일간 무위험이자율
        daily_rf = self.RISK_FREE_RATE / 252

        # 포트폴리오 표준편차
        all_returns = []
        for code in positions:
            all_returns.extend(self.daily_returns.get(code, []))

        if len(all_returns) < 2:
            self.sharpe_ratio = 0.0
            return

        mean_r = sum(all_returns) / len(all_returns)
        variance = sum((r - mean_r) ** 2 for r in all_returns) / (len(all_returns) - 1)
        std_dev = math.sqrt(variance) if variance > 0 else 0.001

        self.sharpe_ratio = (portfolio_return - daily_rf) / std_dev

    def _check_concentration(self, positions: dict) -> None:
        """포트폴리오 집중도를 확인한다."""
        self._concentration_warnings = []

        if len(positions) < 2:
            return

        total_value = sum(
            p.current_price * p.quantity for p in positions.values()
            if hasattr(p, 'current_price') and p.current_price > 0
        )
        if total_value <= 0:
            return

        for code, pos in positions.items():
            if not hasattr(pos, 'current_price') or pos.current_price <= 0:
                continue
            weight = (pos.current_price * pos.quantity) / total_value
            if weight > self.MAX_SINGLE_WEIGHT:
                self._concentration_warnings.append(
                    f"{code}: 비중 {weight*100:.1f}% (한도 {self.MAX_SINGLE_WEIGHT*100:.0f}%)"
                )

    def _calc_size_multipliers(self, positions: dict) -> None:
        """각 종목의 포지션 사이즈 승수를 계산한다."""
        self._size_multipliers = {}

        if not positions:
            return

        for code in positions:
            mult = 1.0

            # 1. 상관관계 패널티: 기존 보유 종목과 높은 상관관계면 축소
            for pair_key, corr in self.correlations.items():
                if code in pair_key and corr > 0.5:
                    # 상관계수 0.5~1.0 → 승수 0.7~1.0
                    penalty = 1.0 - (corr - 0.5) * 0.6
                    mult = min(mult, max(self.MIN_SIZE_MULT, penalty))

            # 2. 집중도 패널티
            total_value = sum(
                p.current_price * p.quantity for p in positions.values()
                if hasattr(p, 'current_price') and p.current_price > 0
            )
            if total_value > 0:
                pos = positions[code]
                if hasattr(pos, 'current_price') and pos.current_price > 0:
                    weight = (pos.current_price * pos.quantity) / total_value
                    if weight > 0.3:
                        mult *= 0.8

            # 3. 샤프비율 보너스: 포트폴리오 성과가 좋으면 약간 확대
            if self.sharpe_ratio > 1.0:
                mult = min(self.MAX_SIZE_MULT, mult * 1.1)

            self._size_multipliers[code] = round(
                max(self.MIN_SIZE_MULT, min(self.MAX_SIZE_MULT, mult)), 3
            )

    @staticmethod
    def _pearson_correlation(x: list[float], y: list[float]) -> float:
        """피어슨 상관계수를 계산한다."""
        n = len(x)
        if n < 3:
            return 0.0

        mean_x = sum(x) / n
        mean_y = sum(y) / n

        cov = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y)) / n
        std_x = math.sqrt(sum((xi - mean_x) ** 2 for xi in x) / n)
        std_y = math.sqrt(sum((yi - mean_y) ** 2 for yi in y) / n)

        if std_x == 0 or std_y == 0:
            return 0.0

        return max(-1.0, min(1.0, cov / (std_x * std_y)))

    @staticmethod
    def _pair_key(code_a: str, code_b: str) -> str:
        """정렬된 종목 쌍 키를 생성한다."""
        return f"{min(code_a, code_b)}:{max(code_a, code_b)}"

    def _load(self):
        """저장된 상태를 로드한다."""
        if PORTFOLIO_STATE_FILE.exists():
            try:
                data = json.loads(PORTFOLIO_STATE_FILE.read_text(encoding="utf-8"))
                self.daily_returns = data.get("daily_returns", {})
                self.correlations = data.get("correlations", {})
                self.sharpe_ratio = data.get("sharpe_ratio", 0.0)
                logger.info(
                    "포트폴리오 상태 복원: %d종목 추적, 샤프=%.3f",
                    len(self.daily_returns), self.sharpe_ratio,
                )
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning("포트폴리오 상태 로드 실패: %s", e)

    def _save(self):
        """현재 상태를 저장한다."""
        PORTFOLIO_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "daily_returns": self.daily_returns,
            "correlations": self.correlations,
            "sharpe_ratio": self.sharpe_ratio,
        }
        PORTFOLIO_STATE_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
