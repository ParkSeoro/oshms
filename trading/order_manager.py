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
        """매도할 만한 수익이 나는지 확인한다 (최소 수익 임계값).

        워렌 버핏 원칙: "잔챙이 수익에 팔지 마라. 큰 물고기를 기다려라."

        Returns:
            (매도 가능 여부, 사유)
        """
        pos = self.positions.get(stock_code)
        if not pos:
            return False, "포지션 없음"

        profit_pct = pos.profit_rate
        profit_krw = pos.profit_loss

        # v3.2: 마이너스 수익률에서는 절대 매도하지 않는다
        # 매수한 이유가 있으므로 반등을 기다린다
        if profit_pct <= 0:
            return False, f"마이너스 수익률 ({profit_pct:.2f}%) — 반등 대기"

        # 최소 보유 시간 확인 (30분)
        try:
            buy_time = datetime.strptime(pos.buy_time, "%H:%M:%S")
            now_time = datetime.now().replace(
                hour=buy_time.hour, minute=buy_time.minute, second=buy_time.second)
            elapsed = (datetime.now() - now_time).total_seconds()
            if 0 < elapsed < self.MIN_HOLD_SECONDS and profit_pct < 5.0:
                return False, f"최소 보유시간 미달 ({elapsed:.0f}초/{self.MIN_HOLD_SECONDS}초)"
        except (ValueError, TypeError):
            pass

        # 수익 중일 때: 최소 수익 임계값 확인
        if profit_pct < self.MIN_SELL_PROFIT_PCT:
            return False, f"수익률 부족 ({profit_pct:.2f}% < {self.MIN_SELL_PROFIT_PCT}%)"
        if profit_krw < self.MIN_SELL_PROFIT_KRW:
            return False, f"수익금 부족 ({profit_krw:,}원 < {self.MIN_SELL_PROFIT_KRW:,}원)"

        return True, "매도 가능"

    # 매도 시 최소 수익 기준 (워렌 버핏: 너무 작은 수익에 매도하지 않는다)
    MIN_SELL_PROFIT_PCT = 2.0    # 최소 2% 이상 수익일 때만 매도
    MIN_SELL_PROFIT_KRW = 3000   # 최소 3,000원 이상 수익일 때만 매도
    MIN_HOLD_SECONDS = 1800      # 최소 30분 보유 후 매도 (급등/급락 제외)

    def calc_buy_quantity(self, price: int, strength: float = 1.0,
                          per: float = 0, pbr: float = 0,
                          size_mult: float = 1.0) -> int:
        """매수 수량을 계산한다 (버핏식 집중투자).

        신호 강도 + 가치 평가에 따라 투자 금액을 조절한다.
        - 강한 신호 + 저평가(PER<12, PBR<1.5): 최대 금액의 100%
        - 강한 신호 (>0.7): 최대 금액의 90%
        - 보통 신호 (0.4~0.7): 최대 금액의 70%
        - 약한 신호 (<0.4): 최대 금액의 55%

        워렌 버핏 원칙: "확신이 있을 때 크게 베팅하라."

        Args:
            size_mult: 포트폴리오 최적화 승수 (0.5~1.3, 기본 1.0)
        """
        if price <= 0:
            return 0

        # 1. 기본 투자 비율 (신호 강도 기반: 55% ~ 95%)
        base_ratio = min(0.95, max(0.55, 0.3 + strength * 0.65))

        # 2. 가치 투자 보너스 (PER/PBR 저평가 시 비율 상향)
        value_bonus = 0.0
        if 0 < per < 12:
            value_bonus += 0.03  # 저PER 보너스
        if 0 < pbr < 1.5:
            value_bonus += 0.02  # 저PBR 보너스

        invest_ratio = min(1.0, base_ratio + value_bonus)

        # 3. 포트폴리오 최적화 승수 적용
        invest_ratio *= max(0.5, min(1.3, size_mult))

        effective_amount = int(self.settings.max_buy_amount * min(1.0, invest_ratio))
        quantity = effective_amount // price

        # 3. 최소 수량 보장: 수익이 의미 있으려면 최소 금액 이상 투자
        #    수익률 3%일 때 최소 3,000원 이상 수익이 나도록 → 최소 100,000원 투자
        min_invest = 100_000
        if quantity * price < min_invest and price > 0:
            min_qty = min_invest // price
            if min_qty > 0 and min_qty * price <= self.settings.max_buy_amount:
                quantity = min_qty
                logger.info(
                    "최소 투자금액 보장: %d주 → %d주 (%s원)",
                    effective_amount // price, quantity, f"{quantity * price:,}",
                )

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

        v2.7: 익절 기준 대폭 상향 — 목표가 기반 매도가 주 매도 메커니즘이므로
        여기서는 극단적 과열 상황(20%+)에서만 강제 익절한다.
        ATR 기반 동적 익절은 최소 15%에서 적용.
        """
        targets = []
        for code, pos in self.positions.items():
            # ATR 기반 동적 익절 (상향 조정)
            if pos.atr_at_buy > 0 and pos.avg_price > 0:
                dynamic_take_pct = pos.atr_at_buy * 8 / pos.avg_price * 100
                # 최소 15%, 최대 30%
                take_pct = max(15.0, min(30.0, dynamic_take_pct))
            else:
                take_pct = 20.0  # 기본 20% (기존 5% → 상향)

            if pos.profit_rate >= take_pct:
                targets.append(code)
                logger.info(
                    "✓ 익절 대상: %s(%s) 수익률=%.2f%% (기준: %.1f%%)",
                    pos.stock_name, code, pos.profit_rate, take_pct,
                )
        return targets

    def check_trailing_stop(self, trail_pct: float = 3.0) -> list[str]:
        """트레일링 스탑 조건을 확인한다.

        수익률 구간별 차등 적용 (v2.7: 더 여유롭게 — 큰 수익 추구):
        - 수익 1~5%:   최고가 대비 3% 하락 시 매도
        - 수익 5~10%:  최고가 대비 5% 하락 시 매도
        - 수익 10~20%: 최고가 대비 7% 하락 시 매도
        - 수익 20%+:   최고가 대비 10% 하락 시 매도 (대형 수익 극대화)
        """
        targets = []
        for code, pos in self.positions.items():
            if pos.highest_price <= 0:
                continue
            if pos.profit_rate <= 0:
                continue  # 수익 중인 종목만

            # 수익률 구간별 트레일링 퍼센트 조정 (여유롭게)
            if pos.profit_rate >= 20.0:
                effective_trail = 10.0
            elif pos.profit_rate >= 10.0:
                effective_trail = 7.0
            elif pos.profit_rate >= 5.0:
                effective_trail = 5.0
            else:
                effective_trail = trail_pct  # 기본 3.0%

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
