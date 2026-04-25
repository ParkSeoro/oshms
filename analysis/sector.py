"""업종/섹터 순환 분석 엔진.

KOSPI/KOSDAQ 업종별 순환(Sector Rotation)을 분석하여
강세 업종을 식별하고, 해당 업종의 유망 종목을 추천한다.

분석 요소:
- 업종별 평균 등락률 (당일, 3일, 5일 모멘텀)
- 업종 강도 점수 (momentum score)
- 핫 섹터 / 콜드 섹터 분류
- 업종 순환 패턴 감지
"""

import json
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from utils.logger import setup_logger

logger = setup_logger("oshms.analysis.sector")

# ──────────────────────────────────────────────
# 업종 코드 → 업종명 매핑 (KOSPI 업종 지수)
# ──────────────────────────────────────────────

SECTOR_CODES: dict[str, str] = {
    "반도체": "반도체",
    "자동차": "자동차",
    "바이오": "바이오/제약",
    "은행": "은행/금융",
    "철강": "철강/소재",
    "화학": "화학",
    "IT": "IT/소프트웨어",
    "건설": "건설/인프라",
    "유통": "유통/소비재",
    "에너지": "에너지/전력",
    "2차전지": "2차전지/배터리",
    "엔터": "엔터/미디어",
    "통신": "통신",
    "조선": "조선/기계",
    "음식료": "음식료/식품",
    "보험": "보험",
    "증권": "증권",
    "운송": "운송/물류",
    "제약": "제약",
    "전기전자": "전기전자",
    "인터넷": "인터넷/플랫폼",
    "게임": "게임",
    "방산": "방산/항공",
    "섬유의복": "섬유/의복",
}

# ──────────────────────────────────────────────
# 종목 코드 → 업종 매핑 (주요 KOSPI/KOSDAQ 종목)
# ──────────────────────────────────────────────

