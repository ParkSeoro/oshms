# OSHMS v2.8 시스템 설계도

## 1. 시스템 개요

```
┌─────────────────────────────────────────────────────────────────┐
│                    OSHMS v2.8.0                                 │
│          AI Stock Trading System (한국투자증권 KIS API)          │
│                                                                 │
│   실행 모드:  CLI(trade/analyze/balance/report/status)          │
│              GUI(Tkinter 데스크톱)                               │
│              WEB(Flask 웹서버)                                   │
│                                                                 │
│   지원 시장: KR(한국) / NASD(나스닥) / NYSE / AMEX / SEHK / TKSE│
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. 디렉토리 구조

```
oshms/
├── main.py                     # 엔트리 포인트 (CLI 명령어 라우터)
│
├── config/
│   ├── settings.py             # Settings 데이터클래스 (.env 기반)
│   └── user_settings.json      # GUI 사용자 설정 (런타임 생성)
│
├── api/
│   └── kis_api.py              # KIS Open API 클라이언트
│
├── strategy/                   # 매매 전략 엔진
│   ├── base.py                 # BaseStrategy, Signal, SignalType
│   ├── technical.py            # TechnicalAnalyzer, TechnicalSnapshot
│   ├── patterns.py             # PatternRecognizer (26종 캔들 패턴)
│   ├── news_sentiment.py       # NewsSentimentAnalyzer (네이버 뉴스)
│   ├── market_context.py       # MarketContextAnalyzer (시장 레짐)
│   ├── expert.py               # ExpertStrategy (종합 전문가 전략)
│   ├── ai_analyst.py           # AIAnalyst (GPT/Claude 연동)
│   ├── scalping.py             # ScalpingStrategy
│   ├── momentum.py             # MomentumStrategy
│   └── combined.py             # CombinedStrategy
│
├── trading/                    # 매매 실행 엔진
│   ├── trader.py               # AutoTrader (자동매매 루프)
│   ├── order_manager.py        # OrderManager (주문/포지션/리스크)
│   └── state_manager.py        # StateManager (세션 영속성)
│
├── learning/                   # 자가학습/진화 엔진
│   ├── learner.py              # SelfLearner (거래 패턴 학습)
│   ├── optimizer.py            # StrategyOptimizer (파라미터 최적화)
│   ├── backtester.py           # Backtester (간이 백테스트)
│   └── evolution.py            # EvolutionEngine (자동 진화)
│
├── analysis/                   # 고급 분석 모듈
│   ├── analyzer.py             # ProfitAnalyzer (수익 리포트)
│   ├── orderbook.py            # OrderBookAnalyzer (호가창 분석)
│   ├── sector.py               # SectorAnalyzer (22개 업종 순환)
│   ├── backtester.py           # EnhancedBacktester (몬테카를로/워크포워드)
│   └── chart.py                # CandlestickChart (Tkinter 차트)
│
├── gui/
│   └── app.py                  # OshmsApp (Tkinter GUI, 5탭)
│
├── web/
│   ├── server.py               # Flask 웹서버
│   ├── static/                 # CSS/JS 정적 파일
│   └── templates/              # HTML 템플릿
│
├── utils/
│   └── logger.py               # setup_logger (콘솔+파일)
│
├── tests/                      # 테스트 모음
│   ├── test_strategy.py
│   ├── test_expert.py
│   ├── test_technical.py
│   ├── test_patterns.py
│   ├── test_news_sentiment.py
│   ├── test_order_manager.py
│   ├── test_analyzer.py
│   ├── test_settings.py
│   ├── test_learning.py
│   └── test_web.py
│
├── data/                       # 런타임 데이터 (git 무시)
│   ├── evolution_state.json    # 진화 엔진 상태
│   ├── trading_state.json      # 거래 세션 상태
│   └── stock_profiles.json     # 종목별 동적 임계값
│
├── logs/                       # 로그 (git 무시)
│   ├── trading.log             # 전체 로그
│   └── trades.json             # 거래 기록
│
├── .env                        # API 키/설정 (git 무시)
└── requirements.txt            # 의존성
```

---

## 3. 전체 아키텍처 다이어그램

```
┌──────────────────────────────────────────────────────────────────────┐
│                         사용자 인터페이스                              │
│  ┌──────────┐    ┌────────────────┐    ┌──────────────────────┐     │
│  │  CLI      │    │  GUI (Tkinter) │    │  WEB (Flask)         │     │
│  │ main.py   │    │  gui/app.py    │    │  web/server.py       │     │
│  │           │    │  5탭 구성       │    │  REST API + SSE      │     │
│  └─────┬─────┘    └──────┬─────────┘    └──────────┬───────────┘     │
│        │                 │                          │                │
└────────┼─────────────────┼──────────────────────────┼────────────────┘
         │                 │                          │
         ▼                 ▼                          ▼
