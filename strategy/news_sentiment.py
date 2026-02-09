"""뉴스 감성 분석 모듈.

네이버 검색 API 및 주요 금융 사이트에서 종목 관련 뉴스를 수집하고
감성 분석을 수행하여 매매 신호를 생성한다.
"""

import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import quote

import requests

from utils.logger import setup_logger

logger = setup_logger("oshms.strategy.news")


# ─── 감성 사전 (주식 특화) ───

POSITIVE_WORDS = {
    # 실적/재무
    "흑자전환", "실적개선", "사상최대", "매출증가", "영업이익", "순이익증가",
    "실적호조", "어닝서프라이즈", "수주", "대규모수주", "계약체결",
    # 시장 긍정
    "급등", "상한가", "신고가", "52주신고가", "강세", "반등", "돌파",
    "매수추천", "목표가상향", "투자의견상향", "컨센서스상회",
    # 산업/사업
    "신사업", "특허", "기술력", "시장점유율", "독점", "인수합병",
    "성장동력", "호재", "모멘텀", "기대감", "수혜주",
    # 수급/외국인
    "외국인매수", "기관매수", "순매수", "대량매수", "프로그램매수",
    # 정책
    "규제완화", "지원정책", "정부지원", "감세", "보조금",
}

NEGATIVE_WORDS = {
    # 실적/재무
    "적자전환", "실적악화", "매출감소", "영업손실", "순손실", "적자확대",
    "어닝쇼크", "실적부진", "하향조정", "신용등급하락",
    # 시장 부정
    "급락", "하한가", "신저가", "52주신저가", "약세", "폭락", "하락",
    "매도추천", "목표가하향", "투자의견하향", "컨센서스하회",
    # 위험
    "소송", "횡령", "분식회계", "상장폐지", "관리종목", "감사의견거절",
    "부도", "파산", "유상증자", "오버행", "악재", "리스크",
    # 수급
    "외국인매도", "기관매도", "순매도", "대량매도", "공매도",
    # 정책
    "규제강화", "과징금", "제재", "수출규제",
}

INTENSITY_WORDS = {
    "대폭": 1.5, "급격": 1.5, "폭발적": 1.8, "사상": 1.5, "역대": 1.3,
    "사상최대": 1.8, "사상최악": 1.8, "대규모": 1.3, "초대형": 1.5,
}


@dataclass
class NewsItem:
    """뉴스 기사."""
    title: str
    description: str = ""
    source: str = ""
    published: str = ""
    url: str = ""
    sentiment_score: float = 0.0  # -1.0 ~ +1.0


@dataclass
class SentimentResult:
    """감성 분석 결과."""
    stock_code: str
    stock_name: str
    overall_score: float = 0.0     # -1.0 ~ +1.0
    news_count: int = 0
    positive_count: int = 0
    negative_count: int = 0
    neutral_count: int = 0
    key_headlines: list[str] = field(default_factory=list)
    analysis_time: str = field(default_factory=lambda: datetime.now().strftime("%H:%M:%S"))

    @property
    def signal(self) -> str:
        if self.overall_score > 0.3:
            return "긍정"
        elif self.overall_score < -0.3:
            return "부정"
        return "중립"


