"""뉴스 감성 분석 테스트."""

import unittest

from strategy.news_sentiment import NewsSentimentAnalyzer, NewsItem, SentimentResult


class TestNewsSentimentAnalyzer(unittest.TestCase):

    def setUp(self):
        self.analyzer = NewsSentimentAnalyzer()

    def test_score_positive_text(self):
        score = self.analyzer._score_text("삼성전자 사상최대 실적개선 영업이익 급등")
        self.assertGreater(score, 0)

    def test_score_negative_text(self):
        score = self.analyzer._score_text("적자전환 실적악화 주가 급락 공매도")
        self.assertLess(score, 0)

    def test_score_neutral_text(self):
        score = self.analyzer._score_text("오늘 날씨가 좋습니다")
        self.assertEqual(score, 0.0)

    def test_score_empty_text(self):
        score = self.analyzer._score_text("")
        self.assertEqual(score, 0.0)

    def test_intensity_amplifies(self):
        normal = self.analyzer._score_text("실적개선")
        intense = self.analyzer._score_text("대폭 실적개선")
        self.assertGreaterEqual(intense, normal)

    def test_clean_html(self):
        result = NewsSentimentAnalyzer._clean_html("<b>삼성전자</b> &amp; SK하이닉스")
        self.assertEqual(result, "삼성전자 & SK하이닉스")

    def test_sentiment_result_signal(self):
        result = SentimentResult("005930", "삼성전자", overall_score=0.5)
        self.assertEqual(result.signal, "긍정")

        result2 = SentimentResult("005930", "삼성전자", overall_score=-0.5)
        self.assertEqual(result2.signal, "부정")

        result3 = SentimentResult("005930", "삼성전자", overall_score=0.1)
        self.assertEqual(result3.signal, "중립")

    def test_analyze_sentiment_mixed(self):
        news_items = [
            NewsItem(title="삼성전자 사상최대 실적 달성", description="영업이익 급등"),
            NewsItem(title="반도체 업황 불투명", description="실적 하향조정 우려"),
            NewsItem(title="삼성전자 신제품 출시", description=""),
        ]
        result = self.analyzer._analyze_sentiment("005930", "삼성전자", news_items)
        self.assertEqual(result.news_count, 3)
        self.assertGreater(result.positive_count, 0)

    def test_analyze_sentiment_empty(self):
        result = self.analyzer._analyze_sentiment("005930", "삼성전자", [])
        self.assertEqual(result.news_count, 0)
        self.assertEqual(result.overall_score, 0.0)

    def test_key_headlines_sorted_by_strength(self):
        news_items = [
            NewsItem(title="일반 뉴스입니다", description=""),
            NewsItem(title="삼성전자 사상최대 실적 급등 흑자전환", description="호재"),
            NewsItem(title="시장 동향", description=""),
        ]
        result = self.analyzer._analyze_sentiment("005930", "삼성전자", news_items)
        self.assertTrue(len(result.key_headlines) > 0)
        # 가장 강한 감성의 뉴스가 먼저
        self.assertIn("사상최대", result.key_headlines[0])

    def test_caching(self):
        """캐시 동작 테스트."""
        # 직접 캐시에 결과 삽입
        import time
        cached_result = SentimentResult("005930", "삼성전자", overall_score=0.77, news_count=5)
        self.analyzer._cache["005930_삼성전자"] = (time.time(), cached_result)

        result = self.analyzer.analyze("005930", "삼성전자")
        self.assertEqual(result.overall_score, 0.77)


if __name__ == "__main__":
    unittest.main()
