"""v4.9: 통합 확신도(Conviction) 점수 시스템.

현재 시스템 문제점 (수익률 변동성 주범):
  - ExpertStrategy: `strength = confidence` (0~1, agreement_ratio 기반)
  - MomentumStrategy: `strength = 0.5 + volume_ratio/10`
  - ScalpingStrategy: 또 다른 로직
  - _scan_momentum_breakouts: `0.3 + (vr-3)*0.05`
  → 진입 경로마다 `strength` 의미가 달라 포지션 크기/필터가 일관되지 않음.

해결: 모든 진입 결정을 단일 0~100 ConvictionScore 에 정규화한다.
  - 70 미만 = 진입 금지 (신호 약함)
  - 70~84 = 반 포지션 (size_mult 0.5)
  - 85~94 = 표준 포지션 (size_mult 1.0)
  - 95+ = 최대 포지션 (size_mult 1.2, 강한 확신)

계산 인자:
  1. 종합 기술 점수 (expert.total_score): -1~1 → 0~40점
  2. 추세 정렬 (trend_score × volume_confirmation): 0~20점
  3. 체결강도 (contract_strength): 100~200 → 0~15점
  4. 거래량 비율 (volume_ratio): 1.0~3.0 → 0~15점
  5. 세션 적합도 (session_fit_bonus): 0~10점
  6. 신뢰도 (expert.confidence): 0~1 → 0~10점

합계 = 0~110점 (상한 100은 합의 자연 상한이 아니므로 튜닝 여지 있음)
"""

from dataclasses import dataclass


# 진입 임계값 (v4.9.1: 현실적 시장 데이터 기준으로 재조정)
# 이전 70/85/95는 모든 지표가 동시 최고일 때만 진입 가능 → 거래 0건
MIN_CONVICTION_TO_ENTER = 40.0  # 보통 신호(39점)도 진입 허용
HALF_SIZE_THRESHOLD = 55.0      # 좋은 신호부터 표준 포지션
FULL_SIZE_THRESHOLD = 70.0      # 강한 신호만 최대 포지션


@dataclass
class ConvictionScore:
    """통합 확신도 점수 (0~100)."""
    total: float = 0.0

    # 세부 기여 (디버깅 + 학습용)
    expert_score_pts: float = 0.0
    trend_alignment_pts: float = 0.0
    contract_strength_pts: float = 0.0
    volume_pts: float = 0.0
    session_fit_pts: float = 0.0
    confidence_pts: float = 0.0

    # 인풋 스냅샷 (로그용)
    inputs: dict = None

    def size_mult(self) -> float:
        """확신도에 따른 포지션 크기 배수."""
        if self.total < MIN_CONVICTION_TO_ENTER:
            return 0.0
        if self.total < HALF_SIZE_THRESHOLD:
            return 0.5
        if self.total < FULL_SIZE_THRESHOLD:
            return 1.0
        return 1.2

    def tier(self) -> str:
        if self.total < MIN_CONVICTION_TO_ENTER:
            return "REJECT"
        if self.total < HALF_SIZE_THRESHOLD:
            return "HALF"
        if self.total < FULL_SIZE_THRESHOLD:
            return "FULL"
        return "MAX"

    def should_enter(self, session_min: float) -> bool:
        """진입 가능 여부 판단. 세션별 최소 확신도 + 글로벌 최소값 모두 충족해야."""
        threshold = max(MIN_CONVICTION_TO_ENTER, session_min)
        return self.total >= threshold

    def describe(self) -> str:
        return (
            f"확신도={self.total:.1f}({self.tier()}) "
            f"[전략={self.expert_score_pts:.0f} "
            f"추세={self.trend_alignment_pts:.0f} "
            f"체결강도={self.contract_strength_pts:.0f} "
            f"거래량={self.volume_pts:.0f} "
            f"세션={self.session_fit_pts:.0f} "
            f"신뢰도={self.confidence_pts:.0f}]"
        )