┌──────────────────────────────────────────────────────────────────────┐
│                      자동매매 엔진 (trading/)                        │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │                    AutoTrader (trader.py)                    │    │
│  │                                                             │    │
│  │  ┌─────────────┐  ┌──────────────┐  ┌─────────────────┐   │    │
│  │  │ 종목 선정    │→ │ 전략 분석     │→ │ 주문 실행        │   │    │
│  │  │ (시장 스캔)  │  │ (Expert)     │  │ (OrderManager)  │   │    │
│  │  └─────────────┘  └──────────────┘  └─────────────────┘   │    │
│  │         │                │                    │            │    │
│  │         │                │                    ▼            │    │
│  │         │                │          ┌──────────────────┐   │    │
│  │         │                │          │ 리스크 관리       │   │    │
│  │         │                │          │ • 트레일링 스탑   │   │    │
│  │         │                │          │ • 목표가 매도     │   │    │
│  │         │                │          │ • 손절 (-3%)     │   │    │
│  │         │                │          │ • 장마감 처리     │   │    │
│  │         │                │          │ • 상승여력 재분석  │   │    │
│  │         │                │          └──────────────────┘   │    │
│  │         │                │                    │            │    │
│  │         ▼                ▼                    ▼            │    │
│  │  ┌───────────────────────────────────────────────┐        │    │
│  │  │             진화 엔진 연동                      │        │    │
│  │  │  매 15건 매도 → evolve() → 전략 조정 적용      │        │    │
│  │  └───────────────────────────────────────────────┘        │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                                                                      │
│  ┌─────────────────────┐  ┌───────────────────────────┐             │
│  │ StateManager        │  │ OrderManager              │             │
│  │ • 세션 영속성        │  │ • Position 추적            │             │
│  │ • 누적 통계          │  │ • 매수/매도 실행           │             │
│  │ • 이전 세션 복원     │  │ • 거래 기록 저장           │             │
│  └─────────────────────┘  └───────────────────────────┘             │
└──────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────────────────────────────┐
│                       전략 분석 엔진 (strategy/)                     │
│                                                                      │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │                ExpertStrategy (expert.py)                     │  │
│  │                                                               │  │
│  │  가중치 배분:                                                  │  │
│  │  ┌──────────┐ ┌──────────┐ ┌────────┐ ┌────────┐ ┌────────┐ │  │
│  │  │기술분석   │ │가격위치   │ │캔들패턴 │ │시장환경 │ │뉴스감성 │ │  │
│  │  │ 40%      │ │ 20%      │ │ 15%    │ │ 15%    │ │ 10%    │ │  │
│  │  └────┬─────┘ └────┬─────┘ └───┬────┘ └───┬────┘ └───┬────┘ │  │
│  │       │            │           │           │          │      │  │
│  │       ▼            ▼           ▼           ▼          ▼      │  │
│  │  ┌─────────────────────────────────────────────────────────┐ │  │
│  │  │              종합 점수 (-1.0 ~ +1.0)                    │ │  │
│  │  │                      │                                  │ │  │
│  │  │         ┌────────────┼────────────────┐                 │ │  │
│  │  │         ▼            ▼                ▼                 │ │  │
│  │  │   STRONG_BUY    BUY / HOLD     SELL / STRONG_SELL       │ │  │
│  │  │   (≥0.25)      (≥0.15)        (≤-0.08)                 │ │  │
│  │  └─────────────────────────────────────────────────────────┘ │  │
│  │                                                               │  │
│  │  종목별 동적 임계값:                                           │  │
│  │  • ATR<1% → 임계값 ×0.7 (빠른 진입)                          │  │
│  │  • ATR>3% → 임계값 ×1.5 (신중 진입)                          │  │
│  │  • 과거 거래 성과 기반 학습 (buy_adj, sell_adj)               │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                                                                      │
│  ┌──────────────────────┐  ┌──────────────────────────────────────┐ │
│  │ estimate_upside()    │  │ _calc_target_price()                 │ │
│  │ • MA 정배열 확인      │  │ • 볼린저 밴드 상단                    │ │
│  │ • MACD 상태 확인      │  │ • 저항선 가격대                       │ │
│  │ • RSI 과열 체크       │  │ • ATR × 추세강도 배수 (3~8x)         │ │
│  │ • BB 위치 분석        │  │ • 최소 목표: 5~8%                    │ │
│  │ • 거래량 추세         │  │ • 중간값 선택 (극값 제외)             │ │
│  │ • 스토캐스틱 크로스    │  │                                      │ │
│  │ → should_hold 판단    │  │                                      │ │
│  └──────────────────────┘  └──────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────────────────────────────┐
│                     기술적 분석 세부 (strategy/)                      │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────────┐│
│  │              TechnicalAnalyzer (technical.py)                   ││
│  │                                                                 ││
│  │  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐  ││
│  │  │ 추세 지표   │ │ 모멘텀     │ │ 변동성     │ │ 거래량     │  ││
│  │  │ SMA 5/20/60│ │ RSI(14)    │ │ ATR(14)    │ │ VWAP       │  ││
│  │  │ EMA 12/26  │ │ Stoch K/D  │ │ BB(20,2σ)  │ │ Volume비율  │  ││
│  │  │ MACD       │ │ Stoch크로스 │ │ BB위치/폭   │ │ OBV 추세   │  ││
│  │  │ MACD크로스  │ │            │ │            │ │            │  ││
│  │  │ 일목균형표  │ │            │ │            │ │            │  ││
│  │  └────────────┘ └────────────┘ └────────────┘ └────────────┘  ││
│  │                                                                 ││
│  │  ┌──────────────────────────────────────────────────────────┐  ││
│  │  │  지지/저항선 자동 탐지 (피봇 포인트 기반)                  │  ││
│  │  │  → 현재가 아래 지지선 3개 / 현재가 위 저항선 3개          │  ││
│  │  └──────────────────────────────────────────────────────────┘  ││
│  │                                                                 ││
│  │  종합 점수 (TechnicalSnapshot):                                ││
│  │  • trend_score    (-1 ~ +1): 추세 방향/강도                    ││
│  │  • momentum_score (-1 ~ +1): 과매수/과매도                     ││
│  │  • volatility_score (0 ~ 1): 변동성 수준                      ││
│  │  • volume_score   (-1 ~ +1): 매수/매도 압력                    ││
│  └─────────────────────────────────────────────────────────────────┘│
│                                                                      │
│  ┌────────────────────┐  ┌───────────────────┐  ┌────────────────┐ │
│  │ PatternRecognizer  │  │NewsSentimentAnalyzer│ │MarketContext   │ │
│  │ 26종 캔들 패턴:     │  │• 네이버 뉴스 크롤링 │ │Analyzer        │ │
│  │ • 망치형/교수형     │  │• 감성 사전 (긍/부정) │ │• KOSPI/KOSDAQ  │ │
│  │ • 장악형/잉태형     │  │• 키워드 빈도 분석   │ │• 시장 레짐 판단│ │
│  │ • 도지/십자형       │  │• key_headlines 추출 │ │• 시간대 적합도 │ │
│  │ • 샛별/저녁별       │  │• overall_score     │ │• 외국인/기관   │ │
│  │ • 쓰리솔저/크로우   │  │  (-1 ~ +1)         │ │  순매수 판단   │ │
│  └────────────────────┘  └───────────────────┘  └────────────────┘ │
└──────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────────────────────────────┐
│                     자가학습 / 진화 엔진 (learning/)                  │
│                                                                      │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │              EvolutionEngine (evolution.py)                    │  │
│  │                                                               │  │
│  │  진화 사이클 (매 15건 매도 후 실행):                            │  │
│  │                                                               │  │
│  │  ① SelfLearner.learn_from_trades()                           │  │
│  │     → 시간대별/종목별/패턴별 승률 분석                         │  │
│  │     → LearningInsight 생성 (category, confidence, action)     │  │
│  │                                                               │  │
│  │  ② _update_rules()                                           │  │
│  │     → 신뢰도 60%+ 인사이트 → 매매 규칙 변환                   │  │
│  │     → 최대 20개 규칙 유지 (저신뢰도 자동 폐기)                │  │
│  │                                                               │  │
│  │  ③ _adjust_weights()                                         │  │
│  │     → 매매 사유별 승률 분석                                    │  │
│  │     → 승률 60%+ 지표 가중치 +0.02, 35%- 지표 -0.02           │  │
│  │                                                               │  │
│  │  ④ StrategyOptimizer.optimize()                              │  │
│  │     → 그리드+랜덤 서치로 최적 파라미터 탐색                    │  │
│  │     → 백테스트 기반 검증                                       │  │
│  │                                                               │  │
│  │  ⑤ _evaluate_fitness()                                       │  │
│  │     → 적합도 = 승률(40%) + 수익팩터(30%)                      │  │
│  │                  + 거래빈도(15%) + 리스크(15%)                 │  │
│  │                                                               │  │
│  │  결과 → ExpertStrategy.apply_adjustments()                    │  │
│  │         가중치 + 매매 임계값 + 리스크 파라미터 동적 조정        │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                                                                      │
│  종목별 학습 (ExpertStrategy.learn_from_trade):                      │
│  • 거래 결과 → buy_adj / sell_adj 미세 조정                         │
│  • 학습률 0.005~0.03 (거래 횟수에 따라 감쇠)                        │
│  • 매수 후 손실 → 매수 기준 상향 / 매수 후 수익 → 기준 하향         │
│  • data/stock_profiles.json에 영속 저장                              │
└──────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────────────────────────────┐
│                       고급 분석 모듈 (analysis/)                     │
│                                                                      │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐  │
│  │ OrderBookAnalyzer │  │ SectorAnalyzer   │  │EnhancedBacktester│  │
│  │                  │  │                  │  │                  │  │
│  │ • 매수/매도 압력  │  │ • 22개 섹터 분류  │  │ • 한국 수수료    │  │
│  │ • 물량벽 감지     │  │ • 200+ 종목 매핑  │  │ • 슬리피지 모델  │  │
│  │   (avg × 3.0배)  │  │ • 모멘텀 스코어링 │  │ • 몬테카를로     │  │
│  │ • 스프레드 분석   │  │ • 핫/콜드 섹터    │  │ • 워크포워드     │  │
│  │ • 기관/세력 감지  │  │ • 업종 순환 패턴  │  │ • Sharpe/Sortino│  │
│  │                  │  │                  │  │ • Max Drawdown  │  │
│  └──────────────────┘  └──────────────────┘  └──────────────────┘  │
│                                                                      │
│  ┌──────────────────┐  ┌──────────────────────────────────────────┐ │
│  │ CandlestickChart │  │ AIAnalyst (strategy/ai_analyst.py)       │ │
│  │ Tkinter Canvas   │  │ • OpenAI GPT-4o / Claude 연동            │ │
│  │ • OHLC 봉차트    │  │ • 기술분석 데이터 → 자연어 리포트         │ │
│  │ • 거래량 바       │  │ • AI 판정 + 신뢰도 코멘트               │ │
│  │ • 이동평균선      │  │ • API 없으면 규칙 기반 응답              │ │
│  │ • BB 밴드        │  │                                          │ │
│  └──────────────────┘  └──────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────────────────────────────┐
│                        외부 API 연동 (api/)                          │
│                                                                      │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │               KISApi (api/kis_api.py)                         │  │
│  │                                                               │  │
│  │  인증: OAuth2 토큰 (자동 갱신)                                 │  │
│  │  HTTP: requests + urllib3 Retry (3회 재시도)                   │  │
│  │  URL: 모의=openapivts:29443 / 실전=openapi:9443               │  │
│  │                                                               │  │
│  │  ┌─── 국내 주식 ───────────────────────────────────────────┐  │  │
│  │  │ get_current_price(code)     현재가 조회                 │  │  │
│  │  │ get_minute_chart(code, n)   분봉 차트                   │  │  │
│  │  │ get_daily_chart(code, n)    일봉 차트                   │  │  │
│  │  │ get_balance()               잔고 조회                   │  │  │
│  │  │ buy_market_order(code, qty)  시장가 매수                │  │  │
│  │  │ sell_market_order(code, qty) 시장가 매도                │  │  │
│  │  │ get_orderbook(code)          호가 10단계                │  │  │
│  │  │ get_volume_rank(n)           거래량 상위                │  │  │
│  │  │ get_market_cap_rank(n)       거래대금 상위              │  │  │
│  │  │ get_fluctuation_rank(dir,n)  등락률 상위                │  │  │
│  │  └────────────────────────────────────────────────────────┘  │  │
│  │                                                               │  │
│  │  ┌─── 해외 주식 ───────────────────────────────────────────┐  │  │
│  │  │ get_overseas_price(mkt, code)    해외 현재가             │  │  │
│  │  │ get_overseas_daily_chart()       해외 일봉              │  │  │
│  │  │ get_overseas_balance()           해외 잔고              │  │  │
│  │  │ buy_overseas_order(mkt, code)    해외 매수              │  │  │
│  │  │ sell_overseas_order(mkt, code)   해외 매도              │  │  │
│  │  └────────────────────────────────────────────────────────┘  │  │
│  │                                                               │  │
│  │  tr_id 매핑:                                                  │  │
│  │  국내 모의: 매수=VTTC0802U  매도=VTTC0801U                    │  │
│  │  국내 실전: 매수=TTTC0802U  매도=TTTC0801U                    │  │
│  │  해외 모의: 매수=VTTT1002U  매도=VTTT1006U                    │  │
│  │  해외 실전: 매수=JTTT1002U  매도=JTTT1006U                    │  │
│  └───────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 4. GUI 탭 구성 (gui/app.py)

