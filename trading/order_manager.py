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

    # v4.7: 매수 논지 스냅샷 (Thesis-based Trading)
    # 매수 시점의 기술적 근거를 저장하여, 나중에 "왜 샀는지"의 근거가
    # 여전히 유효한지 확인한다. 근거가 깨지면 매도, 유효하면 홀드.
    buy_rsi: float = 50.0
    buy_trend_score: float = 0.0      # -1.0 ~ +1.0
    buy_momentum_score: float = 0.0   # -1.0 ~ +1.0
    buy_volume_ratio: float = 1.0     # 평균 대비 배수
    buy_macd_cross: str = ""          # "golden" | "dead" | ""
    buy_regime: str = ""              # 매수 시 시장 레짐

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

    def __init__(self, api: KISApi, settings: Settings, market: str = "KR"):
        self.api = api
        self.settings = settings
        self.market = market  # v4.2: 활성 시장
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
        """API에서 실제 잔고를 동기화한다.

        v4.2: 시장별 API 분기 — 해외 시장은 overseas balance API 사용.
        """
        if self.market != "KR":
            balance = self.api.get_overseas_balance(self.market)
        else:
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
        """보유 종목의 현재가를 갱신한다.

        v4.2: 시장별 API 분기 — 해외 시장은 overseas API 사용.
        """
        for code, pos in self.positions.items():
            try:
                if self.market != "KR":
                    price_data = self.api.get_overseas_price(self.market, code)
                else:
                    price_data = self.api.get_current_price(code)
                if price_data:
                    price = price_data["price"]
                    if isinstance(price, float):
                        price = int(price) if price == int(price) else price
                    pos.current_price = price
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

    # v4.3: 리스크/리워드 재조정 — 정상 변동폭 고려
    MIN_SELL_PROFIT_PCT = 0.4    # v4.4: 0.5→0.4% (더 빠른 수익 확정)
    MIN_SELL_PROFIT_KRW = 200    # v4.4: 300→200원 (소액도 확정)
    MIN_HOLD_SECONDS = 90        # v4.4: 2분→1.5분 (빠른 수익 확정)
    QUICK_STOP_LOSS_PCT = -2.0   # v4.4: 빠른 손절로 큰 손실 방지

    def calc_buy_quantity(self, price: int, strength: float = 1.0,
                          per: float = 0, pbr: float = 0,
                          size_mult: float = 1.0) -> int:
        """매수 수량을 계산한다 (v4.9: 확신도 기반 사이징).

        v4.9 설계:
        - size_mult가 1.0보다 작으면 "확신도가 낮아 의도적으로 줄인 것"
          → 하한(min_invest) 무시하고 그대로 반영 (변동성 주범이었음)
        - size_mult >= 1.0 이면 기존 로직 유지 (강한 신호에 집중)

        Args:
            strength: 통합 확신도 0~1 (v4.9: ConvictionScore.total/100)
            size_mult: 확신도 + 세션 + 방어 모드가 합쳐진 최종 배수 (0.25~1.3)
        """
        if price <= 0:
            return 0

        # v4.9: size_mult를 직접 곱하는 구조로 단순화
        # 강도 변조(±20%)는 이미 conviction 안에 반영되어 있으므로 중복 제거
        effective_mult = max(0.25, min(1.3, size_mult))
        effective_amount = int(self.settings.max_buy_amount * effective_mult)
        quantity = effective_amount // price

        # 하한 보정은 size_mult가 충분히 크고(>=1.0) 가격이 비싸 1주도 못 살 때만
        # (의도적 다운사이징을 깨뜨리지 않도록)
        if quantity == 0 and effective_mult >= 1.0 and price <= self.settings.max_buy_amount:
            quantity = 1

        return quantity

    def execute_buy(
        self, stock_code: str, stock_name: str, price: int, reason: str,
        strength: float = 0.5, atr: float = 0.0,
        target_price: int = 0, estimated_upside: float = 0.0,
        per: float = 0, pbr: float = 0,
        size_mult: float = 1.0,
        thesis: dict | None = None,
    ) -> bool:
        """매수를 실행한다.

        v4.7: thesis 인자 추가 — 매수 근거 스냅샷 저장.
            thesis = {rsi, trend_score, momentum_score, volume_ratio,
                      macd_cross, regime}
        """
        if not self.can_buy():
            logger.warning("최대 보유 종목 수 초과 (%d종목)", self.settings.max_hold_count)
            return False

        if stock_code in self.positions:
            logger.warning("[%s] 이미 보유 중인 종목", stock_code)
            return False

        # v4.8: 종목명이 비어있거나 코드와 동일하면 API에서 보충
        if not stock_name or stock_name == stock_code:
            try:
                stock_name = self.api.get_stock_name(stock_code)
            except Exception:
                stock_name = stock_code

        quantity = self.calc_buy_quantity(price, strength, per=per, pbr=pbr, size_mult=size_mult)
        if quantity <= 0:
            logger.warning("[%s] 매수 수량 0: 가격=%d, 최대금액=%d", stock_code, price, self.settings.max_buy_amount)
            return False

        # v4.2: 시장별 주문 API 분기
        if self.market != "KR":
            result = self.api.buy_overseas_market_order(self.market, stock_code, quantity, price)
        else:
            result = self.api.buy_market_order(stock_code, quantity)
        if not result["success"]:
            return False

        # 포지션 등록 (v4.7: 논지 스냅샷 저장)
        thesis = thesis or {}
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
            buy_rsi=float(thesis.get("rsi", 50.0)),
            buy_trend_score=float(thesis.get("trend_score", 0.0)),
            buy_momentum_score=float(thesis.get("momentum_score", 0.0)),
            buy_volume_ratio=float(thesis.get("volume_ratio", 1.0)),
            buy_macd_cross=str(thesis.get("macd_cross", "")),
            buy_regime=str(thesis.get("regime", "")),
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

        # v4.2: 시장별 주문 API 분기
        if self.market != "KR":
            result = self.api.sell_overseas_market_order(self.market, stock_code, pos.quantity, pos.current_price)
        else:
            result = self.api.sell_market_order(stock_code, pos.quantity)
        if not result["success"]:
            return False

        # 수익 계산
        profit_loss = pos.profit_loss
        profit_rate = pos.profit_rate

        # v4.8: 종목명 빈 문자열 방지
        sell_name = pos.stock_name
        if not sell_name or sell_name == stock_code:
            try:
                sell_name = self.api.get_stock_name(stock_code)
            except Exception:
                sell_name = stock_code

        # 거래 기록
        record = TradeRecord(
            stock_code=stock_code,
            stock_name=sell_name,
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

    def check_stop_loss(self, stop_loss_pct: float | None = None) -> list[str]:
        """손절 조건을 확인하여 매도 대상 종목을 반환한다.

        v4.7: 하드 손절은 극단 낙폭 차단용 최종 안전망 역할.
        - 논지 기반 매도(check_thesis_broken)가 1차 방어선
        - 하드 손절은 -5.0% ~ -1.5% 범위 내에서 자유롭게 설정 가능
        - None이면 settings.stop_loss_pct 참조, 그것도 없으면 -4.0%
        """
        targets = []
        ABSOLUTE_EMERGENCY = -5.0  # v4.7: 절대 상한 — 플래시 크래시 방어선
        MIN_TIGHTNESS = -1.5       # 가장 타이트한 손절 (너무 민감)

        # 진화/설정에서 주입된 손절 기준 적용
        if stop_loss_pct is None:
            stop_loss_pct = getattr(self.settings, "stop_loss_pct", -4.0)
        base_stop = max(ABSOLUTE_EMERGENCY, min(MIN_TIGHTNESS, stop_loss_pct))

        for code, pos in self.positions.items():
            # ATR 기반 동적 손절
            if pos.atr_at_buy > 0 and pos.avg_price > 0:
                dynamic_stop_pct = -(pos.atr_at_buy * 2.5 / pos.avg_price * 100)
                stop_pct = max(base_stop, min(MIN_TIGHTNESS, dynamic_stop_pct))
            else:
                stop_pct = base_stop

            if pos.profit_rate <= stop_pct:
                targets.append(code)
                logger.warning(
                    "⚠ 손절 대상: %s(%s) 수익률=%.2f%% (기준: %.1f%%)",
                    pos.stock_name, code, pos.profit_rate, stop_pct,
                )
        return targets

    def check_take_profit(self, take_profit_pct: float | None = None) -> list[str]:
        """익절 조건을 확인하여 매도 대상 종목을 반환한다.

        v4.6: 진화된 settings.take_profit_pct를 실제로 반영.
        - take_profit_pct가 주어지면 그 값을 사용 (안전 범위 1.0~5.0%)
        - None이면 settings.take_profit_pct를 참조, 그것도 없으면 1.2%
        """
        targets = []

        # v4.6: 진화/설정에서 주입된 익절 기준 적용
        if take_profit_pct is None:
            take_profit_pct = getattr(self.settings, "take_profit_pct", 1.2)
        base_take = max(1.0, min(5.0, take_profit_pct))  # 안전 범위

        for code, pos in self.positions.items():
            # ATR 기반 동적 익절
            if pos.atr_at_buy > 0 and pos.avg_price > 0:
                dynamic_take_pct = pos.atr_at_buy * 1.5 / pos.avg_price * 100
                # 최소 base_take, 최대 5% (빠른 익절)
                take_pct = max(base_take, min(5.0, dynamic_take_pct))
            else:
                take_pct = base_take

            if pos.profit_rate >= take_pct:
                targets.append(code)
                logger.info(
                    "✓ 익절 대상: %s(%s) 수익률=%.2f%% (기준: %.1f%%)",
                    pos.stock_name, code, pos.profit_rate, take_pct,
                )
        return targets

    def check_trailing_stop(self, trail_pct: float = 0.5) -> list[str]:
        """트레일링 스탑 조건을 확인한다.

        v4.4: 수익 보호 최우선 — 이익 발생 즉시 보호.
        - 수익 0.3~0.8%: 최고가 대비 0.5% 하락 시 매도 (작은 수익도 사수)
        - 수익 0.8~2%:   최고가 대비 0.7% 하락 시 매도
        - 수익 2%+:      최고가 대비 1.0% 하락 시 매도
        """
        targets = []
        for code, pos in self.positions.items():
            if pos.highest_price <= 0:
                continue
            if pos.profit_rate <= 0.3:
                continue

            # v4.4: 더 타이트한 트레일링 (수익 사수 최우선)
            if pos.profit_rate >= 2.0:
                effective_trail = 1.0   # v4.4: 1.5→1.0
            elif pos.profit_rate >= 0.8:
                effective_trail = 0.7   # v4.4: 1.0→0.7
            else:
                effective_trail = trail_pct  # v4.4: 0.8→0.5

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

    def check_thesis_broken(self, snapshots: dict[str, dict]) -> list[tuple[str, str]]:
        """v4.7 논지 기반 매도: 매수 근거가 깨진 종목을 찾는다.

        "오를 것이라 판단해서 샀는데, 그 판단 근거가 여전히 유효한가?"를 확인한다.
        근거가 깨졌으면 손실이 작아도 매도(잘못된 판단 빠른 정정).
        근거가 유효하면 일시 하락을 버틴다(whipsaw 방지).

        근거 깨짐 조건 (OR):
          1. 추세 역전: 매수 시 추세점수 > 0 → 현재 추세점수 < -0.3
          2. 모멘텀 소진: 매수 시 모멘텀 > 0 → 현재 < -0.2
          3. RSI 과매수 이탈: 매수 후 RSI 70 넘고 다시 50 밑으로 떨어짐 (정점 이탈)
          4. 거래량 급감: 매수 시 1.5배 이상 → 현재 0.5배 이하 (관심 사라짐)
          5. MACD 데드크로스: 매수 시 골든크로스 → 현재 데드크로스

        Args:
            snapshots: {stock_code: {rsi, trend_score, momentum_score,
                        volume_ratio, macd_cross}}

        Returns:
            [(stock_code, broken_reason), ...] — 논지 깨진 종목 리스트
        """
        broken = []
        for code, pos in self.positions.items():
            snap = snapshots.get(code)
            if not snap:
                continue

            reasons = []

            # 1. 추세 역전
            cur_trend = snap.get("trend_score", 0)
            if pos.buy_trend_score > 0.1 and cur_trend < -0.3:
                reasons.append(f"추세역전({pos.buy_trend_score:.2f}→{cur_trend:.2f})")

            # 2. 모멘텀 소진
            cur_mom = snap.get("momentum_score", 0)
            if pos.buy_momentum_score > 0.1 and cur_mom < -0.2:
                reasons.append(f"모멘텀소진({pos.buy_momentum_score:.2f}→{cur_mom:.2f})")

            # 3. RSI 정점 이탈 (매수 후 과매수였다가 50 밑으로)
            cur_rsi = snap.get("rsi", 50)
            # 매수 시 RSI가 건강한 구간(40~65)이었는데 지금 35 밑 → 하락 전환
            if 40 <= pos.buy_rsi <= 65 and cur_rsi < 35:
                reasons.append(f"RSI하락전환({pos.buy_rsi:.0f}→{cur_rsi:.0f})")

            # 4. 거래량 급감
            cur_vol = snap.get("volume_ratio", 1.0)
            if pos.buy_volume_ratio >= 1.5 and cur_vol <= 0.5:
                reasons.append(f"관심이탈({pos.buy_volume_ratio:.1f}x→{cur_vol:.1f}x)")

            # 5. MACD 반전
            cur_macd = snap.get("macd_cross", "")
            if pos.buy_macd_cross == "golden" and cur_macd == "dead":
                reasons.append("MACD데드크로스")

            if reasons:
                broken.append((code, " / ".join(reasons)))
                logger.info(
                    "📉 논지 깨짐: %s(%s) 수익률=%.2f%% | %s",
                    pos.stock_name, code, pos.profit_rate, " / ".join(reasons),
                )
        return broken
