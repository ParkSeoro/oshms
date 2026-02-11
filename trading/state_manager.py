"""거래 상태 영속성 관리.

프로그램 종료 후 재시작 시에도 이전 상태를 이어서 실행할 수 있도록 한다.
"""

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from utils.logger import setup_logger

logger = setup_logger("oshms.trading.state")

STATE_FILE = Path("data/trading_state.json")

@dataclass
class TradingState:
    """영속적 거래 상태."""
    # 마지막 세션 정보
    last_strategy: str = "expert"
    last_target_stocks: list[str] = field(default_factory=list)
    last_interval: int = 10
    last_market: str = "KR"  # KR, US, JP, HK, etc.
    last_active: str = ""

    # 누적 통계
    total_sessions: int = 0
    total_trades: int = 0
    total_wins: int = 0
    total_losses: int = 0
    total_profit: float = 0
    best_trade: float = 0
    worst_trade: float = 0

    # 세션 히스토리 (최근 30개)
    session_history: list[dict] = field(default_factory=list)

    # 학습 설정
    auto_optimize_enabled: bool = True
    last_optimize_time: str = ""
    evolution_generation: int = 0


class StateManager:
    """거래 상태 관리자."""

    def __init__(self):
        self.state = self._load()

    def _load(self) -> TradingState:
        """저장된 상태를 로드한다."""
        if STATE_FILE.exists():
            try:
                data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
                state = TradingState(**{k: v for k, v in data.items() if k in TradingState.__dataclass_fields__})
                logger.info("거래 상태 복원: %d세션, %d거래, 수익=%+,.0f원",
                           state.total_sessions, state.total_trades, state.total_profit)
                return state
            except (json.JSONDecodeError, TypeError, OSError) as e:
                logger.warning("상태 로드 실패: %s", e)
        return TradingState()

    def save(self) -> None:
        """현재 상태를 저장한다."""
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        self.state.last_active = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        STATE_FILE.write_text(
            json.dumps(asdict(self.state), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def start_session(self, strategy: str, target_stocks: list[str], interval: int, market: str = "KR") -> None:
        """새 매매 세션을 시작한다."""
        self.state.last_strategy = strategy
        self.state.last_target_stocks = target_stocks or []
        self.state.last_interval = interval
        self.state.last_market = market
        self.state.total_sessions += 1
        self.save()
        logger.info("세션 #%d 시작 (전략=%s, 시장=%s)", self.state.total_sessions, strategy, market)

    def record_trade(self, profit: float, is_win: bool) -> None:
        """거래 결과를 기록한다."""
        self.state.total_trades += 1
        self.state.total_profit += profit
        if is_win:
            self.state.total_wins += 1
        else:
            self.state.total_losses += 1
        self.state.best_trade = max(self.state.best_trade, profit)
        self.state.worst_trade = min(self.state.worst_trade, profit)
        self.save()

    def end_session(self, trades: int, profit: float) -> None:
        """매매 세션을 종료한다."""
        session = {
            "end_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "strategy": self.state.last_strategy,
            "market": self.state.last_market,
            "trades": trades,
            "profit": profit,
        }
        self.state.session_history.append(session)
        # 최근 30개만 유지
        if len(self.state.session_history) > 30:
            self.state.session_history = self.state.session_history[-30:]
        self.save()
        logger.info("세션 종료: %d거래, 수익=%+,.0f원", trades, profit)

    def get_resume_info(self) -> dict:
        """이전 세션 복원에 필요한 정보를 반환한다."""
        return {
            "strategy": self.state.last_strategy,
            "target_stocks": self.state.last_target_stocks,
            "interval": self.state.last_interval,
            "market": self.state.last_market,
            "last_active": self.state.last_active,
            "total_profit": self.state.total_profit,
            "win_rate": (self.state.total_wins / self.state.total_trades * 100) if self.state.total_trades else 0,
        }

    def get_stats_summary(self) -> dict:
        """누적 통계 요약을 반환한다."""
        return {
            "total_sessions": self.state.total_sessions,
            "total_trades": self.state.total_trades,
            "total_wins": self.state.total_wins,
            "total_losses": self.state.total_losses,
            "total_profit": self.state.total_profit,
            "win_rate": (self.state.total_wins / self.state.total_trades * 100) if self.state.total_trades else 0,
            "best_trade": self.state.best_trade,
            "worst_trade": self.state.worst_trade,
            "evolution_gen": self.state.evolution_generation,
        }