```
┌──────────────────────────────────────────────────────────────────┐
│  [O] OSHMS  v2.8.0  AI Trading          진화 #N   ● 매매중     │
│  ════════════════════════════════════════════════════════════════ │
│                                                                  │
│  ┌──────────┬──────────┬──────────┬──────────────┬───────┐      │
│  │ 대시보드  │ 자동매매  │ 종목분석  │ AI 어시스턴트 │ 설정  │      │
│  └──────────┴──────────┴──────────┴──────────────┴───────┘      │
│                                                                  │
│  [대시보드 탭]                                                    │
│  ├── 자산 현황 (5카드: 총자산, 손익, 수익률, 보유종목, 오늘거래)   │
│  ├── 누적 통계 (5카드: 총거래, 승률, 누적수익, 최고수익, 진화세대) │
│  ├── 진화 엔진 상태 (적합도 그래프, 활성 규칙)                    │
│  ├── 보유 종목 테이블 (종목명,수량,평균가,현재가,수익률,목표가,여력) │
│  ├── 최근 거래 테이블                                             │
│  ├── 새로고침 + 자동 갱신 (30초)                                  │
│  ├── 개발 로드맵 (버전별 DONE/TODO 뱃지)                          │
│  └── 해외 시장 거래시간 (6개 시장)                                 │
│                                                                  │
│  [자동매매 탭]                                                    │
│  ├── 마켓 선택 (KR/NASD/NYSE/AMEX/SEHK/TKSE)                    │
│  ├── 종목/전략/주기 설정                                          │
│  ├── 이전 세션 이어하기                                           │
│  ├── ▶ 시작 / ■ 중지 버튼                                        │
│  └── 실시간 매매 로그 (ScrolledText)                              │
│                                                                  │
│  [종목분석 탭]                                                    │
│  ├── 시장 + 30종목 드롭다운 (20 국내 + 10 해외)                   │
│  ├── 직접 입력 (코드/종목명)                                      │
│  ├── 섹터 빠른선택 칩 (반도체, 2차전지, 바이오, 인터넷 등 9개)     │
│  ├── AI 분석 / 중지 버튼                                          │
│  └── 분석 결과 (전문가분석 + 목표가 + 기술지표 + 뉴스 + AI리포트)  │
│                                                                  │
│  [AI 어시스턴트 탭]                                               │
│  ├── 빠른질문 칩 (포트폴리오, 시장전망, 전략추천, 리스크)          │
│  ├── 채팅 디스플레이 (사용자/AI 메시지)                           │
│  ├── OpenAI GPT-4o / Anthropic Claude / 규칙기반 자동 선택         │
│  └── 입력 필드 + 전송 버튼                                        │
│                                                                  │
│  [설정 탭]                                                        │
│  ├── 한국투자증권 API (Key, Secret, 계좌번호, 모의투자)           │
│  ├── 매매 설정 (금액, 종목수, 손절, 익절, 시간)                   │
│  ├── AI 설정 (OpenAI Key, Anthropic Key)                         │
│  ├── 네이버 검색 API (Client ID/Secret)                          │
│  ├── 자가학습 설정 (자동최적화, 자동진화, 학습주기)               │
│  └── 저장 / 검증 버튼                                             │
└──────────────────────────────────────────────────────────────────┘
```

