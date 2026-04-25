"""24시간 멀티마켓 자동 스케줄러.

시장별 거래 시간에 맞춰 자동으로 매매를 시작/종료하고,
해외 시장으로 자동 로테이션한다.

시장 거래 시간 (KST 기준):
  KR   (한국)   09:00 ~ 15:30
  TKSE (일본)   09:00 ~ 15:00
  SEHK (홍콩)   10:30 ~ 17:00
  NASD (나스닥) 23:30 ~ 06:00 (+1일)
  NYSE (뉴욕)   23:30 ~ 06:00 (+1일)
  AMEX (아멕스)  23:30 ~ 06:00 (+1일)
"""

import threading
import time
from datetime import datetime
from dataclasses import dataclass, field

from utils.logger import setup_logger

logger = setup_logger("oshms.trading.scheduler")


@dataclass
class MarketSession:
    """시장 세션 정보."""
    code: str
    name: str
    open_time: str       # HH:MM (KST)
    close_time: str      # HH:MM (KST)
    pre_open: str        # 사전 준비 시간 (시장 개장 5분 후 시작)
    liquidate_time: str  # 청산 시간 (시장 마감 10분 전)
    crosses_midnight: bool = False  # 자정을 넘기는지
    is_overseas: bool = False


# 시장 정의 (KST 기준)
MARKETS: dict[str, MarketSession] = {
    "KR": MarketSession(
        code="KR", name="한국 (KOSPI/KOSDAQ)",
        open_time="09:00", close_time="15:30",
        pre_open="09:05", liquidate_time="15:20",
    ),
    "TKSE": MarketSession(
        code="TKSE", name="일본 (도쿄)",
        open_time="09:00", close_time="15:00",
        pre_open="09:05", liquidate_time="14:50",
        is_overseas=True,
    ),
    "SEHK": MarketSession(
        code="SEHK", name="홍콩 (SEHK)",
        open_time="10:30", close_time="17:00",
        pre_open="10:35", liquidate_time="16:50",
        is_overseas=True,
    ),
    "NASD": MarketSession(
        code="NASD", name="미국 (NASDAQ)",
        open_time="23:30", close_time="06:00",
        pre_open="23:35", liquidate_time="05:50",
        crosses_midnight=True,
        is_overseas=True,
    ),
    "NYSE": MarketSession(
        code="NYSE", name="미국 (NYSE)",
        open_time="23:30", close_time="06:00",
        pre_open="23:35", liquidate_time="05:50",
        crosses_midnight=True,
        is_overseas=True,
    ),
    "AMEX": MarketSession(
        code="AMEX", name="미국 (AMEX)",
        open_time="23:30", close_time="06:00",
        pre_open="23:35", liquidate_time="05:50",
        crosses_midnight=True,
        is_overseas=True,
    ),
}


def is_market_open(market: MarketSession, now_str: str | None = None) -> bool:
    """현재 시각(KST)에 해당 시장이 열려있는지 확인한다."""
    if now_str is None:
        now_str = datetime.now().strftime("%H:%M")

    if market.crosses_midnight:
        # 23:30 ~ 06:00 (자정 걸침)
        return now_str >= market.open_time or now_str < market.close_time
    else:
        return market.open_time <= now_str < market.close_time


def is_trading_time(market: MarketSession, now_str: str | None = None) -> bool:
    """매매 가능 시간인지 확인 (개장 5분 후 ~ 마감 10분 전)."""
    if now_str is None:
        now_str = datetime.now().strftime("%H:%M")

    if market.crosses_midnight:
        return now_str >= market.pre_open or now_str < market.liquidate_time
    else:
        return market.pre_open <= now_str < market.liquidate_time


def is_liquidation_time(market: MarketSession, now_str: str | None = None) -> bool:
    """청산 시간인지 확인 (마감 10분 전 ~ 마감)."""
    if now_str is None:
        now_str = datetime.now().strftime("%H:%M")

    if market.crosses_midnight:
        return market.liquidate_time <= now_str < market.close_time
    else:
        return market.liquidate_time <= now_str < market.close_time


def get_open_markets(now_str: str | None = None) -> list[MarketSession]:
    """현재 열린 시장 목록을 반환한다."""
    return [m for m in MARKETS.values() if is_market_open(m, now_str)]


def get_next_market_event(now_str: str | None = None) -> dict:
    """다음 시장 이벤트(개장/폐장)까지 남은 시간을 반환한다."""
    if now_str is None:
        now_str = datetime.now().strftime("%H:%M")

    events = []
    for m in MARKETS.values():
        if is_market_open(m, now_str):
            events.append({
                "market": m.code,
                "event": "close",
                "time": m.close_time,
                "name": m.name,
            })
        else:
            events.append({
                "market": m.code,
                "event": "open",
                "time": m.open_time,
                "name": m.name,
            })
    return events


