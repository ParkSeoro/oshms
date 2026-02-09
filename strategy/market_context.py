"""시장 컨텍스트 분석 모듈.

시장 전반의 상태를 분석하여 개별 종목 매매 판단에 반영한다.
- 시장 레짐(추세/횡보/급변) 감지
- 코스피/코스닥 지수 흐름
- 업종별 자금 흐름
- 시간대별 매매 적합도
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from api.kis_api import KISApi
from utils.logger import setup_logger

logger = setup_logger("oshms.strategy.market")


@dataclass
class MarketContext:
    """시장 컨텍스트 스냅샷."""
    # 시장 지수
    kospi: float = 0
    kospi_change: float = 0
    kosdaq: float = 0
    kosdaq_change: float = 0

    # 시장 레짐
    regime: str = "unknown"  # "trending_up", "trending_down", "ranging", "volatile"
    regime_confidence: float = 0.0

    # 시간대 분석
    time_zone: str = ""  # "opening", "morning", "lunch", "afternoon", "closing"
    time_suitability: float = 0.5  # 0 ~ 1.0 (매매 적합도)

    # 외국인/기관 수급
    foreign_net: str = ""  # "buy", "sell", "neutral"
    institution_net: str = ""

    # 종합 점수
    market_score: float = 0.0  # -1.0(약세) ~ +1.0(강세)
    trading_ok: bool = True  # 매매 허용 여부


class MarketContextAnalyzer:
    """시장 컨텍스트 분석기."""

    # 시간대별 단타 적합도 (경험 기반)
    TIME_SUITABILITY = {
        "pre_market": 0.0,       # 장 전
        "opening": 0.9,          # 09:00-09:30 (변동성 큼, 기회 많음)
        "morning_early": 0.8,    # 09:30-10:30
        "morning_late": 0.6,     # 10:30-11:30
        "lunch": 0.3,            # 11:30-13:00 (거래량 급감)
        "afternoon_early": 0.5,  # 13:00-14:00
        "afternoon_late": 0.7,   # 14:00-14:50
        "closing": 0.8,          # 14:50-15:20 (마감 동시호가)
        "post_market": 0.0,      # 장 후
    }

    def __init__(self, api: KISApi):
        self.api = api

    def analyze(self) -> MarketContext:
        """현재 시장 컨텍스트를 분석한다."""
        ctx = MarketContext()

        # 시간대 분석
        ctx.time_zone = self._get_time_zone()
        ctx.time_suitability = self.TIME_SUITABILITY.get(ctx.time_zone, 0.3)
        ctx.trading_ok = ctx.time_suitability > 0.1

        # 시장 지수 조회
        self._fetch_market_index(ctx)

        # 시장 레짐 판단
        self._detect_regime(ctx)

        # 종합 점수
        ctx.market_score = self._calc_market_score(ctx)

        logger.info(
            "시장 컨텍스트: KOSPI=%+.2f%% KOSDAQ=%+.2f%% 레짐=%s 시간대=%s(적합도=%.1f)",
            ctx.kospi_change, ctx.kosdaq_change, ctx.regime,
            ctx.time_zone, ctx.time_suitability,
        )
        return ctx

    def _get_time_zone(self) -> str:
        """현재 시간대를 판단한다."""
        now = datetime.now()
        h, m = now.hour, now.minute
        t = h * 60 + m

        if t < 540:     # 09:00 전
            return "pre_market"
        elif t < 570:   # 09:00-09:30
            return "opening"
        elif t < 630:   # 09:30-10:30
            return "morning_early"
        elif t < 690:   # 10:30-11:30
            return "morning_late"
        elif t < 780:   # 11:30-13:00
            return "lunch"
        elif t < 840:   # 13:00-14:00
            return "afternoon_early"
        elif t < 890:   # 14:00-14:50
            return "afternoon_late"
        elif t < 920:   # 14:50-15:20
            return "closing"
        else:
            return "post_market"

    def _fetch_market_index(self, ctx: MarketContext) -> None:
        """코스피/코스닥 지수를 조회한다."""
        try:
            # 코스피 지수 (종목코드: 0001)
            kospi_data = self.api.get_current_price("0001")
            if kospi_data:
                ctx.kospi = kospi_data.get("price", 0)
                ctx.kospi_change = kospi_data.get("change_rate", 0)

            # 코스닥 지수 (종목코드: 1001)
            kosdaq_data = self.api.get_current_price("1001")
            if kosdaq_data:
                ctx.kosdaq = kosdaq_data.get("price", 0)
                ctx.kosdaq_change = kosdaq_data.get("change_rate", 0)
        except Exception as e:
            logger.warning("시장 지수 조회 실패: %s", e)

    def _detect_regime(self, ctx: MarketContext) -> None:
        """시장 레짐을 감지한다."""
        # 코스피/코스닥 변동률 기반 판단
        avg_change = (abs(ctx.kospi_change) + abs(ctx.kosdaq_change)) / 2
        direction = ctx.kospi_change + ctx.kosdaq_change

        if avg_change > 2.0:
            ctx.regime = "volatile"
            ctx.regime_confidence = min(1.0, avg_change / 3.0)
        elif direction > 1.0:
            ctx.regime = "trending_up"
            ctx.regime_confidence = min(1.0, direction / 3.0)
        elif direction < -1.0:
            ctx.regime = "trending_down"
            ctx.regime_confidence = min(1.0, abs(direction) / 3.0)
        else:
            ctx.regime = "ranging"
            ctx.regime_confidence = 0.5

    def _calc_market_score(self, ctx: MarketContext) -> float:
        """시장 종합 점수를 계산한다."""
        score = 0.0

        # 지수 방향
        if ctx.kospi_change > 0:
            score += min(0.3, ctx.kospi_change / 3)
        else:
            score += max(-0.3, ctx.kospi_change / 3)

        if ctx.kosdaq_change > 0:
            score += min(0.2, ctx.kosdaq_change / 3)
        else:
            score += max(-0.2, ctx.kosdaq_change / 3)

        # 레짐 보정
        if ctx.regime == "volatile":
            score *= 0.7  # 급변 시 보수적
        elif ctx.regime == "trending_up":
            score += 0.2
        elif ctx.regime == "trending_down":
            score -= 0.2

        # 시간대 보정
        if ctx.time_zone == "lunch":
            score *= 0.5  # 점심 시간 보수적

        return max(-1.0, min(1.0, score))

    def get_regime_strategy_adjustment(self, ctx: MarketContext) -> dict[str, float]:
        """레짐에 따른 전략 파라미터 조정값을 반환한다."""
        adjustments = {
            "buy_threshold_adj": 0.0,   # 매수 임계값 조정
            "sell_threshold_adj": 0.0,  # 매도 임계값 조정
            "position_size_mult": 1.0,  # 포지션 크기 배수
            "stop_loss_adj": 0.0,       # 손절 조정 (%)
        }

        if ctx.regime == "trending_up":
            # 상승장: 매수 적극적, 매도 보수적
            adjustments["buy_threshold_adj"] = -0.05
            adjustments["position_size_mult"] = 1.2
            adjustments["stop_loss_adj"] = -0.5  # 손절 여유

        elif ctx.regime == "trending_down":
            # 하락장: 매수 보수적, 매도 적극적
            adjustments["buy_threshold_adj"] = 0.15
            adjustments["sell_threshold_adj"] = -0.1
            adjustments["position_size_mult"] = 0.6
            adjustments["stop_loss_adj"] = 0.5  # 손절 타이트

        elif ctx.regime == "volatile":
            # 급변장: 전반적 보수적
            adjustments["buy_threshold_adj"] = 0.1
            adjustments["position_size_mult"] = 0.5
            adjustments["stop_loss_adj"] = 1.0  # 넓은 손절

        elif ctx.regime == "ranging":
            # 횡보장: 스캘핑 유리
            adjustments["buy_threshold_adj"] = -0.05
            adjustments["sell_threshold_adj"] = -0.05

        # 시간대 보정
        if ctx.time_suitability < 0.4:
            adjustments["buy_threshold_adj"] += 0.1
            adjustments["position_size_mult"] *= 0.7

        return adjustments
