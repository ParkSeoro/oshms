# OSHMS - 주식 자동 매매 시스템

한국투자증권 Open API 기반의 주식 자동 매매(단타/스캘핑) 시스템.

## 주요 기능

- **자동 매수/매도**: 기술적 분석 기반 자동 주문 실행
- **단타 전략**: 볼린저 밴드+RSI 스캘핑, 이동평균+거래량 모멘텀, 복합 전략
- **리스크 관리**: 손절/익절 자동 실행, 트레일링 스탑
- **수익률 분석**: 일별/종목별/시간대별 자동 분석 리포트
- **거래량 상위 자동 종목 선정**

## 프로젝트 구조

```
oshms/
├── main.py                 # CLI 진입점
├── config/
│   └── settings.py         # 환경변수 기반 설정 관리
├── api/
│   └── kis_api.py          # 한국투자증권 API 클라이언트
├── strategy/
│   ├── base.py             # 전략 기본 클래스 + 기술적 지표
│   ├── scalping.py         # 볼린저밴드+RSI 스캘핑 전략
│   ├── momentum.py         # 이동평균+거래량 모멘텀 전략
│   └── combined.py         # 복합 전략
├── trading/
│   ├── trader.py           # 자동 매매 엔진
│   └── order_manager.py    # 주문/포지션/리스크 관리
├── analysis/
│   └── analyzer.py         # 수익률 분석 및 리포트
├── utils/
│   └── logger.py           # 로깅
└── tests/                  # 단위 테스트
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

2. `.env` 파일에 한국투자증권 API 키를 입력:
```env
KIS_APP_KEY=발급받은_앱키
KIS_APP_SECRET=발급받은_앱시크릿
KIS_ACCOUNT_NO=계좌번호-01
KIS_MOCK=true
```

API 키는 [한국투자증권 API 포털](https://apiportal.koreainvestment.com)에서 발급.

## 사용법

### 자동 매매 실행

```bash
# 복합 전략으로 자동 종목 선정 + 매매
python main.py trade

# 특정 종목 지정 (삼성전자, SK하이닉스)
python main.py trade --stocks 005930,000660

# 스캘핑 전략, 5초 간격
python main.py trade --strategy scalping --interval 5

# 모멘텀 전략
python main.py trade --strategy momentum
```

### 수익률 리포트

```bash
python main.py report
python main.py report --csv
```

### 잔고 조회

```bash
python main.py balance
```

### 시스템 상태

```bash
python main.py status
```

## 매매 전략

### 1. 스캘핑 전략 (`scalping`)
- **매수**: 볼린저 밴드 하단 터치 + RSI 과매도 (< 30)
- **매도**: 볼린저 밴드 상단 터치 + RSI 과매수 (> 70)

### 2. 모멘텀 전략 (`momentum`)
- **매수**: 5일선/20일선 골든크로스 + 거래량 2배 이상 급증
- **매도**: 데드크로스 또는 하락추세 + MACD 음전

### 3. 복합 전략 (`combined`, 기본값)
- 스캘핑(60%) + 모멘텀(40%) 가중 평균
- 신호 강도 기반 최종 판단

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