---

## 5. 매매 사이클 흐름도

```
                    AutoTrader.start()
                         │
                         ▼
                 ┌──────────────┐
                 │ 매매시간 확인  │──No──→ 60초 대기
                 └──────┬───────┘
                        │ Yes
                        ▼
              ┌──────────────────┐
              │ 시장 컨텍스트 갱신 │ (Expert 모드, 60초 캐시)
              │ KOSPI/KOSDAQ 레짐 │
              └────────┬─────────┘
                       ▼
              ┌──────────────────┐
              │   종목 선정       │
              │ • 거래량 상위 30  │
              │ • 거래대금 상위 30│
              │ • 상승종목 20     │
              │ → 합산 스코어링   │
              │ → 상위 20개 선정  │
              │ (5분 캐시)        │
              └────────┬─────────┘
                       ▼
         ┌─────────────────────────┐
         │  종목별 분석 & 매매 루프  │
         │  for stock in stocks:   │
         └─────────┬───────────────┘
                   ▼
    ┌─────────────────────────────┐
    │     1. 가격 갱신             │
    │     2. 리스크 관리           │
    │        ├─ 상승여력 재분석    │ (3사이클마다)
    │        ├─ 트레일링 스탑      │ (목표가 여유 5%+면 유보)
    │        ├─ 목표가 도달 확인   │ → 자동 매도
    │        ├─ 손절 확인          │ (-3% 하드스탑)
    │        └─ 장마감 처리        │ (15:20, 5%+만 청산)
    │     3. 전략 분석             │
    └─────────┬───────────────────┘
              ▼
    ┌─────────────────────────────┐
    │  full_analysis() 실행       │
    │  ├─ 기술적 분석 (40%)       │
    │  ├─ 가격 위치 (20%)         │
    │  ├─ 캔들 패턴 (15%)         │
    │  ├─ 시장 환경 (15%)         │
    │  └─ 뉴스 감성 (10%)         │
    │  → total_score 계산         │
    │  → 종목별 동적 임계값 적용   │
    │  → decision 결정            │
    └─────────┬───────────────────┘
              ▼
    ┌─────────────────────────────────────────────────────┐
    │                   매매 실행 결정                      │
    │                                                     │
    │  ┌─── BUY 신호 ──────────────────────────────────┐  │
    │  │ • can_buy() 확인 (보유 < max_hold_count)       │  │
    │  │ • 중복 매수 방지                                │  │
    │  │ • 쿨다운 확인 (손절 후 15분)                    │  │
    │  │ • 신호 강도 ≥ 0.25 (Expert)                    │  │
    │  │ • 투자금 = max_buy_amount × 신호강도 비율       │  │
    │  │ • execute_buy() + 목표가/상승여력 설정          │  │
    │  └────────────────────────────────────────────────┘  │
    │                                                     │
    │  ┌─── SELL 신호 ─────────────────────────────────┐  │
    │  │ • 손실 중 → 회복 대기 (손절만 작동)             │  │
    │  │ • estimate_upside() 재분석                     │  │
    │  │   ├─ should_hold=True → 홀딩 유지              │  │
    │  │   └─ should_hold=False → 매도 실행             │  │
    │  │ • 목표가 도달 → 무조건 매도                     │  │
    │  │ • execute_sell() + 진화카운터 증가              │  │
    │  └────────────────────────────────────────────────┘  │
    └─────────────────────────────────────────────────────┘
              │
              ▼
    ┌─────────────────────────────┐
    │  4. 진화 체크 (5사이클마다)   │
    │  → 15건 매도 충족 시 진화    │
    │  → 전략 파라미터 조정 적용   │
    └─────────────────────────────┘
              │
              ▼
        interval초 대기 → 다음 사이클
```

