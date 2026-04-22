"""v4.9 통합 확신도 계산 테스트."""

import unittest

from strategy.conviction import (
    MIN_CONVICTION_TO_ENTER,
    HALF_SIZE_THRESHOLD,
    FULL_SIZE_THRESHOLD,
    calc_conviction,
    session_fit_score,
)


class TestConvictionSize(unittest.TestCase):
    """점수 구간별 size_mult가 규칙대로 적용되는지."""

    def _make(self, total):
        from strategy.conviction import ConvictionScore
        s = ConvictionScore(total=total)
        return s

    def test_below_min_rejects(self):
        s = self._make(MIN_CONVICTION_TO_ENTER - 1)
        self.assertEqual(s.size_mult(), 0.0)
        self.assertEqual(s.tier(), "REJECT")

    def test_half_tier(self):
        s = self._make(MIN_CONVICTION_TO_ENTER + 1)
        self.assertEqual(s.size_mult(), 0.5)
        self.assertEqual(s.tier(), "HALF")

    def test_full_tier(self):
        s = self._make(HALF_SIZE_THRESHOLD + 1)
        self.assertEqual(s.size_mult(), 1.0)
        self.assertEqual(s.tier(), "FULL")

    def test_max_tier(self):
        s = self._make(FULL_SIZE_THRESHOLD + 1)
        self.assertEqual(s.size_mult(), 1.2)
        self.assertEqual(s.tier(), "MAX")


class TestConvictionCalc(unittest.TestCase):
    def test_sell_signal_returns_zero(self):
        """매도 신호(expert_total_score <= 0)는 확신도 0."""
        s = calc_conviction(expert_total_score=-0.3, volume_ratio=3.0, contract_strength=150)
        self.assertEqual(s.total, 0.0)

    def test_weak_buy_below_entry_threshold(self):
        """약한 매수 신호는 MIN_CONVICTION_TO_ENTER 미만이어야 한다."""
        s = calc_conviction(
            expert_total_score=0.22,  # 임계선 언저리
            expert_confidence=0.4,
            trend_score=0.15,
            volume_ratio=1.2,
            contract_strength=110,
            session_fit=0.3,
        )
        self.assertLess(s.total, MIN_CONVICTION_TO_ENTER, f"약한 신호가 진입했다: {s.describe()}")

    def test_strong_buy_qualifies(self):
        """강한 매수 신호(모든 지표 양호)는 HALF 이상."""
        s = calc_conviction(
            expert_total_score=0.4,
            expert_confidence=0.7,
            trend_score=0.5,
            volume_ratio=2.0,
            contract_strength=150,
            session_fit=0.8,
            macd_cross="golden",
        )
        self.assertGreaterEqual(s.total, MIN_CONVICTION_TO_ENTER,
                                 f"강한 신호가 거절됨: {s.describe()}")

    def test_dead_cross_penalizes_trend(self):
        """MACD 데드크로스는 추세 점수를 대폭 감쇠."""
        base = calc_conviction(
            expert_total_score=0.3, expert_confidence=0.5,
            trend_score=0.5, volume_ratio=1.5, contract_strength=130,
            session_fit=0.6, macd_cross="",
        )
        dead = calc_conviction(
            expert_total_score=0.3, expert_confidence=0.5,
            trend_score=0.5, volume_ratio=1.5, contract_strength=130,
            session_fit=0.6, macd_cross="dead",
        )
        self.assertLess(dead.trend_alignment_pts, base.trend_alignment_pts)

    def test_session_min_gates_entry(self):
        """세션 최소 확신도가 글로벌 최소보다 높으면 그게 우선."""
        s = calc_conviction(
            expert_total_score=0.25, expert_confidence=0.5,
            trend_score=0.3, volume_ratio=1.4, contract_strength=125,
            session_fit=0.5,
        )
        # should_enter with session_min=75: need to pass stricter bar
        if s.total < 75.0:
            self.assertFalse(s.should_enter(75.0))
        # but passes at 65 (still above global 70 floor of MIN_CONVICTION_TO_ENTER)
        s2 = calc_conviction(
            expert_total_score=0.4, expert_confidence=0.7,
            trend_score=0.5, volume_ratio=2.0, contract_strength=150,
            session_fit=0.8, macd_cross="golden",
        )
        self.assertTrue(s2.should_enter(MIN_CONVICTION_TO_ENTER))

    def test_bounds_clamp(self):
        """극단적 입력에도 합계는 합리적 범위 (≤110)."""
        s = calc_conviction(
            expert_total_score=1.0, expert_confidence=1.0,
            trend_score=1.0, volume_ratio=10.0, contract_strength=500,
            session_fit=1.0, macd_cross="golden",
        )
        self.assertLessEqual(s.total, 110.0 + 1e-6)
        self.assertGreater(s.total, 90.0)


class TestSessionFit(unittest.TestCase):
    def test_morning_favors_volume_and_trend(self):
        high = session_fit_score("morning", volume_ratio=2.5, trend_score=0.5)
        low = session_fit_score("morning", volume_ratio=1.0, trend_score=0.0)
        self.assertGreater(high, low)

    def test_afternoon_weights_trend_more(self):
        """오후는 거래량보다 추세 생존이 중요."""
        trend_strong = session_fit_score("afternoon", volume_ratio=1.2, trend_score=0.5)
        volume_strong = session_fit_score("afternoon", volume_ratio=3.0, trend_score=0.1)
        self.assertGreater(trend_strong, volume_strong)

    def test_lunch_returns_zero(self):
        self.assertEqual(session_fit_score("lunch", 3.0, 0.5), 0.0)


if __name__ == "__main__":
    unittest.main()