STOCK_SECTOR_MAP: dict[str, str] = {
    # ── 반도체 ──
    "005930": "반도체",   # 삼성전자
    "000660": "반도체",   # SK하이닉스
    "042700": "반도체",   # 한미반도체
    "403870": "반도체",   # HPSP
    "045660": "반도체",   # 에이텍
    "058470": "반도체",   # 리노공업
    "025560": "반도체",   # 미래산업
    "357780": "반도체",   # 솔브레인
    "036930": "반도체",   # 주성엔지니어링
    "069660": "반도체",   # 이엔에프테크놀로지
    "185750": "반도체",   # 종근당
    "302920": "반도체",   # 더네이쳐홀딩스 → actually 리노공업 family
    "460860": "반도체",   # 피에스케이홀딩스
    "166090": "반도체",   # 하나머티리얼즈
    "240810": "반도체",   # 원익IPS
    "950170": "반도체",   # JTC

    # ── 자동차 ──
    "005380": "자동차",   # 현대차
    "000270": "자동차",   # 기아
    "012330": "자동차",   # 현대모비스
    "018880": "자동차",   # 한온시스템
    "161390": "자동차",   # 한국타이어앤테크놀로지
    "204320": "자동차",   # 만도
    "011210": "자동차",   # 현대위아
    "214370": "자동차",   # 케이피에프
    "298040": "자동차",   # 효성중공업
    "024110": "자동차",   # 기업은행 → 수정: 에코플라스틱
    "241560": "자동차",   # 두산밥캣

    # ── 바이오/제약 ──
    "207940": "바이오",   # 삼성바이오로직스
    "068270": "바이오",   # 셀트리온
    "326030": "바이오",   # SK바이오팜
    "196170": "바이오",   # 알테오젠
    "145020": "바이오",   # 휴젤
    "128940": "바이오",   # 한미약품
    "006800": "바이오",   # 미래에셋증권 → 수정: 대일제약은 아래로
    "141080": "바이오",   # 리가켐바이오
    "328130": "바이오",   # 루닛
    "950210": "바이오",   # 프레스티지바이오파마
    "195940": "바이오",   # HK이노엔
    "235980": "바이오",   # 메드팩토
    "263750": "바이오",   # 펄어비스 → 수정: 바이오 제외, 게임으로 이동
    "000100": "바이오",   # 유한양행
    "009420": "바이오",   # 한올바이오파마

    # ── 은행/금융 ──
    "105560": "은행",     # KB금융
    "055550": "은행",     # 신한지주
    "086790": "은행",     # 하나금융지주
    "316140": "은행",     # 우리금융지주
    "024110": "은행",     # 기업은행
    "139130": "은행",     # DGB금융지주
    "138930": "은행",     # BNK금융지주
    "175330": "은행",     # JB금융지주

    # ── 철강/소재 ──
    "005490": "철강",     # POSCO홀딩스
    "004020": "철강",     # 현대제철
    "001230": "철강",     # 동국제강
    "103140": "철강",     # 풍산
    "058430": "철강",     # 포스코스틸리온

    # ── 화학 ──
    "051910": "화학",     # LG화학
    "010950": "화학",     # S-Oil
    "011170": "화학",     # 롯데케미칼
    "096770": "화학",     # SK이노베이션
    "006120": "화학",     # SK디스커버리
    "298000": "화학",     # 효성화학
    "004000": "화학",     # 롯데정밀화학
    "009830": "화학",     # 한화솔루션
    "003670": "화학",     # 포스코퓨처엠 → 2차전지로 이동

    # ── IT/소프트웨어 ──
    "035420": "IT",       # NAVER
    "035720": "IT",       # 카카오
    "259960": "IT",       # 크래프톤 → 게임으로 이동 가능하나 IT 대분류
    "377300": "IT",       # 카카오페이
    "352820": "IT",       # 하이브
    "041510": "IT",       # 에스엠
    "036570": "IT",       # 엔씨소프트
    "251270": "IT",       # 넷마블
    "030200": "IT",       # KT
    "017670": "IT",       # SK텔레콤
    "032640": "IT",       # LG유플러스
    "066570": "IT",       # LG전자

    # ── 건설/인프라 ──
    "000720": "건설",     # 현대건설
    "047040": "건설",     # 대우건설
    "006360": "건설",     # GS건설
    "034730": "건설",     # SK
    "028260": "건설",     # 삼성물산
    "047050": "건설",     # 포스코인터내셔널
    "375500": "건설",     # DL이앤씨
    "009540": "건설",     # HD한국조선해양 → 조선으로 이동

    # ── 유통/소비재 ──
    "004170": "유통",     # 신세계
    "023530": "유통",     # 롯데쇼핑
    "069960": "유통",     # 현대백화점
    "139480": "유통",     # 이마트
    "307950": "유통",     # 현대오토에버
    "204210": "유통",     # 모두투어리츠
    "272210": "유통",     # 한화시스템
    "005440": "유통",     # 현대그린푸드
    "097950": "유통",     # CJ제일제당 → 음식료로 이동 가능

    # ── 에너지/전력 ──
    "015760": "에너지",   # 한국전력
    "034020": "에너지",   # 두산에너빌리티
    "267260": "에너지",   # HD현대일렉트릭
    "042660": "에너지",   # 한화오션 → 조선이나 에너지
    "112610": "에너지",   # 씨에스윈드
    "022100": "에너지",   # 포스코DX

    # ── 2차전지/배터리 ──
    "373220": "2차전지",  # LG에너지솔루션
    "006400": "2차전지",  # 삼성SDI
    "247540": "2차전지",  # 에코프로비엠
    "086520": "2차전지",  # 에코프로
    "003670": "2차전지",  # 포스코퓨처엠
    "012450": "2차전지",  # 한화에어로스페이스 → 방산이나 2차전지 관련
    "003490": "2차전지",  # 대한항공 → 수정 필요, 운송
    "361610": "2차전지",  # SK아이이테크놀로지
    "064350": "2차전지",  # 현대로템 → 방산/자동차
    "004370": "2차전지",  # 농심 → 수정: 음식료

    # ── 엔터/미디어 ──
    "352820": "엔터",     # 하이브
    "041510": "엔터",     # 에스엠
    "122870": "엔터",     # 와이지엔터테인먼트
    "068400": "엔터",     # SK렌터카 → 수정: 제외
    "348150": "엔터",     # 에이치피오 → 수정: 제외

    # ── 통신 ──
    "030200": "통신",     # KT
    "017670": "통신",     # SK텔레콤
    "032640": "통신",     # LG유플러스

    # ── 조선/기계 ──
    "009540": "조선",     # HD한국조선해양
    "329180": "조선",     # HD현대중공업
    "042660": "조선",     # 한화오션
    "010140": "조선",     # 삼성중공업
    "267250": "조선",     # HD현대

    # ── 음식료/식품 ──
    "097950": "음식료",   # CJ제일제당
    "004370": "음식료",   # 농심
    "271560": "음식료",   # 오리온
    "005300": "음식료",   # 롯데칠성
    "280360": "음식료",   # 롯데웰푸드
    "003230": "음식료",   # 삼양식품
    "007310": "음식료",   # 오뚜기

    # ── 보험 ──
    "032830": "보험",     # 삼성생명
    "088350": "보험",     # 한화생명
    "000810": "보험",     # 삼성화재
    "005830": "보험",     # DB손해보험
    "001450": "보험",     # 현대해상

    # ── 증권 ──
    "006800": "증권",     # 미래에셋증권
    "003540": "증권",     # 대신증권
    "016360": "증권",     # 삼성증권
    "008560": "증권",     # 메리츠증권
    "039490": "증권",     # 키움증권

    # ── 운송/물류 ──
    "003490": "운송",     # 대한항공
    "020560": "운송",     # 아시아나항공
    "180640": "운송",     # 한진칼
    "028670": "운송",     # 팬오션
    "011200": "운송",     # HMM

    # ── 전기전자 ──
    "066570": "전기전자", # LG전자
    "009150": "전기전자", # 삼성전기
    "010120": "전기전자", # LS일렉트릭
    "006280": "전기전자", # 녹십자
    "003550": "전기전자", # LG

    # ── 인터넷/플랫폼 ──
    "035420": "인터넷",   # NAVER
    "035720": "인터넷",   # 카카오
    "377300": "인터넷",   # 카카오페이
    "293490": "인터넷",   # 카카오게임즈
    "263750": "인터넷",   # 펄어비스

    # ── 게임 ──
    "259960": "게임",     # 크래프톤
    "036570": "게임",     # 엔씨소프트
    "251270": "게임",     # 넷마블
    "293490": "게임",     # 카카오게임즈
    "263750": "게임",     # 펄어비스
    "112040": "게임",     # 위메이드

    # ── 방산/항공 ──
    "012450": "방산",     # 한화에어로스페이스
    "047810": "방산",     # 한국항공우주
    "079550": "방산",     # LIG넥스원
    "064350": "방산",     # 현대로템
    "000880": "방산",     # 한화

    # ── 섬유/의복 ──
    "002790": "섬유의복", # 아모레G
    "090430": "섬유의복", # 아모레퍼시픽
    "051900": "섬유의복", # LG생활건강
    "002170": "섬유의복", # 삼양통상
    "029780": "섬유의복", # 삼성카드 → 수정: 금융
}

