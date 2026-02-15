"""주문 관리 모듈.

매수/매도 주문의 실행, 추적, 리스크 관리를 담당한다.
"""

import json
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from api.kis_api import KISApi
from config.settings import Settings
from utils.logger import setup_logger

logger = setup_logger("oshms.trading.order")


@dataclass
class Position:
    """보유 포지션."""
    stock_code: str
    stock_name: str
    quantity: int
    avg_price: int
    buy_time: str
    buy_reason: str
    current_price: int = 0
    highest_price: int = 0  # 매수 후 최고가 (트레일링 스탑용)
    signal_strength: float = 0.0  # 매수 시 신호 강도
    atr_at_buy: float = 0.0  # 매수 시 ATR (동적 리스크 관리용)

    @property
    def profit_rate(self) -> float:
        """수익률(%)."""
        if self.avg_price == 0:
            return 0.0
        return ((self.current_price - self.avg_price) / self.avg_price) * 100

    @property
    def profit_loss(self) -> int:
        """평가 손익(원)."""
        return (self.current_price - self.avg_price) * self.quantity


@dataclass
class TradeRecord:
    """거래 기록."""
    stock_code: str
    stock_name: str
    side: str  # "BUY" or "SELL"
    quantity: int
    price: int
    amount: int
    reason: str
    timestamp: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    profit_loss: int = 0
    profit_rate: float = 0.0