---

## 6. 리스크 관리 체계

```
┌──────────────────────────────────────────────────────────────────┐
│                     리스크 관리 계층 구조                         │
│                                                                  │
│  ┌─── Layer 1: 진입 제어 ──────────────────────────────────────┐ │
│  │ • 최대 보유종목 제한 (max_hold_count, 기본 5)                │ │
│  │ • 1회 매수금액 제한 (max_buy_amount, 기본 50만원)           │ │
│  │ • 신호 강도 문턱값 (Expert ≥ 0.25)                          │ │
│  │ • 거래량 미달 시 매수 보류 (volume_ratio < 0.8)              │ │
│  │ • 장 시작 직후 변동성 구간 임계값 상향 (+0.10, 09:00~09:15)  │ │
│  │ • 종목가격 범위 필터 (2,000 ~ 500,000원)                     │ │
│  │ • 등락률 필터 (-5% ~ +8%)                                    │ │
│  │ • 하락장 시 매수기준 상향 (+0.12)                             │ │
│  └──────────────────────────────────────────────────────────────┘ │
│                                                                  │
│  ┌─── Layer 2: 포지션 관리 ────────────────────────────────────┐ │
│  │ • 목표가 설정 (BB상단, 저항선, ATR×배수 종합)                │ │
│  │ • 상승여력 모니터링 (3사이클마다 재분석)                      │ │
│  │ • 추세 소진 판단 → 자동 매도                                 │ │
│  │ • 목표가 상향만 허용 (하향 금지)                              │ │
│  └──────────────────────────────────────────────────────────────┘ │
│                                                                  │
│  ┌─── Layer 3: 수익 보호 ──────────────────────────────────────┐ │
│  │ 트레일링 스탑 (최고가 대비 하락폭):                           │ │
│  │   수익 1~5%:   3% 하락 시 매도                               │ │
│  │   수익 5~10%:  5% 하락 시 매도                               │ │
│  │   수익 10~20%: 7% 하락 시 매도                               │ │
│  │   수익 20%+:  10% 하락 시 매도                               │ │
│  │                                                              │ │
│  │ ※ 목표가 잔여 여력 5%+ 시 트레일링 유보                      │ │
│  │                                                              │ │
│  │ 강제 익절: 20% (ATR 기반 15~30%)                             │ │
│  └──────────────────────────────────────────────────────────────┘ │
│                                                                  │
│  ┌─── Layer 4: 손실 제한 ──────────────────────────────────────┐ │
│  │ • 하드 손절: -3% (무조건 매도)                                │ │
│  │ • 동적 손절: ATR × 2 (최소 -1.5%, 최대 settings값)           │ │
│  │ • 손절 후 쿨다운: 15분간 동일종목 재매수 금지                  │ │
│  └──────────────────────────────────────────────────────────────┘ │
│                                                                  │
│  ┌─── Layer 5: 장마감 처리 (15:20) ────────────────────────────┐ │
│  │ • 수익 5%+ → 수익 확정 (매도)                                │ │
│  │ • 손실 -3%+ → 손절 (매도)                                    │ │
│  │ • 소폭 수익/손실 → 오버나이트 보유 (내일 목표가 대기)          │ │
│  └──────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
```