def calc_conviction(
    expert_total_score: float = 0.0,   # -1 ~ +1
    expert_confidence: float = 0.0,    # 0 ~ 1
    trend_score: float = 0.0,          # -1 ~ +1
    volume_ratio: float = 1.0,         # 평균 대비
    contract_strength: float = 0.0,    # 0~200+ (KIS 체결강도)
    session_fit: float = 0.5,          # 0~1 (세션 적합도)
    macd_cross: str = "",              # "golden"/"dead"/""
) -> ConvictionScore:
    """통합 확신도 점수를 계산한다.

    매수 신호(점수 > 0)에 대해서만 유효한 양수를 반환한다.
    매도/중립 신호는 0점이 된다.
    """
    score = ConvictionScore()
    score.inputs = {
        "expert_total_score": round(expert_total_score, 3),
        "expert_confidence": round(expert_confidence, 3),
        "trend_score": round(trend_score, 3),
        "volume_ratio": round(volume_ratio, 2),
        "contract_strength": round(contract_strength, 1),
        "session_fit": round(session_fit, 2),
        "macd_cross": macd_cross,
    }

    # 매도/중립 신호는 확신도 0 (매수 전용 점수)
    if expert_total_score <= 0:
        return score

    # 1. 전문가 종합 점수: 0.2 → 0점, 0.4 → 30점, 0.47+ → 40점(상한)
    # 매수 임계값(0.2) 미만이면 0점 유지. 배수 150: 기술 확신은 실제 수익과 가장
    # 높은 상관을 보이는 요인이므로 가장 큰 가중치를 받는다.
    if expert_total_score >= 0.2:
        score.expert_score_pts = min(40.0, (expert_total_score - 0.2) * 150.0)

    # 2. 추세 정렬 × 거래량 확인 (최대 20점)
    # 강한 추세(+) + 거래량 확인(>1.2)이 있을 때만 full points
    trend_pts = max(0.0, trend_score) * 20.0  # 0~20
    vol_confirm = min(1.0, max(0.0, (volume_ratio - 1.0) / 1.0))  # 1.0=0, 2.0=1.0
    score.trend_alignment_pts = trend_pts * (0.5 + 0.5 * vol_confirm)
    # MACD 골든크로스 보너스: 추세 신호 강화
    if macd_cross == "golden":
        score.trend_alignment_pts = min(20.0, score.trend_alignment_pts + 3.0)
    elif macd_cross == "dead":
        score.trend_alignment_pts *= 0.3  # 데드크로스면 추세 점수 대폭 감소

    # 3. 체결강도: 120 → 5점, 150 → 12점, 180+ → 15점 (상한)
    # 100 미만은 0 (매도 우위)
    if contract_strength >= 100:
        score.contract_strength_pts = min(15.0, (contract_strength - 100) * 0.2)

    # 4. 거래량: 1.2 → 3점, 2.0 → 10점, 3.0+ → 15점 (상한)
    if volume_ratio >= 1.2:
        score.volume_pts = min(15.0, (volume_ratio - 1.0) * 7.5)

    # 5. 세션 적합도 (0~1 → 0~10점)
    score.session_fit_pts = max(0.0, min(10.0, session_fit * 10.0))

    # 6. 전문가 신뢰도 (0~1 → 0~10점)
    score.confidence_pts = max(0.0, min(10.0, expert_confidence * 10.0))

    score.total = (
        score.expert_score_pts
        + score.trend_alignment_pts
        + score.contract_strength_pts
        + score.volume_pts
        + score.session_fit_pts
        + score.confidence_pts
    )
    return score


def session_fit_score(session_name: str, volume_ratio: float, trend_score: float) -> float:
    """세션별 진입 적합도 (0~1).

    각 세션에 가장 잘 맞는 신호 유형을 채점한다:
      - morning: 거래량 강세 + 상승 추세 선호 (강한 돌파형)
      - afternoon: 상승 추세 필수, 거래량 덜 중요 (피로감 우려)
      - opening/lunch/pre_close: 애초에 진입 금지 세션 → 0
    """
    s = (session_name or "").lower()
    if s == "morning":
        vol = min(1.0, max(0.0, (volume_ratio - 1.0) / 1.5))
        tr = min(1.0, max(0.0, trend_score / 0.5))
        return vol * 0.5 + tr * 0.5
    if s == "afternoon":
        # 오후는 추세 생존이 핵심, 거래량 가중 축소
        tr = min(1.0, max(0.0, trend_score / 0.4))
        return tr * 0.8 + min(0.2, max(0.0, (volume_ratio - 1.0) * 0.1))
    return 0.0
