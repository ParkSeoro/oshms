# OSHMS - 주식 자동 매매 시스템

한국투자증권 Open API 기반의 전문가 수준 주식 자동 매매(단타/스캘핑) 시스템.

## 주요 기능

- **전문가 모드 자동 매매**: 기술적 분석 + 캔들 패턴 + 뉴스 감성 + 시장 레짐 종합 판단
- **고급 기술적 분석**: VWAP, Stochastic, ATR, 일목균형표, MACD, 볼린저밴드, 지지/저항선
- **캔들스틱 패턴 인식**: 도지, 망치형, 장악형, 모닝스타, 적삼병 등 26종 패턴 자동 감지
- **뉴스 감성 분석**: 네이버 뉴스 크롤링 + 주식 특화 감성 사전으로 실시간 분석
- **시장 레짐 감지**: 추세/횡보/급변 자동 판별, 시간대별 매매 적합도 반영
- **리스크 관리**: 손절/익절/트레일링 스탑, 시장 상황 따라 동적 조정
- **수익률 자동 분석**: 일별/종목별/시간대별 상세 리포트

## 프로젝트 구조

```
oshms/
├── main.py                     # CLI 진입점
├── config/
│   └── settings.py             # 환경변수 기반 설정 관리
├── api/
│   └── kis_api.py              # 한국투자증권 API 클라이언트
├── strategy/
│   ├── base.py                 # 전략 기본 클래스 + 기본 지표
│   ├── technical.py            # 고급 기술적 분석 (VWAP, 스토캐스틱, ATR, 일목균형표)
│   ├── patterns.py             # 캔들스틱 패턴 인식 (26종)
│   ├── news_sentiment.py       # 뉴스 감성 분석
│   ├── market_context.py       # 시장 레짐/시간대 분석
│   ├── expert.py               # 전문가 종합 전략
│   ├── scalping.py             # 볼린저+RSI 스캘핑 전략
│   ├── momentum.py             # 이동평균+거래량 모멘텀 전략
│   └── combined.py             # 복합 전략
├── trading/
│   ├── trader.py               # 자동 매매 엔진
│   └── order_manager.py        # 주문/포지션/리스크 관리
├── analysis/
│   └── analyzer.py             # 수익률 분석 및 리포트
├── utils/
│   └── logger.py               # 로깅
└── tests/                      # 단위 테스트 (95개)
```

## 설치

```bash
pip install -r requirements.txt
```

## 설정

1. `.env.example`을 `.env`로 복사:
```bash
cp .env.example .env
```

2. `.env` 파일에 API 키를 입력:
```env
KIS_APP_KEY=발급받은_앱키
KIS_APP_SECRET=발급받은_앱시크릿
KIS_ACCOUNT_NO=계좌번호-01
KIS_MOCK=true

# 뉴스 분석 (선택 - 없어도 웹 크롤링으로 동작)
NAVER_CLIENT_ID=네이버_클라이언트_ID
NAVER_CLIENT_SECRET=네이버_클라이언트_시크릿
```

- 한국투자증권 API: [KIS Developers](https://apiportal.koreainvestment.com)
- 네이버 검색 API: [Naver Developers](https://developers.naver.com) (선택사항)

## 사용법

### 자동 매매 실행

```bash
# 전문가 모드 (기본값) - 모든 분석 모듈 통합
python main.py trade

# 특정 종목 지정
python main.py trade --stocks 005930,000660

# 전략 선택
python main.py trade --strategy expert      # 전문가 종합 (기본)
python main.py trade --strategy scalping    # 볼린저+RSI
python main.py trade --strategy momentum    # 이동평균+거래량
python main.py trade --strategy combined    # 스캘핑+모멘텀

# 주기 조절
python main.py trade --interval 5
```

### 종목 전문가 분석 (매매 없이)

```bash
python main.py analyze 005930 --name 삼성전자
python main.py analyze 000660 --name SK하이닉스
```

출력 예시:
```
══ 삼성전자(005930) 전문가 분석 ══
현재가: 72,000원 | 판정: BUY
종합점수: +0.382 (신뢰도: 68%)
  기술분석: +0.245
  패턴분석: +0.400
  뉴스감성: +0.350
  시장환경: +0.120
  가격위치: +0.200
근거:
  • RSI 과매도(28)
  • MACD 골든크로스
  • 패턴: 상승장악형(▲, 신뢰도=80%)
  • 뉴스감성: 긍정(+0.35, 12건)
  • 시장: trending_up(KOSPI=+1.23%)
```

### 수익률 리포트

```bash
python main.py report
python main.py report --csv
```

### 잔고 / 상태

```bash
python main.py balance
python main.py status
```

## 전문가 전략 분석 체계

### 분석 가중치

| 분석 모듈 | 가중치 | 내용 |
|-----------|--------|------|
| 기술적 분석 | 35% | SMA/EMA, MACD, RSI, 스토캐스틱, 볼린저, VWAP, ATR, 일목균형표 |
| 뉴스 감성 | 20% | 네이버 뉴스 크롤링 + 주식 특화 감성 사전 분석 |
| 캔들 패턴 | 15% | 도지, 망치형, 장악형, 모닝/이브닝스타, 적삼병/흑삼병 등 |
| 시장 환경 | 15% | KOSPI/KOSDAQ 추세, 레짐 감지, 시간대별 적합도 |
| 가격 위치 | 15% | 볼린저 위치, VWAP 대비, 지지/저항선 근접도 |

### 캔들스틱 패턴 (26종)

**단일 캔들**: 도지, 잠자리도지, 비석도지, 망치형, 교수형, 역망치형, 슈팅스타, 마루보즈, 스피닝탑

**이중 캔들**: 상승/하락 장악형, 관통형, 먹구름형, 상승/하락 하라미, 트위저 탑/바텀

**삼중 캔들**: 모닝스타, 이브닝스타, 적삼병, 흑삼병

### 시장 레짐별 자동 조정

| 레짐 | 매수 기준 | 포지션 크기 | 손절 |
|------|-----------|-------------|------|
| 상승장 | 적극적 (-5%) | 120% | 여유 (-0.5%) |
| 하락장 | 보수적 (+15%) | 60% | 타이트 (+0.5%) |
| 급변장 | 보수적 (+10%) | 50% | 넓게 (+1.0%) |
| 횡보장 | 스캘핑 유리 (-5%) | 100% | 기본 |

## 리스크 관리

| 항목 | 기본값 | 설명 |
|------|--------|------|
| 손절 | -2.0% | 평균 매수가 대비 하락 시 자동 매도 |
| 익절 | +3.0% | 평균 매수가 대비 상승 시 자동 매도 |
| 트레일링 스탑 | 1.5% | 최고가 대비 하락 시 수익 확보 매도 |
| 최대 보유 종목 | 5개 | 분산 투자 제한 |
| 1회 최대 매수 | 50만원 | 종목당 투자 한도 |

## 테스트

```bash
python -m unittest discover -s tests -v
```

## 주의사항

- 모의투자(`KIS_MOCK=true`)로 충분히 테스트 후 실전투자 전환
- 투자 손실에 대한 책임은 사용자에게 있음
- 장중(09:05~15:10)에만 매매 실행
