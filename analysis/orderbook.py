"""호가창 분석 모듈.

호가(매수/매도 대기 주문) 데이터를 분석하여 매매 신호를 생성한다.
대형 물량벽 감지, 매수/매도 압력 비율, 스프레드 분석 등을 통해
기관/세력의 의도를 파악한다.
"""

from dataclasses import dataclass, field
from typing import Any

from utils.logger import setup_logger

logger = setup_logger("oshms.analysis.orderbook")


@dataclass
class OrderBookSignal:
    """호가창 분석 결과 시그널."""

    buy_pressure: float = 0.0       # 0~1, 매수 잔량 비율
    sell_pressure: float = 0.0      # 0~1, 매도 잔량 비율
    imbalance: float = 0.0          # -1~1, 양수=매수 우세
    wall_detected: str = "none"     # "buy_wall", "sell_wall", "none"
    wall_price: int = 0             # 벽이 감지된 가격
    wall_volume: int = 0            # 벽의 물량
    spread_pct: float = 0.0         # 호가 스프레드 비율 (%)
    signal_strength: float = 0.0    # 0~1, 시그널 강도
    recommendation: str = "NEUTRAL" # STRONG_BUY, BUY, NEUTRAL, SELL, STRONG_SELL


class OrderBookAnalyzer:
    """호가창 데이터를 분석하여 매매 시그널을 생성한다.

    KISApi.get_orderbook()이 반환하는 dict를 입력으로 받아
    매수/매도 압력, 물량벽, 스프레드 등을 종합 분석한다.
    """

    # 물량벽 판단 배수 (평균 대비 N배 이상이면 벽으로 간주)
    WALL_MULTIPLIER = 3.0

    # 스프레드 기준 (%)
    TIGHT_SPREAD = 0.1   # 이하면 매우 유동적
    WIDE_SPREAD = 0.5    # 이상이면 비유동적

    # 시그널 강도 가중치
    WEIGHT_IMBALANCE = 0.40
    WEIGHT_WALL = 0.30
    WEIGHT_SPREAD = 0.15
    WEIGHT_VOLUME = 0.15

    def analyze(self, orderbook_data: dict[str, Any]) -> OrderBookSignal:
        """호가 데이터를 종합 분석하여 시그널을 반환한다.

        Args:
            orderbook_data: KISApi.get_orderbook()의 반환값
                - asks: list[dict] 매도호가 (price, volume)
                - bids: list[dict] 매수호가 (price, volume)
                - total_ask_volume: int 총 매도 잔량
                - total_bid_volume: int 총 매수 잔량
                - expected_price: int 예상 체결가

        Returns:
            OrderBookSignal 분석 결과
        """
        if not orderbook_data:
            logger.warning("호가 데이터가 비어 있음")
            return OrderBookSignal()

        asks = orderbook_data.get("asks", [])
        bids = orderbook_data.get("bids", [])
        total_ask = orderbook_data.get("total_ask_volume", 0)
        total_bid = orderbook_data.get("total_bid_volume", 0)

        if not asks and not bids:
            logger.warning("매수/매도 호가가 모두 비어 있음")
            return OrderBookSignal()

        # 1. 매수/매도 압력 비율
        buy_pressure, sell_pressure, imbalance = self.calc_pressure_ratio(
            total_bid, total_ask
        )

        # 2. 물량벽 감지
        wall_detected, wall_price, wall_volume = self.detect_walls(asks, bids)

        # 3. 스프레드 분석
        spread_pct = self.analyze_spread(asks, bids)

        # 4. 종합 시그널 (기관 의도 판단)
        signal_strength, recommendation = self.get_institutional_signal(
            imbalance, wall_detected, wall_volume, spread_pct,
            total_bid, total_ask, asks, bids,
        )

        signal = OrderBookSignal(
            buy_pressure=buy_pressure,
            sell_pressure=sell_pressure,
            imbalance=imbalance,
            wall_detected=wall_detected,
            wall_price=wall_price,
            wall_volume=wall_volume,
            spread_pct=spread_pct,
            signal_strength=signal_strength,
            recommendation=recommendation,
        )

        logger.debug(
            "호가 분석 완료: imbalance=%.2f, wall=%s, spread=%.3f%%, "
            "strength=%.2f, rec=%s",
            imbalance, wall_detected, spread_pct,
            signal_strength, recommendation,
        )
        return signal

    # ──────────────────────────────────────────────
    # 개별 분석 함수
    # ──────────────────────────────────────────────

    def calc_pressure_ratio(
        self, total_bid: int, total_ask: int
    ) -> tuple[float, float, float]:
        """매수/매도 압력 비율을 계산한다.

        Args:
            total_bid: 총 매수 잔량
            total_ask: 총 매도 잔량

        Returns:
            (buy_pressure, sell_pressure, imbalance)
            - buy_pressure: 0~1 (매수 잔량 / 전체)
            - sell_pressure: 0~1 (매도 잔량 / 전체)
            - imbalance: -1~1 (양수=매수 우세, 음수=매도 우세)
        """
        total = total_bid + total_ask
        if total == 0:
            return 0.0, 0.0, 0.0

        buy_pressure = total_bid / total
        sell_pressure = total_ask / total

        # imbalance = (bid - ask) / (bid + ask), 범위 -1 ~ 1
        imbalance = (total_bid - total_ask) / total

        return round(buy_pressure, 4), round(sell_pressure, 4), round(imbalance, 4)

    def detect_walls(
        self,
        asks: list[dict[str, int]],
        bids: list[dict[str, int]],
    ) -> tuple[str, int, int]:
        """대형 물량벽을 감지한다.

        평균 호가 잔량 대비 WALL_MULTIPLIER배 이상인 가격대를 찾는다.
        기관/세력이 특정 가격대에 대량 주문을 걸어둔 것으로 해석한다.

        Args:
            asks: 매도호가 리스트 [{"price": int, "volume": int}, ...]
            bids: 매수호가 리스트 [{"price": int, "volume": int}, ...]

        Returns:
            (wall_type, wall_price, wall_volume)
            - wall_type: "buy_wall", "sell_wall", "none"
            - wall_price: 벽이 감지된 가격 (없으면 0)
            - wall_volume: 벽의 물량 (없으면 0)
        """
        all_volumes = []
        for entry in asks:
            vol = entry.get("volume", 0)
            if vol > 0:
                all_volumes.append(vol)
        for entry in bids:
            vol = entry.get("volume", 0)
            if vol > 0:
                all_volumes.append(vol)

        if not all_volumes:
            return "none", 0, 0

        avg_volume = sum(all_volumes) / len(all_volumes)
        threshold = avg_volume * self.WALL_MULTIPLIER

        # 매도벽 탐색 (낮은 가격부터 = 현재가에 가까운 매도호가)
        sell_wall_price = 0
        sell_wall_vol = 0
        for entry in asks:
            vol = entry.get("volume", 0)
            if vol >= threshold and vol > sell_wall_vol:
                sell_wall_price = entry.get("price", 0)
                sell_wall_vol = vol

        # 매수벽 탐색 (높은 가격부터 = 현재가에 가까운 매수호가)
        buy_wall_price = 0
        buy_wall_vol = 0
        for entry in bids:
            vol = entry.get("volume", 0)
            if vol >= threshold and vol > buy_wall_vol:
                buy_wall_price = entry.get("price", 0)
                buy_wall_vol = vol

        # 더 큰 벽을 선택
        if sell_wall_vol > 0 and sell_wall_vol >= buy_wall_vol:
            logger.debug(
                "매도벽 감지: 가격=%d, 물량=%d (평균의 %.1f배)",
                sell_wall_price, sell_wall_vol, sell_wall_vol / avg_volume,
            )
            return "sell_wall", sell_wall_price, sell_wall_vol

        if buy_wall_vol > 0:
            logger.debug(
                "매수벽 감지: 가격=%d, 물량=%d (평균의 %.1f배)",
                buy_wall_price, buy_wall_vol, buy_wall_vol / avg_volume,
            )
            return "buy_wall", buy_wall_price, buy_wall_vol

        return "none", 0, 0

    def analyze_spread(
        self,
        asks: list[dict[str, int]],
        bids: list[dict[str, int]],
    ) -> float:
        """호가 스프레드를 분석한다.

        최우선 매도호가와 최우선 매수호가의 차이를 비율(%)로 반환.
        - 좁은 스프레드: 유동성 풍부, 체결 용이
        - 넓은 스프레드: 유동성 부족, 슬리피지 위험

        Args:
            asks: 매도호가 리스트 (가격 오름차순)
            bids: 매수호가 리스트 (가격 내림차순)

        Returns:
            스프레드 비율 (%). 계산 불가 시 0.0
        """
        if not asks or not bids:
            return 0.0

        # 최우선 매도호가 = 가장 낮은 매도 가격
        best_ask = min(entry.get("price", 0) for entry in asks)
        # 최우선 매수호가 = 가장 높은 매수 가격
        best_bid = max(entry.get("price", 0) for entry in bids)

        if best_bid <= 0 or best_ask <= 0:
            return 0.0

        mid_price = (best_ask + best_bid) / 2
        spread_pct = ((best_ask - best_bid) / mid_price) * 100

        return round(spread_pct, 4)

    def get_institutional_signal(
        self,
        imbalance: float,
        wall_detected: str,
        wall_volume: int,
        spread_pct: float,
        total_bid: int,
        total_ask: int,
        asks: list[dict[str, int]],
        bids: list[dict[str, int]],
    ) -> tuple[float, str]:
        """모든 요소를 종합하여 기관/세력 의도를 판단한다.

        Args:
            imbalance: 매수/매도 불균형 (-1~1)
            wall_detected: 감지된 벽 종류
            wall_volume: 벽 물량
            spread_pct: 스프레드 비율 (%)
            total_bid: 총 매수 잔량
            total_ask: 총 매도 잔량
            asks: 매도호가 리스트
            bids: 매수호가 리스트

        Returns:
            (signal_strength, recommendation)
            - signal_strength: 0~1
            - recommendation: STRONG_BUY / BUY / NEUTRAL / SELL / STRONG_SELL
        """
        # ── 1. 불균형 점수 (imbalance -> 0~1) ──
        # imbalance가 +1이면 1.0, -1이면 0.0
        imbalance_score = (imbalance + 1) / 2  # 범위 0~1

        # ── 2. 물량벽 점수 ──
        wall_score = 0.5  # 기본 중립
        if wall_detected == "buy_wall":
            # 매수벽 = 하방 지지 = 매수에 유리
            wall_score = 0.8
        elif wall_detected == "sell_wall":
            # 매도벽 = 상방 저항 = 매수에 불리
            wall_score = 0.2

        # 벽의 크기에 따라 점수 강화
        all_volumes = [e.get("volume", 0) for e in asks + bids if e.get("volume", 0) > 0]
        if all_volumes and wall_volume > 0:
            avg_vol = sum(all_volumes) / len(all_volumes)
            wall_ratio = min(wall_volume / avg_vol / 10, 1.0)  # 10배면 최대
            if wall_detected == "buy_wall":
                wall_score = 0.5 + 0.5 * wall_ratio
            elif wall_detected == "sell_wall":
                wall_score = 0.5 - 0.5 * wall_ratio

        # ── 3. 스프레드 점수 ──
        # 좁은 스프레드 = 유동성 좋음 = 약간 매수 유리
        if spread_pct <= self.TIGHT_SPREAD:
            spread_score = 0.7  # 유동성 풍부
        elif spread_pct >= self.WIDE_SPREAD:
            spread_score = 0.3  # 유동성 부족 (위험)
        else:
            # 선형 보간
            ratio = (spread_pct - self.TIGHT_SPREAD) / (self.WIDE_SPREAD - self.TIGHT_SPREAD)
            spread_score = 0.7 - 0.4 * ratio

        # ── 4. 총량 비율 점수 ──
        total = total_bid + total_ask
        if total > 0:
            volume_score = total_bid / total  # 매수 잔량 비율
        else:
            volume_score = 0.5

        # ── 종합 점수 (가중 평균) ──
        composite = (
            self.WEIGHT_IMBALANCE * imbalance_score
            + self.WEIGHT_WALL * wall_score
            + self.WEIGHT_SPREAD * spread_score
            + self.WEIGHT_VOLUME * volume_score
        )

        # signal_strength: 중립(0.5)에서 얼마나 벗어났는지
        signal_strength = round(abs(composite - 0.5) * 2, 4)  # 0~1
        signal_strength = min(signal_strength, 1.0)

        # 추천 결정
        if composite >= 0.75:
            recommendation = "STRONG_BUY"
        elif composite >= 0.60:
            recommendation = "BUY"
        elif composite <= 0.25:
            recommendation = "STRONG_SELL"
        elif composite <= 0.40:
            recommendation = "SELL"
        else:
            recommendation = "NEUTRAL"

        logger.debug(
            "기관 시그널: composite=%.3f (imb=%.2f, wall=%.2f, "
            "spread=%.2f, vol=%.2f) -> strength=%.2f, rec=%s",
            composite, imbalance_score, wall_score,
            spread_score, volume_score, signal_strength, recommendation,
        )
        return signal_strength, recommendation