---

## 7. 데이터 흐름 (영속 데이터)

```
┌──────────────────────────────────────────────────────────────────┐
│                       영속 데이터 파일                            │
│                                                                  │
│  .env                                                            │
│  ├── KIS_APP_KEY, KIS_APP_SECRET, KIS_ACCOUNT_NO                │
│  ├── KIS_MOCK (true/false)                                       │
│  ├── MAX_BUY_AMOUNT, MAX_HOLD_COUNT                             │
│  ├── STOP_LOSS_PCT, TAKE_PROFIT_PCT                             │
│  ├── TRADING_START_TIME, TRADING_END_TIME                        │
│  ├── OPENAI_API_KEY, ANTHROPIC_API_KEY                          │
│  └── NAVER_CLIENT_ID, NAVER_CLIENT_SECRET                       │
│                                                                  │
│  config/user_settings.json                                       │
│  ├── auto_optimize, auto_evolve, learn_interval                 │
│  ├── naver_client_id/secret                                      │
│  └── anthropic_api_key                                           │
│                                                                  │
│  logs/trades.json  ← OrderManager 기록                           │
│  [{"stock_code", "stock_name", "side", "quantity",               │
│    "price", "amount", "reason", "timestamp",                     │
│    "profit_loss", "profit_rate"}, ...]                           │
│                                                                  │
│  data/trading_state.json  ← StateManager 관리                    │
│  {"last_strategy", "last_market", "total_sessions",              │
│   "total_trades", "total_profit", "session_history", ...}        │
│                                                                  │
│  data/evolution_state.json  ← EvolutionEngine 관리               │
│  {"generation", "best_fitness", "fitness_history",               │
│   "active_rules", "weight_history", ...}                         │
│                                                                  │
│  data/stock_profiles.json  ← ExpertStrategy 관리                 │
│  {"005930": {"buy_adj": 0.01, "sell_adj": -0.005,               │
│              "trades": 15, "wins": 10, "total_profit": 2.5}, ..} │
│                                                                  │
│  logs/trading.log  ← 전체 시스템 로그                             │
└──────────────────────────────────────────────────────────────────┘
```

