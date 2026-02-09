"""캔들스틱 패턴 인식 테스트."""

import unittest

from strategy.patterns import PatternRecognizer, PatternDirection, CandlePattern


class TestPatternRecognizer(unittest.TestCase):

    def setUp(self):
        self.recognizer = PatternRecognizer()

    def _candle(self, o, h, l, c, v=1000):
        return {"open": o, "high": h, "low": l, "close": c, "volume": v}

    def test_insufficient_data(self):
        patterns = self.recognizer.recognize_all([self._candle(100, 110, 90, 105)])
        self.assertEqual(patterns, [])

    def test_doji_detection(self):
        # 도지: 시가 == 종가, 긴 꼬리
        candles = [
            self._candle(100, 110, 90, 105),
            self._candle(105, 115, 95, 108),
            self._candle(108, 118, 98, 110),
            self._candle(110, 120, 100, 112),
            self._candle(112, 125, 100, 112),  # 도지 (시가 == 종가, 꼬리 긺)
        ]
        patterns = self.recognizer.recognize_all(candles)
        doji_found = any(p.name == "doji" for p in patterns)
        self.assertTrue(doji_found)

    def test_hammer_detection(self):
        # 하락추세 후 망치형: 아래꼬리 긺, 위꼬리 없음, 몸통 작음
        candles = [
            self._candle(120, 125, 115, 118),
            self._candle(118, 120, 112, 114),
            self._candle(114, 116, 108, 110),
            self._candle(110, 112, 104, 106),
            self._candle(104, 105, 90, 105),   # 망치: 아래꼬리(14) > 몸통(1)*2, 위꼬리(0)
        ]
        patterns = self.recognizer.recognize_all(candles)
        hammer_found = any(p.name == "hammer" for p in patterns)
        self.assertTrue(hammer_found)

    def test_bullish_engulfing(self):
        # 하락추세 후 상승장악형
        candles = [
            self._candle(120, 125, 115, 118),
            self._candle(118, 120, 112, 114),
            self._candle(114, 116, 108, 110),
            self._candle(110, 112, 106, 107),  # 음봉
            self._candle(106, 115, 105, 114),  # 양봉이 전봉 장악
        ]
        patterns = self.recognizer.recognize_all(candles)
        engulfing = [p for p in patterns if p.name == "bullish_engulfing"]
        self.assertTrue(len(engulfing) > 0)
        self.assertEqual(engulfing[0].direction, PatternDirection.BULLISH)

    def test_bearish_engulfing(self):
        # 상승추세 후 하락장악형
        candles = [
            self._candle(100, 105, 98, 103),
            self._candle(103, 108, 101, 107),
            self._candle(107, 113, 105, 112),
            self._candle(112, 118, 110, 116),  # 양봉
            self._candle(117, 118, 108, 110),  # 음봉이 전봉 장악
        ]
        patterns = self.recognizer.recognize_all(candles)
        engulfing = [p for p in patterns if p.name == "bearish_engulfing"]
        self.assertTrue(len(engulfing) > 0)

    def test_shooting_star(self):
        # 상승추세 후 슈팅스타: 위꼬리 긺, 아래꼬리 없음
        candles = [
            self._candle(100, 105, 98, 103),
            self._candle(103, 108, 101, 107),
            self._candle(107, 113, 105, 112),
            self._candle(112, 118, 110, 116),
            self._candle(117, 130, 117, 118),  # 위꼬리(12) > 몸통(1)*2, 아래꼬리(0)
        ]
        patterns = self.recognizer.recognize_all(candles)
        star_found = any(p.name == "shooting_star" for p in patterns)
        self.assertTrue(star_found)

    def test_three_white_soldiers(self):
        # 적삼병
        candles = [
            self._candle(90, 95, 88, 92),
            self._candle(88, 93, 86, 90),
            self._candle(91, 97, 90, 96),   # 양봉1
            self._candle(93, 101, 92, 100),  # 양봉2 (시가/종가 더 높음)
            self._candle(97, 107, 96, 106),  # 양봉3
        ]
        patterns = self.recognizer.recognize_all(candles)
        soldiers = [p for p in patterns if p.name == "three_white_soldiers"]
        self.assertTrue(len(soldiers) > 0)

    def test_three_black_crows(self):
        # 흑삼병
        candles = [
            self._candle(108, 115, 106, 112),
            self._candle(112, 118, 110, 116),
            self._candle(115, 116, 108, 109),  # 음봉1
            self._candle(112, 113, 103, 104),  # 음봉2
            self._candle(107, 108, 96, 97),    # 음봉3
        ]
        patterns = self.recognizer.recognize_all(candles)
        crows = [p for p in patterns if p.name == "three_black_crows"]
        self.assertTrue(len(crows) > 0)

    def test_signal_score_bullish(self):
        patterns = [
            CandlePattern("hammer", "망치형", PatternDirection.BULLISH, 0.7, 4),
            CandlePattern("bullish_engulfing", "상승장악형", PatternDirection.BULLISH, 0.8, 4),
        ]
        score = self.recognizer.get_signal_score(patterns)
        self.assertGreater(score, 0)

    def test_signal_score_bearish(self):
        patterns = [
            CandlePattern("shooting_star", "슈팅스타", PatternDirection.BEARISH, 0.7, 4),
        ]
        score = self.recognizer.get_signal_score(patterns)
        self.assertLess(score, 0)

    def test_signal_score_empty(self):
        score = self.recognizer.get_signal_score([])
        self.assertEqual(score, 0.0)


if __name__ == "__main__":
    unittest.main()
