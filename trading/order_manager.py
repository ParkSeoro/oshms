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
    target_price: int = 0  # 전략 분석 기반 목표가
    estimated_upside: float = 0.0  # 예상 상승여력 (%)

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

    def is_sell_worthy(self, stock_code: str) -> tuple[bool, str]:
        """매도할 만한 조건인지 확인한다.

        v4.0 소액 빈번 거래 전략:
        - 수익 나면 즉시 확정 (0.3% 이상)
        - 손실도 빠르게 손절 (-1.5% 이하)
        - 최소 보유 3분 (급등매수 직후 되팔기 방지)

        Returns:
            (매도 가능 여부, 사유)
        """
        pos = self.positions.get(stock_code)
        if not pos:
            return False, "포지션 없음"

        profit_pct = pos.profit_rate
        profit_krw = pos.profit_loss

        # 최소 보유 시간 확인 (3분 — 시세 안정화)
        try:
            buy_time = datetime.strptime(pos.buy_time, "%H:%M:%S")
            now_time = datetime.now().replace(
                hour=buy_time.hour, minute=buy_time.minute, second=buy_time.second)
            elapsed = (datetime.now() - now_time).total_seconds()
            if 0 < elapsed < self.MIN_HOLD_SECONDS:
                return False, f"최소 보유시간 미달 ({elapsed:.0f}초/{self.MIN_HOLD_SECONDS}초)"
        except (ValueError, TypeError):
            pass

        # v4.0: 손실 종목도 손절선 이하면 빠르게 정리
        if profit_pct <= self.QUICK_STOP_LOSS_PCT:
            return True, f"빠른 손절 ({profit_pct:.2f}%)"

        # 수익 중: 아주 작은 수익이라도 확정
        if profit_pct >= self.MIN_SELL_PROFIT_PCT and profit_krw >= self.MIN_SELL_PROFIT_KRW:
            return True, "매도 가능"

        if profit_pct >= self.MIN_SELL_PROFIT_PCT:
            return False, f"수익금 부족 ({profit_krw:,}원 < {self.MIN_SELL_PROFIT_KRW:,}원)"

        return False, f"수익률 부족 ({profit_pct:.2f}% < {self.MIN_SELL_PROFIT_PCT}%)"

    # v4.0: 소액 빈번 거래 전략 — 적은 수익이라도 자주 확정
    MIN_SELL_PROFIT_PCT = 0.3    # 최소 0.3% 이상 수익이면 매도 (v4.0: 0.5%→0.3%)
    MIN_SELL_PROFIT_KRW = 500    # 최소 500원 이상 수익 (v4.0: 1000→500원)
    MIN_HOLD_SECONDS = 180       # 최소 3분 보유 후 매도 (v4.0: 10분→3분 빠른 회전)
    QUICK_STOP_LOSS_PCT = -1.5   # 빠른 손절: -1.5% 이하면 즉시 매도

    def calc_buy_quantity(self, price: int, strength: float = 1.0,
                          per: float = 0, pbr: float = 0,
                          size_mult: float = 1.0) -> int:
        """매수 수량을 계산한다 (v4.0: 균등 분산 투자).

        소액 빈번 거래 전략: 종목당 균등 금액으로 분산 투자.
        - 최대 보유 종목 수로 균등 분배
        - 신호 강도에 따라 소폭 조정 (±20%)

        Args:
            size_mult: 포트폴리오 최적화 승수 (0.5~1.3, 기본 1.0)
        """
        if price <= 0:
            return 0

        # v4.0: 균등 분배 기반 (집중 투자 → 분산 투자)
        # max_buy_amount를 기준으로, 신호 강도에 따라 ±20% 조정
        strength_adj = 0.8 + strength * 0.4  # 0.8 ~ 1.2
        invest_ratio = min(1.0, strength_adj)

        # 포트폴리오 최적화 승수 적용
        invest_ratio *= max(0.5, min(1.3, size_mult))

        effective_amount = int(self.settings.max_buy_amount * min(1.0, invest_ratio))
        quantity = effective_amount // price

        # 최소 수량 보장: 0.3% 수익이면 최소 500원 이상 → 최소 약 170,000원 투자
        min_invest = 100_000
        if quantity * price < min_invest and price > 0:
            min_qty = min_invest // price
            if min_qty > 0 and min_qty * price <= self.settings.max_buy_amount:
                quantity = min_qty

        return quantity

    def execute_buy(
        self, stock_code: str, stock_name: str, price: int, reason: str,
        strength: float = 0.5, atr: float = 0.0,
        target_price: int = 0, estimated_upside: float = 0.0,
        per: float = 0, pbr: float = 0,
        size_mult: float = 1.0,
    ) -> bool:
        """매수를 실행한다."""
        if not self.can_buy():
            logger.warning("최대 보유 종목 수 초과 (%d종목)", self.settings.max_hold_count)
            return False

        if stock_code in self.positions:
            logger.warning("[%s] 이미 보유 중인 종목", stock_code)
            return False

        quantity = self.calc_buy_quantity(price, strength, per=per, pbr=pbr, size_mult=size_mult)
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
            target_price=target_price,
            estimated_upside=estimated_upside,
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
        """익절 조건을 확인하여 매도 대상 종목을 반환한다.

        v4.0: 소액 빈번 거래 — 3% 이상이면 확정 익절.
        ATR 기반 동적 익절은 최소 2%에서 적용.
        """
        targets = []
        for code, pos in self.positions.items():
            # ATR 기반 동적 익절 (소액 전략에 맞게 하향)
            if pos.atr_at_buy > 0 and pos.avg_price > 0:
                dynamic_take_pct = pos.atr_at_buy * 3 / pos.avg_price * 100
                # 최소 2%, 최대 8%
                take_pct = max(2.0, min(8.0, dynamic_take_pct))
            else:
                take_pct = 3.0  # 기본 3% (v4.0: 20% → 3% 빠른 익절)

            if pos.profit_rate >= take_pct:
                targets.append(code)
                logger.info(
                    "✓ 익절 대상: %s(%s) 수익률=%.2f%% (기준: %.1f%%)",
                    pos.stock_name, code, pos.profit_rate, take_pct,
                )
        return targets

    def check_trailing_stop(self, trail_pct: float = 1.5) -> list[str]:
        """트레일링 스탑 조건을 확인한다.

        v4.0 소액 빈번 거래: 타이트한 트레일링으로 수익 보호
        - 수익 0.3~2%:  최고가 대비 1.5% 하락 시 매도 (소액 수익 보호)
        - 수익 2~5%:    최고가 대비 2% 하락 시 매도
        - 수익 5%+:     최고가 대비 3% 하락 시 매도
        """
        targets = []
        for code, pos in self.positions.items():
            if pos.highest_price <= 0:
                continue
            if pos.profit_rate <= 0:
                continue  # 수익 중인 종목만

            # v4.0: 타이트한 트레일링 (소액 수익 보호)
            if pos.profit_rate >= 5.0:
                effective_trail = 3.0
            elif pos.profit_rate >= 2.0:
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