---

## 8. 핵심 데이터클래스 요약

```
┌─── config/settings.py ──────────────────────────────────────┐
│ Settings                                                     │
│   app_key, app_secret, account_no, is_mock                  │
│   max_buy_amount, max_hold_count                            │
│   stop_loss_pct, take_profit_pct                            │
│   trading_start_time, trading_end_time                      │
│   openai_api_key, openai_model                              │
│   base_url (auto: MOCK_URL / REAL_URL)                      │
│   validate() → list[str]                                     │
└──────────────────────────────────────────────────────────────┘

┌─── strategy/base.py ─────────────────────────────────────────┐
│ SignalType: BUY | SELL | HOLD                                │
│ Signal: signal_type, stock_code, reason, strength, target    │
│ BaseStrategy: analyze() → Signal  [abstract]                 │
│   + calc_sma(), calc_ema(), calc_rsi(), calc_bb(), calc_macd │
└──────────────────────────────────────────────────────────────┘

┌─── strategy/technical.py ────────────────────────────────────┐
│ TechnicalSnapshot:                                           │
│   추세: sma_5/20/60, ema_12/26, macd_*, ichimoku_*         │
│   모멘텀: rsi, stoch_k/d, stoch_cross                       │
│   변동성: atr, atr_pct, bb_upper/middle/lower/width/pos     │
│   거래량: vwap, volume_ratio, obv_trend                      │
│   지지/저항: support_levels, resistance_levels               │
│   종합: trend_score, momentum_score, volatility_score,       │
│         volume_score                                         │
└──────────────────────────────────────────────────────────────┘

┌─── strategy/expert.py ───────────────────────────────────────┐
│ ExpertAnalysis:                                              │
│   *_score (5개), total_score, confidence, decision, reasons  │
│   technical(TechnicalSnapshot), patterns, sentiment, ctx     │
│                                                              │
│ ExpertStrategy:                                              │
│   WEIGHTS: {technical:0.40, price_level:0.20, pattern:0.15,  │
│             market:0.15, sentiment:0.10}                     │
│   analyze() → Signal                                         │
│   full_analysis() → ExpertAnalysis                           │
│   estimate_upside() → {target_price, upside_pct, trend_alive,│
│                         momentum_score, should_hold, reason}  │
│   learn_from_trade() — 종목별 임계값 학습                      │
│   apply_adjustments() — 진화 엔진 결과 적용                    │
│   get_stock_thresholds() — ATR + 학습 기반 동적 임계값         │
└──────────────────────────────────────────────────────────────┘

┌─── trading/order_manager.py ─────────────────────────────────┐
│ Position:                                                    │
│   stock_code, stock_name, quantity, avg_price, buy_time      │
│   current_price, highest_price, signal_strength, atr_at_buy  │
│   target_price, estimated_upside                             │
│   profit_rate (property), profit_loss (property)             │
│                                                              │
│ TradeRecord:                                                 │
│   stock_code, stock_name, side, quantity, price, amount      │
│   reason, timestamp, profit_loss, profit_rate                │
│                                                              │
│ OrderManager:                                                │
│   positions: dict[str, Position]                             │
│   sync_positions(), update_prices()                          │
│   execute_buy(), execute_sell()                              │
│   check_stop_loss(), check_take_profit(), check_trailing_stop│
└──────────────────────────────────────────────────────────────┘

┌─── learning/evolution.py ────────────────────────────────────┐
│ EvolutionState:                                              │
│   generation, fitness_history, active_rules, weight_history  │
│   best_fitness, best_generation                              │
│                                                              │
│ EvolutionEngine:                                             │
│   EVOLUTION_INTERVAL = 15 (매도)                              │
│   evolve(trades, candles) → result                           │
│   get_strategy_adjustments() → dict                          │
│   should_evolve(count) → bool                                │
└──────────────────────────────────────────────────────────────┘
```