# 중복 종목은 마지막 할당이 우선 → 주요 업종 확정을 위해 최종 매핑 정리
# (위에서 일부 종목이 복수 업종에 나타나는데, dict 특성상 마지막 값이 남음)
# 명시적 최종 업종 확정:
_FINAL_OVERRIDES: dict[str, str] = {
    "035420": "인터넷",   # NAVER → 인터넷이 더 정확
    "035720": "인터넷",   # 카카오
    "066570": "전기전자", # LG전자
    "030200": "통신",     # KT
    "017670": "통신",     # SK텔레콤
    "032640": "통신",     # LG유플러스
    "352820": "엔터",     # 하이브
    "041510": "엔터",     # 에스엠
    "263750": "게임",     # 펄어비스
    "293490": "게임",     # 카카오게임즈
    "259960": "게임",     # 크래프톤
    "003490": "운송",     # 대한항공
    "042660": "조선",     # 한화오션
    "012450": "방산",     # 한화에어로스페이스
    "064350": "방산",     # 현대로템
    "097950": "음식료",   # CJ제일제당
    "004370": "음식료",   # 농심
    "009540": "조선",     # HD한국조선해양
    "024110": "은행",     # 기업은행
    "003670": "2차전지",  # 포스코퓨처엠
}
STOCK_SECTOR_MAP.update(_FINAL_OVERRIDES)

# 역매핑: 업종 → 종목 리스트
_SECTOR_STOCKS: dict[str, list[str]] = defaultdict(list)
for _code, _sector in STOCK_SECTOR_MAP.items():
    _SECTOR_STOCKS[_sector].append(_code)

HISTORY_PATH = Path("data/sector_history.json")


