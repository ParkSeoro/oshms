"""v4.9: 한국 시장 세션별 매매 프로필.

한국 주식 시장의 시간대별 특성이 완전히 다르다는 점을 반영한다.
기존 시스템은 09:05 ~ 15:10 전체를 균일하게 다루지만, 실제로는:

- 오프닝(09:00~09:30): 갭/뉴스 소화, 변동성 최대 — 추격 금지, 관망
- 오전장(09:30~11:30): 일중 방향 결정 — 강한 추세/거래량 종목만 진입
- 점심(11:30~13:00): 유동성 고갈 — 신규 진입 금지
- 오후장(13:00~14:00): 2차 모멘텀 — 오전 강세 종목 재진입 가능
- 마감전(14:00~15:10): 청산/숏커버 — 신규 진입 금지, 약세 정리
- 동시호가(15:10~): 매매 중단

각 세션마다 `SessionProfile`이 다음을 결정한다:
  - allow_new_entry: 신규 매수 가능 여부
  - min_conviction: 해당 세션에서 진입하려면 필요한 최소 확신도
  - size_mult: 포지션 크기 배수 (오전장 1.0, 오후 0.7, 마감전 0)
  - max_new_entries: 해당 세션 내 신규 진입 최대 횟수
  - force_defensive_sell: 손실 포지션 강제 청산 여부
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class TradingSession(Enum):
    """한국 시장 5단계 세션."""
    PRE_OPEN = "pre_open"        # 09:00 이전
    OPENING = "opening"          # 09:00 ~ 09:30 (갭 소화)
    MORNING = "morning"          # 09:30 ~ 11:30 (주 트레이딩 윈도우)
    LUNCH = "lunch"              # 11:30 ~ 13:00 (유동성 고갈)
    AFTERNOON = "afternoon"      # 13:00 ~ 14:00 (2차 모멘텀)
    PRE_CLOSE = "pre_close"      # 14:00 ~ 15:10 (청산 우선)
    CLOSED = "closed"            # 15:10 이후


@dataclass(frozen=True)
class SessionProfile:
    """세션별 매매 프로필."""
    session: TradingSession
    allow_new_entry: bool      # 신규 매수 가능
    min_conviction: float      # 진입에 필요한 최소 확신도 (0~100)
    size_mult: float           # 포지션 크기 배수 (0~1.2)
    max_new_entries: int       # 이 세션 안에서 허용되는 최대 신규 진입 수
    force_defensive_sell: bool # 손실 포지션 강제 매도 허용
    reason: str                # 로그용 설명

    def describe(self) -> str:
        if not self.allow_new_entry:
            return f"[{self.session.value}] 신규 매수 금지 — {self.reason}"
        return (
            f"[{self.session.value}] 진입 OK | 최소확신={self.min_conviction:.0f} "
            f"| 크기={self.size_mult:.1f}x | 최대진입={self.max_new_entries}건 | {self.reason}"
        )


# ── 세션 프로필 정의 ─────────────────────────────────────────────────────────
# 수익률 안정화를 위한 핵심 설계:
# - 오프닝: 진입 금지 (갭 메우기/리버설 빈번, 확률 낮음)
# - 오전장: 주 거래 윈도우 (최대 4건, 확신도 70+)
# - 점심: 진입 금지 (유동성 고갈 → 체결 불리)
# - 오후장: 제한적 진입 (최대 2건, 확신도 75+ — 더 엄격)
# - 마감전: 진입 금지 + 약세 청산
SESSION_PROFILES: dict[TradingSession, SessionProfile] = {
    TradingSession.PRE_OPEN: SessionProfile(
        session=TradingSession.PRE_OPEN,
        allow_new_entry=False, min_conviction=999, size_mult=0.0,
        max_new_entries=0, force_defensive_sell=False,
        reason="장 시작 전",
    ),
    TradingSession.OPENING: SessionProfile(
        session=TradingSession.OPENING,
        allow_new_entry=False, min_conviction=999, size_mult=0.0,
        max_new_entries=0, force_defensive_sell=False,
        reason="갭 소화 시간 — 진입 관망",
    ),
    TradingSession.MORNING: SessionProfile(
        session=TradingSession.MORNING,
        allow_new_entry=True, min_conviction=70.0, size_mult=1.0,
        max_new_entries=4, force_defensive_sell=False,
        reason="주 거래 윈도우",
    ),
    TradingSession.LUNCH: SessionProfile(
        session=TradingSession.LUNCH,
        allow_new_entry=False, min_conviction=999, size_mult=0.0,
        max_new_entries=0, force_defensive_sell=False,
        reason="유동성 고갈 — 체결 불리",
    ),
    TradingSession.AFTERNOON: SessionProfile(
        session=TradingSession.AFTERNOON,
        allow_new_entry=True, min_conviction=75.0, size_mult=0.7,
        max_new_entries=2, force_defensive_sell=False,
        reason="2차 모멘텀 (엄격 기준)",
    ),
    TradingSession.PRE_CLOSE: SessionProfile(
        session=TradingSession.PRE_CLOSE,
        allow_new_entry=False, min_conviction=999, size_mult=0.0,
        max_new_entries=0, force_defensive_sell=True,
        reason="마감전 — 청산 우선",
    ),
    TradingSession.CLOSED: SessionProfile(
        session=TradingSession.CLOSED,
        allow_new_entry=False, min_conviction=999, size_mult=0.0,
        max_new_entries=0, force_defensive_sell=False,
        reason="장 마감",
    ),
}


def get_session(now: datetime | None = None) -> TradingSession:
    """현재 시각으로 세션을 판단한다."""
    now = now or datetime.now()
    hhmm = now.strftime("%H:%M")
    if hhmm < "09:00":
        return TradingSession.PRE_OPEN
    if hhmm < "09:30":
        return TradingSession.OPENING
    if hhmm < "11:30":
        return TradingSession.MORNING
    if hhmm < "13:00":
        return TradingSession.LUNCH
    if hhmm < "14:00":
        return TradingSession.AFTERNOON
    if hhmm < "15:10":
        return TradingSession.PRE_CLOSE
    return TradingSession.CLOSED


def get_profile(now: datetime | None = None) -> SessionProfile:
    """현재 세션의 매매 프로필을 반환한다."""
    return SESSION_PROFILES[get_session(now)]