class NewsSentimentAnalyzer:
    """뉴스 감성 분석기."""

    def __init__(
        self,
        naver_client_id: str = "",
        naver_client_secret: str = "",
    ):
        self.naver_client_id = naver_client_id or os.getenv("NAVER_CLIENT_ID", "")
        self.naver_client_secret = naver_client_secret or os.getenv("NAVER_CLIENT_SECRET", "")
        self.session = requests.Session()
        self._cache: dict[str, tuple[float, SentimentResult]] = {}
        self._cache_ttl = 300  # 5분 캐시

    def analyze(self, stock_code: str, stock_name: str) -> SentimentResult:
        """종목의 최신 뉴스를 분석하여 감성 점수를 반환한다."""
        cache_key = f"{stock_code}_{stock_name}"
        now = time.time()
        if cache_key in self._cache:
            cached_time, cached_result = self._cache[cache_key]
            if now - cached_time < self._cache_ttl:
                return cached_result

        news_items = self._collect_news(stock_name)
        result = self._analyze_sentiment(stock_code, stock_name, news_items)

        self._cache[cache_key] = (now, result)
        logger.info(
            "[%s] 뉴스 감성분석: %d건 분석, 점수=%.2f (%s)",
            stock_name, result.news_count, result.overall_score, result.signal,
        )
        return result

    def _collect_news(self, query: str) -> list[NewsItem]:
        """여러 소스에서 뉴스를 수집한다."""
        all_news = []

        # 1. 네이버 뉴스 검색 API
        if self.naver_client_id:
            all_news.extend(self._fetch_naver_news(query))

        # 2. 네이버 뉴스 웹 크롤링 (API 키 없을 때 폴백)
        if not all_news:
            all_news.extend(self._fetch_naver_news_web(query))

        return all_news

    def _fetch_naver_news(self, query: str) -> list[NewsItem]:
        """네이버 검색 API로 뉴스를 가져온다."""
        url = "https://openapi.naver.com/v1/search/news.json"
        headers = {
            "X-Naver-Client-Id": self.naver_client_id,
            "X-Naver-Client-Secret": self.naver_client_secret,
        }
        params = {
            "query": f"{query} 주식",
            "display": 20,
            "sort": "date",
        }

        try:
            resp = self.session.get(url, headers=headers, params=params, timeout=5)
            resp.raise_for_status()
            data = resp.json()

            items = []
            for item in data.get("items", []):
                title = self._clean_html(item.get("title", ""))
                desc = self._clean_html(item.get("description", ""))
                items.append(NewsItem(
                    title=title,
                    description=desc,
                    source="네이버뉴스",
                    published=item.get("pubDate", ""),
                    url=item.get("link", ""),
                ))
            return items
        except Exception as e:
            logger.debug("네이버 API 뉴스 수집 실패: %s", e)
            return []

    def _fetch_naver_news_web(self, query: str) -> list[NewsItem]:
        """네이버 뉴스 웹에서 뉴스를 크롤링한다 (API 키 불필요)."""
        encoded = quote(f"{query} 주식")
        url = f"https://search.naver.com/search.naver?where=news&query={encoded}&sort=1"

        try:
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            }
            resp = self.session.get(url, headers=headers, timeout=5)
            resp.raise_for_status()
            html = resp.text

            items = []
            # 제목 추출 (간소화된 파싱)
            title_pattern = r'<a[^>]*class="news_tit"[^>]*title="([^"]*)"'
            titles = re.findall(title_pattern, html)

            desc_pattern = r'<div class="news_dsc">\s*<div[^>]*>\s*<a[^>]*>([^<]*)'
            descs = re.findall(desc_pattern, html)

            for idx, title in enumerate(titles[:15]):
                desc = descs[idx] if idx < len(descs) else ""
                items.append(NewsItem(
                    title=self._clean_html(title),
                    description=self._clean_html(desc),
                    source="네이버웹",
                ))

            return items
        except Exception as e:
            logger.debug("네이버 웹 뉴스 수집 실패: %s", e)
            return []

    def _analyze_sentiment(
        self, stock_code: str, stock_name: str, news_items: list[NewsItem]
    ) -> SentimentResult:
        """뉴스 목록의 감성을 분석한다."""
        result = SentimentResult(stock_code=stock_code, stock_name=stock_name)
        result.news_count = len(news_items)

        if not news_items:
            return result

        scores = []
        for item in news_items:
            score = self._score_text(item.title + " " + item.description)
            item.sentiment_score = score
            scores.append(score)

            if score > 0.2:
                result.positive_count += 1
            elif score < -0.2:
                result.negative_count += 1
            else:
                result.neutral_count += 1

        # 가중 평균 (최신 뉴스에 더 높은 가중치)
        if scores:
            weights = [1.0 + 0.1 * (len(scores) - i) for i in range(len(scores))]
            weighted_sum = sum(s * w for s, w in zip(scores, weights))
            total_weight = sum(weights)
            result.overall_score = max(-1.0, min(1.0, weighted_sum / total_weight))

        # 핵심 헤드라인 (감성 점수 절대값이 높은 순)
        sorted_news = sorted(news_items, key=lambda x: abs(x.sentiment_score), reverse=True)
        result.key_headlines = [
            f"[{'▲' if n.sentiment_score > 0 else '▼' if n.sentiment_score < 0 else '─'}] {n.title}"
            for n in sorted_news[:5]
        ]

        return result

    def _score_text(self, text: str) -> float:
        """텍스트의 감성 점수를 계산한다."""
        if not text:
            return 0.0

        text_normalized = text.replace(" ", "")
        pos_count = 0
        neg_count = 0
        intensity = 1.0

        # 강도 단어 확인
        for word, mult in INTENSITY_WORDS.items():
            if word in text_normalized:
                intensity = max(intensity, mult)

        # 긍정어 카운트
        for word in POSITIVE_WORDS:
            if word in text_normalized:
                pos_count += 1

        # 부정어 카운트
        for word in NEGATIVE_WORDS:
            if word in text_normalized:
                neg_count += 1

        total = pos_count + neg_count
        if total == 0:
            return 0.0

        raw_score = (pos_count - neg_count) / total
        return max(-1.0, min(1.0, raw_score * intensity))

    @staticmethod
    def _clean_html(text: str) -> str:
        """HTML 태그를 제거한다."""
        clean = re.sub(r"<[^>]+>", "", text)
        clean = clean.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
        clean = clean.replace("&quot;", '"').replace("&apos;", "'")
        return clean.strip()
