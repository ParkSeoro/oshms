"""v4.9 세션 기반 매매 윈도우 테스트."""

import unittest
from datetime import datetime

from strategy.session import (
    TradingSession,
    SESSION_PROFILES,
    get_session,
    get_profile,
)


class TestSessionBoundaries(unittest.TestCase):
    """시간 → 세션 매핑이 정확한지 검증."""

    def test_pre_open(self):
        self.assertEqual(get_session(datetime(2026, 1, 1, 8, 30)), TradingSession.PRE_OPEN)

    def test_opening(self):
        self.assertEqual(get_session(datetime(2026, 1, 1, 9, 0)), TradingSession.OPENING)
        self.assertEqual(get_session(datetime(2026, 1, 1, 9, 29)), TradingSession.OPENING)

    def test_morning(self):
        self.assertEqual(get_session(datetime(2026, 1, 1, 9, 30)), TradingSession.MORNING)
        self.assertEqual(get_session(datetime(2026, 1, 1, 11, 29)), TradingSession.MORNING)

    def test_lunch(self):
        self.assertEqual(get_session(datetime(2026, 1, 1, 11, 30)), TradingSession.LUNCH)
        self.assertEqual(get_session(datetime(2026, 1, 1, 12, 59)), TradingSession.LUNCH)

    def test_afternoon(self):
        self.assertEqual(get_session(datetime(2026, 1, 1, 13, 0)), TradingSession.AFTERNOON)
        self.assertEqual(get_session(datetime(2026, 1, 1, 13, 59)), TradingSession.AFTERNOON)

    def test_pre_close(self):
        self.assertEqual(get_session(datetime(2026, 1, 1, 14, 0)), TradingSession.PRE_CLOSE)
        self.assertEqual(get_session(datetime(2026, 1, 1, 15, 9)), TradingSession.PRE_CLOSE)

    def test_closed(self):
        self.assertEqual(get_session(datetime(2026, 1, 1, 15, 10)), TradingSession.CLOSED)


class TestSessionProfiles(unittest.TestCase):
    """세션 프로필 정책이 v4.9 설계와 일치하는지."""

    def test_opening_blocks_new_entries(self):
        profile = SESSION_PROFILES[TradingSession.OPENING]
        self.assertFalse(profile.allow_new_entry, "오프닝은 갭 소화 시간 — 진입 금지")
        self.assertEqual(profile.max_new_entries, 0)

    def test_morning_is_primary_window(self):
        profile = SESSION_PROFILES[TradingSession.MORNING]
        self.assertTrue(profile.allow_new_entry)
        self.assertEqual(profile.min_conviction, 40.0)
        self.assertEqual(profile.size_mult, 1.0)
        self.assertEqual(profile.max_new_entries, 4)

    def test_lunch_blocks(self):
        profile = SESSION_PROFILES[TradingSession.LUNCH]
        self.assertFalse(profile.allow_new_entry, "점심은 유동성 고갈 — 진입 금지")

    def test_afternoon_is_stricter_than_morning(self):
        """오후는 오전보다 진입 기준이 더 엄격해야 한다."""
        morning = SESSION_PROFILES[TradingSession.MORNING]
        afternoon = SESSION_PROFILES[TradingSession.AFTERNOON]
        self.assertTrue(afternoon.allow_new_entry)
        self.assertGreater(afternoon.min_conviction, morning.min_conviction)
        self.assertLess(afternoon.size_mult, morning.size_mult)
        self.assertLess(afternoon.max_new_entries, morning.max_new_entries)

    def test_pre_close_forces_defensive(self):
        profile = SESSION_PROFILES[TradingSession.PRE_CLOSE]
        self.assertFalse(profile.allow_new_entry)
        self.assertTrue(profile.force_defensive_sell, "마감전은 약세 강제 청산")

    def test_get_profile_matches_session(self):
        now = datetime(2026, 1, 1, 10, 0)
        profile = get_profile(now)
        self.assertEqual(profile.session, TradingSession.MORNING)


if __name__ == "__main__":
    unittest.main()