---

## 9. 의존성 그래프

```
main.py
 ├── config.settings.Settings
 ├── api.kis_api.KISApi
 ├── strategy.{Expert,Scalping,Momentum,Combined}Strategy
 ├── trading.trader.AutoTrader
 ├── analysis.analyzer.ProfitAnalyzer
 ├── gui.app.OshmsApp
 └── web.server.run_server

AutoTrader
 ├── KISApi
 ├── Settings
 ├── ExpertStrategy
 │    ├── TechnicalAnalyzer
 │    ├── PatternRecognizer
 │    ├── NewsSentimentAnalyzer
 │    └── MarketContextAnalyzer ← KISApi
 ├── OrderManager ← KISApi, Settings
 ├── StateManager
 └── EvolutionEngine
      ├── SelfLearner
      │    ├── Backtester
      │    └── StrategyOptimizer
      └── StrategyOptimizer

OshmsApp (GUI)
 ├── Settings
 ├── AutoTrader (trading thread)
 ├── ExpertStrategy (analysis)
 ├── StateManager
 └── AIAnalyst (chat, OpenAI/Claude)
```

---

## 10. 버전 히스토리

| 버전 | 주요 변경 |
|------|----------|
| v2.4 | AI 분석 (OpenAI/Claude), 해외 주식, 자동 진화 엔진 |
| v2.5 | 소프트 다크 테마, AI 어시스턴트 채팅, 동적 리스크(ATR) |
| v2.6 | 진화 엔진 완전체 (실제 학습 적용), 전체 시장 스캔, 프리미엄 테마 v2 |
| v2.7 | 분석 기반 목표가 매도, 호가창 분석, 업종 순환, 강화 백테스터, 차트 엔진 |
| v2.8 | 프리미엄 다크 테마 v3 (Bloomberg/Arc), 종목분석 드롭다운 + 섹터 빠른선택 |

---

## 11. 향후 로드맵

| 버전 | 계획 |
|------|------|
| v2.9 | 텔레그램 알림 봇, 포트폴리오 리밸런싱 |
| v3.0 | 안드로이드 앱 (PWA), 멀티 계좌 지원 |