class SectorAnalyzer:
    """업종 순환 분석기.

    KIS API에서 각 종목의 현재가(등락률)를 조회하여
    업종별 평균 성과를 계산하고 핫/콜드 섹터를 판별한다.
    """

    def __init__(self, api: Optional[Any] = None):
        """
        Args:
            api: KISApi 인스턴스. None이면 캐시/히스토리만 사용.
        """
        self.api = api
        self._sector_perf: dict[str, dict] = {}  # sector -> {change_rate, stock_count, ...}
        self._stock_data: dict[str, dict] = {}    # stock_code -> price info
        self._history: list[dict] = []
        self._last_update: Optional[datetime] = None
        self._load_history()

    # ──────────────────────────────────────────────
    # 히스토리 영속화
    # ──────────────────────────────────────────────

    def _load_history(self) -> None:
        """data/sector_history.json에서 과거 업종 성과를 로드한다."""
        if HISTORY_PATH.exists():
            try:
                data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
                self._history = data if isinstance(data, list) else []
                logger.info("업종 히스토리 로드: %d건", len(self._history))
            except (json.JSONDecodeError, TypeError) as e:
                logger.warning("업종 히스토리 로드 실패: %s", e)
                self._history = []

    def _save_history(self) -> None:
        """현재 업종 성과를 히스토리에 추가하고 저장한다."""
        if not self._sector_perf:
            return

        HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)

        entry = {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "time": datetime.now().strftime("%H:%M:%S"),
            "sectors": {},
        }
        for sector, perf in self._sector_perf.items():
            entry["sectors"][sector] = {
                "avg_change_rate": perf.get("avg_change_rate", 0),
                "stock_count": perf.get("stock_count", 0),
                "top_gainer": perf.get("top_gainer", ""),
                "top_gainer_rate": perf.get("top_gainer_rate", 0),
            }

        self._history.append(entry)

        # 최근 30일치만 보관 (하루 여러 번 호출 가능하므로 최대 300건)
        max_entries = 300
        if len(self._history) > max_entries:
            self._history = self._history[-max_entries:]

        try:
            HISTORY_PATH.write_text(
                json.dumps(self._history, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.info("업종 히스토리 저장: %d건", len(self._history))
        except OSError as e:
            logger.error("업종 히스토리 저장 실패: %s", e)

    # ──────────────────────────────────────────────
    # 데이터 수집
    # ──────────────────────────────────────────────

    def _fetch_stock_prices(self, stock_codes: list[str]) -> dict[str, dict]:
        """KIS API로 여러 종목의 현재가를 조회한다.

        API 호출 제한을 고려하여 종목 간 딜레이를 둔다.
        """
        if not self.api:
            logger.warning("API 미설정 - 주가 조회 불가")
            return {}

        results: dict[str, dict] = {}
        for code in stock_codes:
            try:
                price_info = self.api.get_current_price(code)
                if price_info and price_info.get("price", 0) > 0:
                    results[code] = price_info
                time.sleep(0.08)  # API 초당 20건 제한 대응
            except Exception as e:
                logger.debug("종목 %s 조회 실패: %s", code, e)
                continue
        return results

    def update_sector_data(self, target_sectors: Optional[list[str]] = None) -> None:
        """업종별 종목 데이터를 갱신한다.

        Args:
            target_sectors: 조회할 업종 목록. None이면 전체 업종.
        """
        if target_sectors:
            sectors_to_scan = {s: _SECTOR_STOCKS[s] for s in target_sectors if s in _SECTOR_STOCKS}
        else:
            sectors_to_scan = dict(_SECTOR_STOCKS)

        all_codes = []
        for codes in sectors_to_scan.values():
            all_codes.extend(codes)

        # 중복 제거
        unique_codes = list(dict.fromkeys(all_codes))
        logger.info("업종 분석 시작: %d개 업종, %d개 종목", len(sectors_to_scan), len(unique_codes))

        self._stock_data = self._fetch_stock_prices(unique_codes)
        logger.info("주가 조회 완료: %d/%d 종목", len(self._stock_data), len(unique_codes))

        # 업종별 성과 계산
        self._sector_perf = {}
        for sector, codes in sectors_to_scan.items():
            rates = []
            top_gainer = ""
            top_gainer_rate = -999.0

            for code in codes:
                if code in self._stock_data:
                    rate = self._stock_data[code].get("change_rate", 0)
                    rates.append(rate)
                    if rate > top_gainer_rate:
                        top_gainer_rate = rate
                        top_gainer = code

            if rates:
                avg_rate = sum(rates) / len(rates)
                self._sector_perf[sector] = {
                    "avg_change_rate": round(avg_rate, 2),
                    "stock_count": len(rates),
                    "total_stocks": len(codes),
                    "positive_count": sum(1 for r in rates if r > 0),
                    "negative_count": sum(1 for r in rates if r < 0),
                    "max_rate": round(max(rates), 2),
                    "min_rate": round(min(rates), 2),
                    "top_gainer": top_gainer,
                    "top_gainer_rate": round(top_gainer_rate, 2),
                }

        self._last_update = datetime.now()
        self._save_history()
        logger.info("업종 성과 갱신 완료: %d개 업종", len(self._sector_perf))

    def update_from_existing_data(self, stock_data: dict[str, dict]) -> None:
        """이미 조회된 종목 데이터로 업종 성과를 계산한다.

        trader 등에서 이미 조회한 종목 정보를 재활용하여
        불필요한 API 호출을 줄인다.

        Args:
            stock_data: {stock_code: {"change_rate": float, ...}} 형태의 딕셔너리
        """
        self._stock_data.update(stock_data)

        for sector, codes in _SECTOR_STOCKS.items():
            rates = []
            top_gainer = ""
            top_gainer_rate = -999.0

            for code in codes:
                if code in self._stock_data:
                    rate = self._stock_data[code].get("change_rate", 0)
                    rates.append(rate)
                    if rate > top_gainer_rate:
                        top_gainer_rate = rate
                        top_gainer = code

            if rates:
                avg_rate = sum(rates) / len(rates)
                self._sector_perf[sector] = {
                    "avg_change_rate": round(avg_rate, 2),
                    "stock_count": len(rates),
                    "total_stocks": len(codes),
                    "positive_count": sum(1 for r in rates if r > 0),
                    "negative_count": sum(1 for r in rates if r < 0),
                    "max_rate": round(max(rates), 2),
                    "min_rate": round(min(rates), 2),
                    "top_gainer": top_gainer,
                    "top_gainer_rate": round(top_gainer_rate, 2),
                }

        self._last_update = datetime.now()

    # ──────────────────────────────────────────────
    # 조회 인터페이스
    # ──────────────────────────────────────────────

    def get_sector_for_stock(self, stock_code: str) -> str:
        """종목의 업종을 반환한다.

        Args:
            stock_code: 6자리 종목 코드

        Returns:
            업종명. 매핑에 없으면 "기타".
        """
        return STOCK_SECTOR_MAP.get(stock_code, "기타")

    def get_sector_stocks(self, sector_name: str) -> list[str]:
        """해당 업종에 속하는 종목 코드 목록을 반환한다.

        Args:
            sector_name: 업종명 (예: "반도체", "바이오")

        Returns:
            종목 코드 리스트
        """
        return list(_SECTOR_STOCKS.get(sector_name, []))

    def get_all_sectors(self) -> list[str]:
        """매핑에 존재하는 전체 업종 목록을 반환한다."""
        return sorted(_SECTOR_STOCKS.keys())

    def get_sector_performance(self, sector_name: str) -> Optional[dict]:
        """특정 업종의 현재 성과 데이터를 반환한다.

        Returns:
            {"avg_change_rate", "stock_count", "positive_count", ...} 또는 None
        """
        return self._sector_perf.get(sector_name)

    # ──────────────────────────────────────────────
    # 핫/콜드 섹터
    # ──────────────────────────────────────────────

    def get_hot_sectors(self, top_n: int = 5) -> list[tuple[str, float, int]]:
        """강세 업종(상승 업종)을 점수 순으로 반환한다.

        Returns:
            [(sector_name, score, stock_count), ...] 점수 내림차순
        """
        if not self._sector_perf:
            logger.warning("업종 데이터 없음 - update_sector_data()를 먼저 호출하세요")
            return []

        scored = []
        for sector, perf in self._sector_perf.items():
            score = self._calculate_sector_score(sector, perf)
            scored.append((sector, round(score, 3), perf["stock_count"]))

        scored.sort(key=lambda x: x[1], reverse=True)
        hot = [item for item in scored[:top_n] if item[1] > 0]
        return hot

    def get_cold_sectors(self, top_n: int = 5) -> list[tuple[str, float, int]]:
        """약세 업종(하락 업종)을 점수 순으로 반환한다.

        Returns:
            [(sector_name, score, stock_count), ...] 점수 오름차순(가장 약한 것부터)
        """
        if not self._sector_perf:
            return []

        scored = []
        for sector, perf in self._sector_perf.items():
            score = self._calculate_sector_score(sector, perf)
            scored.append((sector, round(score, 3), perf["stock_count"]))

        scored.sort(key=lambda x: x[1])
        cold = [item for item in scored[:top_n] if item[1] < 0]
        return cold

    def _calculate_sector_score(self, sector: str, perf: dict) -> float:
        """업종의 종합 점수를 계산한다.

        점수 구성:
        - 당일 평균 등락률 (가중치 50%)
        - 상승 종목 비율 (가중치 20%)
        - 모멘텀 (히스토리 기반 3일/5일, 가중치 30%)
        """
        avg_rate = perf.get("avg_change_rate", 0)
        stock_count = perf.get("stock_count", 1)
        positive_count = perf.get("positive_count", 0)

        # 당일 등락률 점수 (정규화: +-5%를 +-1.0으로)
        daily_score = max(-1.0, min(1.0, avg_rate / 5.0))

        # 상승 종목 비율 점수 (-1 ~ +1, 50%가 0)
        positive_ratio = positive_count / max(stock_count, 1)
        breadth_score = (positive_ratio - 0.5) * 2

        # 모멘텀 점수 (히스토리 기반)
        momentum_score = self._calculate_momentum(sector)

        # 가중 합산
        score = (daily_score * 0.50) + (breadth_score * 0.20) + (momentum_score * 0.30)
        return score

    def _calculate_momentum(self, sector: str) -> float:
        """히스토리 기반 업종 모멘텀을 계산한다.

        최근 3일, 5일 평균 등락률을 기반으로 추세를 판단한다.

        Returns:
            -1.0 ~ +1.0 사이의 모멘텀 점수
        """
        if len(self._history) < 2:
            return 0.0

        # 날짜별 최신 데이터만 추출 (하루 여러 건이면 마지막 것)
        daily_data: dict[str, float] = {}
        for entry in self._history:
            date = entry.get("date", "")
            sectors = entry.get("sectors", {})
            if sector in sectors:
                daily_data[date] = sectors[sector].get("avg_change_rate", 0)

        if not daily_data:
            return 0.0

        # 최근 날짜순 정렬
        sorted_dates = sorted(daily_data.keys(), reverse=True)

        # 3일 모멘텀
        recent_3d = [daily_data[d] for d in sorted_dates[:3]]
        mom_3d = sum(recent_3d) / len(recent_3d) if recent_3d else 0

        # 5일 모멘텀
        recent_5d = [daily_data[d] for d in sorted_dates[:5]]
        mom_5d = sum(recent_5d) / len(recent_5d) if recent_5d else 0

        # 가속도: 3일 모멘텀이 5일보다 강하면 추세 가속
        acceleration = mom_3d - mom_5d

        # 정규화 (-1 ~ +1)
        momentum = max(-1.0, min(1.0, (mom_3d * 0.6 + mom_5d * 0.3 + acceleration * 0.1) / 3.0))
        return momentum

    # ──────────────────────────────────────────────
    # 업종 순환 분석
    # ──────────────────────────────────────────────

    def analyze_rotation(self) -> dict[str, Any]:
        """업종 순환(Sector Rotation) 종합 분석을 수행한다.

        Returns:
            {
                "timestamp": str,
                "sector_rankings": [...],       # 업종 순위 (점수 내림차순)
                "hot_sectors": [...],            # 강세 업종
                "cold_sectors": [...],           # 약세 업종
                "momentum": {...},               # 업종별 모멘텀
                "rotation_signal": str,          # 순환 신호
                "recommendations": [...],        # 추천 종목 (핫 섹터 기반)
            }
        """
        if not self._sector_perf:
            logger.warning("업종 데이터 미수집 - 빈 분석 결과 반환")
            return {
                "timestamp": datetime.now().isoformat(),
                "sector_rankings": [],
                "hot_sectors": [],
                "cold_sectors": [],
                "momentum": {},
                "rotation_signal": "데이터 없음",
                "recommendations": [],
            }

        # 1) 업종별 점수 & 순위
        rankings = []
        for sector, perf in self._sector_perf.items():
            score = self._calculate_sector_score(sector, perf)
            momentum = self._calculate_momentum(sector)
            rankings.append({
                "sector": sector,
                "sector_full_name": SECTOR_CODES.get(sector, sector),
                "score": round(score, 3),
                "avg_change_rate": perf["avg_change_rate"],
                "stock_count": perf["stock_count"],
                "positive_count": perf["positive_count"],
                "negative_count": perf["negative_count"],
                "max_rate": perf["max_rate"],
                "min_rate": perf["min_rate"],
                "top_gainer": perf["top_gainer"],
                "top_gainer_rate": perf["top_gainer_rate"],
                "momentum": round(momentum, 3),
            })

        rankings.sort(key=lambda x: x["score"], reverse=True)

        # 2) 핫/콜드 분류
        hot = self.get_hot_sectors()
        cold = self.get_cold_sectors()

        # 3) 업종별 모멘텀
        momentum_map = {}
        for r in rankings:
            momentum_map[r["sector"]] = r["momentum"]

        # 4) 순환 신호 판단
        rotation_signal = self._detect_rotation_signal(rankings)

        # 5) 추천 종목 (핫 섹터의 상승 종목)
        recommendations = self._generate_recommendations(hot)

        return {
            "timestamp": datetime.now().isoformat(),
            "last_update": self._last_update.isoformat() if self._last_update else None,
            "sector_rankings": rankings,
            "hot_sectors": [{"sector": s, "score": sc, "stock_count": c} for s, sc, c in hot],
            "cold_sectors": [{"sector": s, "score": sc, "stock_count": c} for s, sc, c in cold],
            "momentum": momentum_map,
            "rotation_signal": rotation_signal,
            "recommendations": recommendations,
        }

    def _detect_rotation_signal(self, rankings: list[dict]) -> str:
        """업종 순환 방향을 감지한다.

        Returns:
            "공격적 (성장주 강세)", "방어적 (가치주 강세)",
            "혼조세 (업종 분산)", "전면 강세", "전면 약세"
        """
        if not rankings:
            return "데이터 없음"

        scores = [r["score"] for r in rankings]
        avg_score = sum(scores) / len(scores)
        positive_sectors = sum(1 for s in scores if s > 0)
        total = len(scores)

        # 전면 강세/약세
        if positive_sectors >= total * 0.8 and avg_score > 0.1:
            return "전면 강세 (대부분 업종 상승)"
        if positive_sectors <= total * 0.2 and avg_score < -0.1:
            return "전면 약세 (대부분 업종 하락)"

        # 상위 업종의 특성으로 순환 방향 판단
        top_sectors = [r["sector"] for r in rankings[:3]]
        growth_sectors = {"반도체", "바이오", "2차전지", "IT", "인터넷", "게임"}
        defensive_sectors = {"은행", "보험", "통신", "음식료", "유통", "에너지"}

        growth_count = sum(1 for s in top_sectors if s in growth_sectors)
        defensive_count = sum(1 for s in top_sectors if s in defensive_sectors)

        if growth_count >= 2:
            return "공격적 순환 (성장주/테마주 강세)"
        if defensive_count >= 2:
            return "방어적 순환 (가치주/배당주 강세)"

        return "혼조세 (업종 간 순환 진행 중)"

    def _generate_recommendations(
        self, hot_sectors: list[tuple[str, float, int]]
    ) -> list[dict[str, Any]]:
        """핫 섹터 기반 추천 종목을 생성한다.

        핫 섹터에 속한 종목 중 당일 상승 중인 종목을 추천한다.

        Returns:
            [{"stock_code", "sector", "change_rate", "reason"}, ...]
        """
        recommendations = []

        for sector, sector_score, _ in hot_sectors:
            codes = self.get_sector_stocks(sector)
            sector_stocks = []

            for code in codes:
                if code in self._stock_data:
                    data = self._stock_data[code]
                    rate = data.get("change_rate", 0)
                    price = data.get("price", 0)
                    volume = data.get("volume", 0)

                    # 상승 중이고 가격과 거래량이 있는 종목만
                    if rate > 0 and price > 0 and volume > 0:
                        sector_stocks.append({
                            "stock_code": code,
                            "sector": sector,
                            "sector_score": sector_score,
                            "price": price,
                            "change_rate": rate,
                            "volume": volume,
                            "reason": f"핫섹터 [{sector}] (점수: {sector_score:.2f}) 소속 상승 종목",
                        })

            # 업종당 상위 3종목 (등락률 기준)
            sector_stocks.sort(key=lambda x: x["change_rate"], reverse=True)
            recommendations.extend(sector_stocks[:3])

        # 전체 추천에서 상위 10개
        recommendations.sort(key=lambda x: (x["sector_score"], x["change_rate"]), reverse=True)
        return recommendations[:10]

    # ──────────────────────────────────────────────
    # 통합 포인트: trader 연동
    # ──────────────────────────────────────────────

    def get_recommended_stocks(self, top_n: int = 10) -> list[str]:
        """핫 섹터 기반 추천 종목 코드 목록을 반환한다.

        trader에서 매수 후보 선정 시 이 메서드를 호출하여
        핫 섹터 종목을 우선 검토할 수 있다.

        Returns:
            추천 종목 코드 리스트 (최대 top_n개)
        """
        rotation = self.analyze_rotation()
        recs = rotation.get("recommendations", [])
        return [r["stock_code"] for r in recs[:top_n]]

    def get_sector_bias(self, stock_code: str) -> float:
        """종목이 속한 업종의 강도 보정값을 반환한다.

        매수/매도 판단 시 업종 강도를 반영하기 위한 보정값.
        핫 섹터 종목이면 양수, 콜드 섹터 종목이면 음수.

        Args:
            stock_code: 종목 코드

        Returns:
            -0.2 ~ +0.2 사이의 보정값
        """
        sector = self.get_sector_for_stock(stock_code)
        if sector == "기타" or sector not in self._sector_perf:
            return 0.0

        perf = self._sector_perf[sector]
        score = self._calculate_sector_score(sector, perf)

        # +-1.0 점수를 +-0.2 보정값으로 스케일링
        bias = max(-0.2, min(0.2, score * 0.2))
        return round(bias, 3)

    # ──────────────────────────────────────────────
    # 리포트
    # ──────────────────────────────────────────────

    def summary_report(self) -> str:
        """업종 분석 요약 리포트 문자열을 반환한다."""
        if not self._sector_perf:
            return "업종 데이터가 없습니다. update_sector_data()를 먼저 실행하세요."

        rotation = self.analyze_rotation()
        lines = [
            "=" * 56,
            f"  업종 순환 분석 ({datetime.now().strftime('%Y-%m-%d %H:%M')})",
            "=" * 56,
            "",
            f"  순환 신호: {rotation['rotation_signal']}",
            "",
        ]

        # 업종 순위
        lines.append("  [업종 순위]")
        lines.append(f"  {'순위':>4} {'업종':<10} {'점수':>7} {'등락률':>7} {'상승':>4} {'하락':>4}")
        lines.append("  " + "-" * 42)
        for i, r in enumerate(rotation["sector_rankings"], 1):
            lines.append(
                f"  {i:>4} {r['sector']:<10} {r['score']:>+7.3f} "
                f"{r['avg_change_rate']:>+6.2f}% {r['positive_count']:>4} {r['negative_count']:>4}"
            )
        lines.append("")

        # 핫 섹터
        hot = rotation["hot_sectors"]
        if hot:
            lines.append("  [강세 업종 (핫 섹터)]")
            for h in hot:
                lines.append(f"    {h['sector']} (점수: {h['score']:+.3f}, 종목: {h['stock_count']}개)")
            lines.append("")

        # 콜드 섹터
        cold = rotation["cold_sectors"]
        if cold:
            lines.append("  [약세 업종 (콜드 섹터)]")
            for c in cold:
                lines.append(f"    {c['sector']} (점수: {c['score']:+.3f}, 종목: {c['stock_count']}개)")
            lines.append("")

        # 추천 종목
        recs = rotation["recommendations"]
        if recs:
            lines.append("  [핫 섹터 추천 종목]")
            for r in recs[:5]:
                lines.append(
                    f"    {r['stock_code']} [{r['sector']}] "
                    f"등락률: {r['change_rate']:+.2f}%"
                )
            lines.append("")

        lines.append("=" * 56)
        return "\n".join(lines)