class OrderManager:
    """주문 관리자: 포지션 추적, 리스크 관리, 주문 실행."""

    TRADE_LOG_PATH = Path("logs/trades.json")

    def __init__(self, api: KISApi, settings: Settings):
        self.api = api
        self.settings = settings
        self.positions: dict[str, Position] = {}
        self.trade_history: list[TradeRecord] = []
        self._load_trade_history()

    def _load_trade_history(self) -> None:
        """이전 거래 기록을 로드한다."""
        if self.TRADE_LOG_PATH.exists():
            try:
                data = json.loads(self.TRADE_LOG_PATH.read_text(encoding="utf-8"))
                self.trade_history = [TradeRecord(**r) for r in data]
                logger.info("거래 기록 %d건 로드", len(self.trade_history))
            except (json.JSONDecodeError, TypeError):
                self.trade_history = []

    def _save_trade_history(self) -> None:
        """거래 기록을 저장한다."""
        self.TRADE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = [asdict(r) for r in self.trade_history]
        self.TRADE_LOG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def sync_positions(self) -> None:
        """API에서 실제 잔고를 동기화한다."""
        balance = self.api.get_balance()
        synced = {}
        for h in balance.get("holdings", []):
            code = h["stock_code"]
            existing = self.positions.get(code)
            synced[code] = Position(
                stock_code=code,
                stock_name=h["stock_name"],
                quantity=h["quantity"],
                avg_price=h["avg_price"],
                buy_time=existing.buy_time if existing else datetime.now().strftime("%H:%M:%S"),
                buy_reason=existing.buy_reason if existing else "기존보유",
                current_price=h["current_price"],
                highest_price=max(
                    h["current_price"],
                    existing.highest_price if existing else 0,
                ),
                signal_strength=existing.signal_strength if existing else 0.0,
                atr_at_buy=existing.atr_at_buy if existing else 0.0,
            )
        self.positions = synced
        logger.info("포지션 동기화 완료: %d종목", len(self.positions))

    def update_prices(self) -> None:
        """보유 종목의 현재가를 갱신한다."""
        for code, pos in self.positions.items():
            try:
                price_data = self.api.get_current_price(code)
                if price_data:
                    pos.current_price = price_data["price"]
                    pos.highest_price = max(pos.highest_price, pos.current_price)
            except Exception as e:
                logger.warning("[%s] 가격 갱신 실패: %s", code, e)

    def can_buy(self) -> bool:
        """매수 가능 여부를 확인한다."""
        return len(self.positions) < self.settings.max_hold_count

    def calc_buy_quantity(self, price: int, strength: float = 1.0) -> int:
        """매수 수량을 계산한다.

        신호 강도에 따라 투자 금액을 조절한다.
        - 강한 신호 (>0.7): 최대 금액의 100%
        - 보통 신호 (0.4~0.7): 최대 금액의 70%
        - 약한 신호 (<0.4): 최대 금액의 50%
        """
        if price <= 0:
            return 0
        # 신호 강도에 따른 투자 비율 (50% ~ 100%)
        invest_ratio = min(1.0, max(0.5, 0.3 + strength))
        effective_amount = int(self.settings.max_buy_amount * invest_ratio)
        return effective_amount // price

    def execute_buy(
        self, stock_code: str, stock_name: str, price: int, reason: str,
        strength: float = 0.5, atr: float = 0.0,
    ) -> bool:
        """매수를 실행한다."""
        if not self.can_buy():
            logger.warning("최대 보유 종목 수 초과 (%d종목)", self.settings.max_hold_count)
            return False

        if stock_code in self.positions:
            logger.warning("[%s] 이미 보유 중인 종목", stock_code)
            return False

        quantity = self.calc_buy_quantity(price, strength)
        if quantity <= 0:
            logger.warning("[%s] 매수 수량 0: 가격=%d, 최대금액=%d", stock_code, price, self.settings.max_buy_amount)
            return False

        result = self.api.buy_market_order(stock_code, quantity)
        if not result["success"]:
            return False

        # 포지션 등록
        self.positions[stock_code] = Position(
            stock_code=stock_code,
            stock_name=stock_name,
            quantity=quantity,
            avg_price=price,
            buy_time=datetime.now().strftime("%H:%M:%S"),
            buy_reason=reason,
            current_price=price,
            highest_price=price,
            signal_strength=strength,
            atr_at_buy=atr,
        )

        # 거래 기록
        record = TradeRecord(
            stock_code=stock_code,
            stock_name=stock_name,
            side="BUY",
            quantity=quantity,
            price=price,
            amount=price * quantity,
            reason=reason,
        )
        self.trade_history.append(record)
        self._save_trade_history()

        logger.info(
            "★ 매수 완료: %s(%s) %d주 × %s원 = %s원 | 사유: %s",
            stock_name, stock_code, quantity,
            f"{price:,}", f"{price * quantity:,}", reason,
        )
        return True

    def execute_sell(self, stock_code: str, reason: str) -> bool:
        """매도를 실행한다."""
        pos = self.positions.get(stock_code)
        if not pos:
            logger.warning("[%s] 보유하지 않은 종목 매도 시도", stock_code)
            return False

        result = self.api.sell_market_order(stock_code, pos.quantity)
        if not result["success"]:
            return False

        # 수익 계산
        profit_loss = pos.profit_loss
        profit_rate = pos.profit_rate

        # 거래 기록
        record = TradeRecord(
            stock_code=stock_code,
            stock_name=pos.stock_name,
            side="SELL",
            quantity=pos.quantity,
            price=pos.current_price,
            amount=pos.current_price * pos.quantity,
            reason=reason,
            profit_loss=profit_loss,
            profit_rate=profit_rate,
        )
        self.trade_history.append(record)
        self._save_trade_history()

        logger.info(
            "★ 매도 완료: %s(%s) %d주 × %s원 | 손익: %s원 (%.2f%%) | 사유: %s",
            pos.stock_name, stock_code, pos.quantity,
            f"{pos.current_price:,}", f"{profit_loss:,}", profit_rate, reason,
        )

        del self.positions[stock_code]
        return True

    def check_stop_loss(self) -> list[str]:
        """손절 조건을 확인하여 매도 대상 종목을 반환한다.

        ATR 기반 동적 손절: ATR이 있으면 ATR의 2배를 손절선으로 사용.
        없으면 설정의 고정 손절률 사용.
        """
        targets = []
        for code, pos in self.positions.items():
            # ATR 기반 동적 손절
            if pos.atr_at_buy > 0 and pos.avg_price > 0:
                dynamic_stop_pct = -(pos.atr_at_buy * 2 / pos.avg_price * 100)
                # 최소 -1.5%, 최대 설정값
                stop_pct = max(self.settings.stop_loss_pct, min(-1.5, dynamic_stop_pct))
            else:
                stop_pct = self.settings.stop_loss_pct

            if pos.profit_rate <= stop_pct:
                targets.append(code)
                logger.warning(
                    "⚠ 손절 대상: %s(%s) 수익률=%.2f%% (기준: %.1f%%)",
                    pos.stock_name, code, pos.profit_rate, stop_pct,
                )
        return targets

    def check_take_profit(self) -> list[str]:
        """익절 조건을 확인하여 매도 대상 종목을 반환한다."""
        targets = []
        for code, pos in self.positions.items():
            if pos.profit_rate >= self.settings.take_profit_pct:
                targets.append(code)
                logger.info(
                    "✓ 익절 대상: %s(%s) 수익률=%.2f%% (기준: %.1f%%)",
                    pos.stock_name, code, pos.profit_rate, self.settings.take_profit_pct,
                )
        return targets

    def check_trailing_stop(self, trail_pct: float = 1.5) -> list[str]:
        """트레일링 스탑 조건을 확인한다.

        수익률 구간별 차등 적용:
        - 수익 1~3%: 최고가 대비 1.5% 하락 시 매도 (수익 보호)
        - 수익 3~5%: 최고가 대비 2.0% 하락 시 매도 (약간 여유)
        - 수익 5%+:  최고가 대비 2.5% 하락 시 매도 (큰 수익 보호하되 여유)
        """
        targets = []
        for code, pos in self.positions.items():
            if pos.highest_price <= 0:
                continue
            if pos.profit_rate <= 0:
                continue  # 수익 중인 종목만

            # 수익률 구간별 트레일링 퍼센트 조정
            if pos.profit_rate >= 5.0:
                effective_trail = 2.5
            elif pos.profit_rate >= 3.0:
                effective_trail = 2.0
            else:
                effective_trail = trail_pct  # 기본 1.5%

            drop_from_high = ((pos.highest_price - pos.current_price) / pos.highest_price) * 100
            if drop_from_high >= effective_trail:
                targets.append(code)
                logger.info(
                    "트레일링스탑: %s(%s) 최고가=%s 현재=%s 하락=%.1f%% (기준=%.1f%%)",
                    pos.stock_name, code,
                    f"{pos.highest_price:,}", f"{pos.current_price:,}",
                    drop_from_high, effective_trail,
                )
        return targets