class MarketScheduler:
    """24시간 멀티마켓 자동 스케줄러.

    활성화된 시장을 자동으로 감지하고, 매매를 시작/종료한다.
    한국 시장이 마감되면 자동으로 해외 시장으로 전환한다.
    """

    def __init__(self, enabled_markets: list[str] | None = None):
        self.enabled_markets = enabled_markets or ["KR", "NASD"]
        self._running = False
        self._thread: threading.Thread | None = None
        self._active_market: str | None = None
        self._callbacks: dict[str, callable] = {}
        self._status: dict = {
            "running": False,
            "active_market": None,
            "enabled_markets": self.enabled_markets,
            "last_check": "",
            "next_event": "",
            "market_status": {},
        }
        self._lock = threading.Lock()

    def set_callback(self, event: str, callback: callable):
        """이벤트 콜백을 등록한다.

        Events:
            market_open: 시장 개장 시 (market_code)
            market_close: 시장 마감 시 (market_code)
            start_trading: 매매 시작 시 (market_code)
            stop_trading: 매매 종료 시 (market_code)
            liquidate: 청산 시 (market_code)
        """
        self._callbacks[event] = callback

    def start(self):
        """스케줄러를 시작한다."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="MarketScheduler")
        self._thread.start()
        logger.info("24시간 멀티마켓 스케줄러 시작 (시장: %s)", ", ".join(self.enabled_markets))

    def stop(self):
        """스케줄러를 종료한다."""
        self._running = False
        if self._active_market:
            self._fire("stop_trading", self._active_market)
            self._active_market = None
        with self._lock:
            self._status["running"] = False
        logger.info("멀티마켓 스케줄러 종료")

    def get_status(self) -> dict:
        """현재 스케줄러 상태를 반환한다."""
        with self._lock:
            # 실시간 시장 상태 갱신
            now_str = datetime.now().strftime("%H:%M")
            market_status = {}
            for code in self.enabled_markets:
                m = MARKETS.get(code)
                if not m:
                    continue
                open_now = is_market_open(m, now_str)
                trading_now = is_trading_time(m, now_str)
                liq = is_liquidation_time(m, now_str)
                market_status[code] = {
                    "name": m.name,
                    "open": open_now,
                    "trading": trading_now,
                    "liquidating": liq,
                    "open_time": m.open_time,
                    "close_time": m.close_time,
                    "active": code == self._active_market,
                    "is_overseas": m.is_overseas,
                }
            status = dict(self._status)
            status["market_status"] = market_status
            status["active_market"] = self._active_market
            status["last_check"] = now_str
            return status

    def update_enabled_markets(self, markets: list[str]):
        """활성 시장 목록을 업데이트한다."""
        self.enabled_markets = [m for m in markets if m in MARKETS]
        with self._lock:
            self._status["enabled_markets"] = self.enabled_markets
        logger.info("활성 시장 업데이트: %s", ", ".join(self.enabled_markets))

    def _fire(self, event: str, *args):
        """콜백을 실행한다."""
        cb = self._callbacks.get(event)
        if cb:
            try:
                cb(*args)
            except Exception as e:
                logger.error("스케줄러 콜백 오류 (%s): %s", event, e)

    def _run_loop(self):
        """메인 스케줄러 루프 — 30초마다 시장 상태를 확인한다."""
        with self._lock:
            self._status["running"] = True

        while self._running:
            try:
                self._check_markets()
            except Exception as e:
                logger.error("스케줄러 루프 오류: %s", e)

            time.sleep(30)  # 30초마다 체크

    def _check_markets(self):
        """시장 상태를 확인하고 필요한 액션을 실행한다."""
        now_str = datetime.now().strftime("%H:%M")

        # 현재 활성 시장의 상태 확인
        if self._active_market:
            active = MARKETS.get(self._active_market)
            if active:
                # 청산 시간이면 포지션 청산
                if is_liquidation_time(active, now_str):
                    logger.info("[%s] 청산 시간 → 포지션 청산", self._active_market)
                    self._fire("liquidate", self._active_market)

                # 시장 마감이면 매매 종료
                if not is_market_open(active, now_str):
                    logger.info("[%s] 시장 마감 → 매매 종료", self._active_market)
                    self._fire("stop_trading", self._active_market)
                    self._fire("market_close", self._active_market)
                    self._active_market = None

        # 활성 시장이 없으면 새로 찾기
        if not self._active_market:
            for code in self.enabled_markets:
                m = MARKETS.get(code)
                if not m:
                    continue
                if is_trading_time(m, now_str):
                    logger.info("[%s] 시장 개장 감지 → 매매 시작", code)
                    self._active_market = code
                    self._fire("market_open", code)
                    self._fire("start_trading", code)
                    break

        with self._lock:
            self._status["active_market"] = self._active_market
            self._status["last_check"] = now_str
