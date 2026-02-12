"""OSHMS 데스크톱 GUI 앱.

Tkinter 기반 메인 윈도우. 현대적 다크 테마 디자인.
탭 구성:
  - 대시보드: 보유 현황, 누적 통계, 자동 갱신
  - 자동매매: 시작/중지, 실시간 로그, 해외주식 지원
  - 종목분석: 전문가 분석 + AI 리포트 (국내/해외)
  - 설정: API키, 매매 파라미터, AI 설정, 테마
"""

import json
import threading
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
from pathlib import Path

from config.settings import Settings


class OshmsApp:
    """메인 데스크톱 애플리케이션."""

    SETTINGS_FILE = Path("config/user_settings.json")

    # 현대적 다크 테마 컬러 (Catppuccin Mocha 기반)
    THEME_COLORS = {
        "dark": {
            "bg": "#0f0f23",
            "bg_surface": "#1a1a36",
            "fg": "#e2e4f4",
            "dim": "#6c7086",
            "accent": "#7c9cff",
            "accent2": "#a78bfa",
            "green": "#34d399",
            "red": "#f87171",
            "yellow": "#fbbf24",
            "card": "#181830",
            "card_border": "#2a2a4a",
            "input_bg": "#22223a",
            "button": "#3a3a5a",
            "button_text": "#e2e4f4",
            "header_bg": "#0a0a1a",
            "tab_active": "#7c9cff",
            "tab_inactive": "#3a3a5a",
        },
        "light": {
            "bg": "#f0f2f8",
            "bg_surface": "#ffffff",
            "fg": "#2d3048",
            "dim": "#8890a8",
            "accent": "#5b6abf",
            "accent2": "#7c5cbf",
            "green": "#22a06b",
            "red": "#d32f2f",
            "yellow": "#e67e22",
            "card": "#ffffff",
            "card_border": "#dde0ea",
            "input_bg": "#eef0f6",
            "button": "#dde0ea",
            "button_text": "#2d3048",
            "header_bg": "#e8eaf4",
            "tab_active": "#5b6abf",
            "tab_inactive": "#c8cce0",
        },
    }

    MARKETS = {
        "KR": "KR 한국",
        "NASD": "NASD 나스닥",
        "NYSE": "NYSE 뉴욕",
        "AMEX": "AMEX",
        "SEHK": "SEHK 홍콩",
        "TKSE": "TKSE 일본",
    }

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("OSHMS - AI 주식 자동매매 시스템")
        self.root.geometry("1150x850")
        self.root.minsize(950, 700)

        self.settings = Settings.from_env()
        self.user_prefs = self._load_user_prefs()
        self.theme = self.user_prefs.get("theme", "dark")
        self.c = self.THEME_COLORS[self.theme]

        self._trader = None
        self._trading_thread = None
        self._is_trading = False
        self._auto_refresh_id = None
        self._state_mgr = None
        self._evolution = None

        self._build_ui()
        self._apply_theme()
        self._load_settings_to_ui()
        self._load_state_to_dashboard()

    def run(self):
        """앱을 실행한다."""
        self.root.mainloop()

    # ─────────────────── UI 빌드 ───────────────────

    def _build_ui(self):
        c = self.c

        # 헤더
        self.header = tk.Frame(self.root, height=56)
        self.header.pack(fill=tk.X)
        self.header.pack_propagate(False)

        # 로고 영역
        logo_frame = tk.Frame(self.header)
        logo_frame.pack(side=tk.LEFT, padx=18, pady=10)

        self.logo_badge = tk.Label(
            logo_frame, text=" O ", font=("Helvetica", 13, "bold"),
            fg="#ffffff", padx=4, pady=2,
        )
        self.logo_badge.pack(side=tk.LEFT, padx=(0, 8))

        self.title_label = tk.Label(
            logo_frame, text="OSHMS", font=("Helvetica", 17, "bold"),
        )
        self.title_label.pack(side=tk.LEFT)

        self.subtitle_label = tk.Label(
            logo_frame, text="AI Trading System", font=("Helvetica", 9),
        )
        self.subtitle_label.pack(side=tk.LEFT, padx=(8, 0), pady=(3, 0))

        # 상태 영역 (오른쪽)
        status_frame = tk.Frame(self.header)
        status_frame.pack(side=tk.RIGHT, padx=18, pady=10)

        self.evo_label = tk.Label(status_frame, text="", font=("Helvetica", 9))
        self.evo_label.pack(side=tk.LEFT, padx=(0, 12))

        self.status_dot = tk.Label(status_frame, text="●", font=("", 8))
        self.status_dot.pack(side=tk.LEFT, padx=(0, 4))

        self.status_label = tk.Label(status_frame, text="대기중", font=("Helvetica", 11, "bold"))
        self.status_label.pack(side=tk.LEFT)

        # 구분선
        self.header_line = tk.Frame(self.root, height=1)
        self.header_line.pack(fill=tk.X)

        # 탭 노트북 (커스텀 스타일)
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=0, pady=0)

        self._build_dashboard_tab()
        self._build_trading_tab()
        self._build_analysis_tab()
        self._build_settings_tab()

    # ── 대시보드 탭 ──

    def _build_dashboard_tab(self):
        frame = tk.Frame(self.notebook)
        self.notebook.add(frame, text="   대시보드   ")

        # 스크롤 가능한 컨테이너
        canvas = tk.Canvas(frame, highlightthickness=0)
        scrollbar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=canvas.yview)
        inner = tk.Frame(canvas)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # 메인 자산 카드
        cards_frame = tk.Frame(inner)
        cards_frame.pack(fill=tk.X, padx=20, pady=(16, 8))

        self.card_labels = {}
        card_items = [
            ("total_asset", "총 자산", "-- 원", "accent"),
            ("total_profit", "총 손익", "-- 원", "green"),
            ("profit_rate", "수익률", "--", "accent2"),
            ("hold_count", "보유 종목", "0 개", "yellow"),
            ("today_trades", "오늘 거래", "0 건", "dim"),
        ]
        for i, (key, title, default, color_key) in enumerate(card_items):
            card = tk.Frame(cards_frame, padx=18, pady=14)
            card.grid(row=0, column=i, padx=5, sticky="nsew")
            cards_frame.columnconfigure(i, weight=1)

            tk.Label(card, text=title, font=("Helvetica", 10)).pack(anchor=tk.W)
            lbl = tk.Label(card, text=default, font=("Helvetica", 16, "bold"))
            lbl.pack(anchor=tk.W, pady=(5, 0))
            self.card_labels[key] = lbl
            self.card_labels[f"{key}_card"] = card

        # 누적 통계
        stats_frame = tk.Frame(inner)
        stats_frame.pack(fill=tk.X, padx=20, pady=(4, 8))

        self.stat_labels = {}
        stat_items = [
            ("cum_trades", "누적 거래", "0 건"),
            ("cum_wins", "승률", "--"),
            ("cum_profit", "누적 수익", "0 원"),
            ("best_trade", "최고 수익", "0 원"),
            ("evo_gen", "진화 세대", "#0"),
        ]
        for i, (key, title, default) in enumerate(stat_items):
            card = tk.Frame(stats_frame, padx=14, pady=10)
            card.grid(row=0, column=i, padx=4, sticky="nsew")
            stats_frame.columnconfigure(i, weight=1)
            tk.Label(card, text=title, font=("Helvetica", 9)).pack(anchor=tk.W)
            lbl = tk.Label(card, text=default, font=("Helvetica", 13, "bold"))
            lbl.pack(anchor=tk.W, pady=(3, 0))
            self.stat_labels[key] = lbl
            self.stat_labels[f"{key}_card"] = card

        # 보유 종목 테이블
        holdings_header = tk.Frame(inner)
        holdings_header.pack(fill=tk.X, padx=20, pady=(12, 4))
        tk.Label(holdings_header, text="보유 종목", font=("Helvetica", 13, "bold")).pack(side=tk.LEFT)
        self.holdings_count_lbl = tk.Label(holdings_header, text="0 종목", font=("Helvetica", 10))
        self.holdings_count_lbl.pack(side=tk.RIGHT)

        cols = ("종목명", "시장", "수량", "평균가", "현재가", "손익", "수익률")
        tree_frame = tk.Frame(inner)
        tree_frame.pack(fill=tk.BOTH, padx=20, pady=(0, 8))
        self.holdings_tree = ttk.Treeview(tree_frame, columns=cols, show="headings", height=6)
        for col in cols:
            self.holdings_tree.heading(col, text=col)
            w = 130 if col == "종목명" else 65 if col == "시장" else 90
            self.holdings_tree.column(col, width=w, anchor=tk.E if col not in ("종목명", "시장") else tk.W)
        hsb = ttk.Scrollbar(tree_frame, orient=tk.HORIZONTAL, command=self.holdings_tree.xview)
        self.holdings_tree.configure(xscrollcommand=hsb.set)
        self.holdings_tree.pack(fill=tk.BOTH, expand=True)
        hsb.pack(fill=tk.X)

        # 최근 거래
        tk.Label(inner, text="최근 거래", font=("Helvetica", 13, "bold")).pack(
            anchor=tk.W, padx=20, pady=(8, 4),
        )
        trade_cols = ("시간", "종목", "매매", "수량", "가격", "손익")
        trade_frame = tk.Frame(inner)
        trade_frame.pack(fill=tk.BOTH, padx=20, pady=(0, 8))
        self.trades_tree = ttk.Treeview(trade_frame, columns=trade_cols, show="headings", height=4)
        for col in trade_cols:
            self.trades_tree.heading(col, text=col)
            w = 140 if col == "시간" else 110 if col == "종목" else 75
            self.trades_tree.column(col, width=w, anchor=tk.E if col in ("수량", "가격", "손익") else tk.W)
        self.trades_tree.pack(fill=tk.BOTH, expand=True)

        # 하단 버튼
        btn_frame = tk.Frame(inner)
        btn_frame.pack(fill=tk.X, padx=20, pady=(4, 16))
        self.refresh_btn = tk.Button(
            btn_frame, text="  새로고침  ", command=self._refresh_balance,
            font=("Helvetica", 10, "bold"), relief=tk.FLAT, padx=16, pady=6,
            cursor="hand2",
        )
        self.refresh_btn.pack(side=tk.LEFT)
        self.auto_refresh_var = tk.BooleanVar(value=True)
        tk.Checkbutton(btn_frame, text="자동 갱신 (30초)", variable=self.auto_refresh_var,
                       font=("Helvetica", 10)).pack(side=tk.LEFT, padx=12)

    # ── 자동매매 탭 ──

    def _build_trading_tab(self):
        frame = tk.Frame(self.notebook)
        self.notebook.add(frame, text="   자동매매   ")

        # 설정 영역
        settings_card = tk.Frame(frame, padx=20, pady=16)
        settings_card.pack(fill=tk.X, padx=20, pady=(16, 8))

        # 시장 선택
        market_row = tk.Frame(settings_card)
        market_row.pack(fill=tk.X, pady=(0, 10))
        tk.Label(market_row, text="마켓", font=("Helvetica", 11, "bold")).pack(side=tk.LEFT, padx=(0, 10))
        self.market_var = tk.StringVar(value="KR")
        market_combo = ttk.Combobox(
            market_row, textvariable=self.market_var, width=16,
            values=list(self.MARKETS.values()), state="readonly",
        )
        market_combo.set("KR 한국")
        market_combo.pack(side=tk.LEFT, padx=5)

        # 종목/전략/주기
        ctrl_row = tk.Frame(settings_card)
        ctrl_row.pack(fill=tk.X, pady=(0, 10))

        tk.Label(ctrl_row, text="종목:", font=("Helvetica", 10)).pack(side=tk.LEFT)
        self.stocks_entry = tk.Entry(ctrl_row, width=22, font=("Helvetica", 10))
        self.stocks_entry.pack(side=tk.LEFT, padx=5)
        self.stocks_entry.insert(0, "자동선정")

        tk.Label(ctrl_row, text="전략:", font=("Helvetica", 10)).pack(side=tk.LEFT, padx=(12, 0))
        self.strategy_var = tk.StringVar(value="expert")
        ttk.Combobox(
            ctrl_row, textvariable=self.strategy_var, width=10,
            values=["expert", "scalping", "momentum", "combined"],
            state="readonly",
        ).pack(side=tk.LEFT, padx=5)

        tk.Label(ctrl_row, text="주기(초):", font=("Helvetica", 10)).pack(side=tk.LEFT, padx=(12, 0))
        self.interval_var = tk.StringVar(value="10")
        tk.Entry(ctrl_row, textvariable=self.interval_var, width=4, font=("Helvetica", 10)).pack(side=tk.LEFT, padx=5)

        # 이전 세션 복원
        resume_frame = tk.Frame(settings_card)
        resume_frame.pack(fill=tk.X, pady=(0, 8))
        self.resume_label = tk.Label(resume_frame, text="", font=("Helvetica", 9))
        self.resume_label.pack(side=tk.LEFT)
        self.resume_btn = tk.Button(
            resume_frame, text="이전 세션 이어하기", command=self._resume_trading,
            font=("Helvetica", 9), relief=tk.FLAT, padx=10, pady=2, cursor="hand2",
        )

        # 시작/중지 버튼
        btn_frame = tk.Frame(settings_card)
        btn_frame.pack(fill=tk.X, pady=(4, 0))

        self.start_btn = tk.Button(
            btn_frame, text="  자동매매 시작  ", font=("Helvetica", 12, "bold"),
            command=self._start_trading, relief=tk.FLAT, padx=24, pady=10,
            cursor="hand2",
        )
        self.start_btn.pack(side=tk.LEFT, padx=(0, 10))

        self.stop_btn = tk.Button(
            btn_frame, text="  중지  ", font=("Helvetica", 11),
            command=self._stop_trading, relief=tk.FLAT, padx=16, pady=10,
            state=tk.DISABLED, cursor="hand2",
        )
        self.stop_btn.pack(side=tk.LEFT)

        # 실시간 로그
        log_header = tk.Frame(frame)
        log_header.pack(fill=tk.X, padx=20, pady=(12, 4))
        tk.Label(log_header, text="매매 로그", font=("Helvetica", 12, "bold")).pack(side=tk.LEFT)

        self.log_text = scrolledtext.ScrolledText(
            frame, height=16, font=("Consolas", 10), wrap=tk.WORD,
        )
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 16))

    # ── 종목분석 탭 ──

    def _build_analysis_tab(self):
        frame = tk.Frame(self.notebook)
        self.notebook.add(frame, text="   종목분석   ")

        # 검색 카드
        search_card = tk.Frame(frame, padx=20, pady=16)
        search_card.pack(fill=tk.X, padx=20, pady=(16, 8))

        search_row = tk.Frame(search_card)
        search_row.pack(fill=tk.X)

        tk.Label(search_row, text="시장:", font=("Helvetica", 10)).pack(side=tk.LEFT)
        self.analyze_market_var = tk.StringVar(value="KR")
        ttk.Combobox(
            search_row, textvariable=self.analyze_market_var, width=10,
            values=["KR", "NASD", "NYSE", "AMEX", "SEHK", "TKSE"], state="readonly",
        ).pack(side=tk.LEFT, padx=5)

        tk.Label(search_row, text="코드:", font=("Helvetica", 10)).pack(side=tk.LEFT, padx=(12, 0))
        self.analyze_code = tk.Entry(search_row, width=10, font=("Helvetica", 10))
        self.analyze_code.pack(side=tk.LEFT, padx=5)
        self.analyze_code.insert(0, "005930")

        tk.Label(search_row, text="종목명:", font=("Helvetica", 10)).pack(side=tk.LEFT, padx=(12, 0))
        self.analyze_name = tk.Entry(search_row, width=12, font=("Helvetica", 10))
        self.analyze_name.pack(side=tk.LEFT, padx=5)
        self.analyze_name.insert(0, "삼성전자")

        self.analyze_btn = tk.Button(
            search_row, text="  AI 분석  ", command=self._run_analysis,
            font=("Helvetica", 10, "bold"), relief=tk.FLAT, padx=14, pady=4,
            cursor="hand2",
        )
        self.analyze_btn.pack(side=tk.LEFT, padx=10)

        # 힌트
        hint = tk.Label(
            search_card,
            text="예) KR: 005930(삼성전자) | NASD: AAPL(애플), TSLA(테슬라) | NYSE: BRK.B | SEHK: 00700",
            font=("Helvetica", 9),
        )
        hint.pack(anchor=tk.W, pady=(8, 0))

        # 분석 결과
        self.analysis_text = scrolledtext.ScrolledText(
            frame, height=28, font=("Consolas", 10), wrap=tk.WORD,
        )
        self.analysis_text.pack(fill=tk.BOTH, expand=True, padx=20, pady=(4, 16))

    # ── 설정 탭 ──

    def _build_settings_tab(self):
        frame = tk.Frame(self.notebook)
        self.notebook.add(frame, text="   설정   ")

        canvas = tk.Canvas(frame, highlightthickness=0)
        scrollbar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=canvas.yview)
        inner = tk.Frame(canvas)

        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.setting_vars = {}
        row = 0

        # API
        row = self._add_section(inner, "한국투자증권 API", row)
        row = self._add_setting(inner, "APP Key", "app_key", row, show="*")
        row = self._add_setting(inner, "APP Secret", "app_secret", row, show="*")
        row = self._add_setting(inner, "계좌번호 (예: 12345678-01)", "account_no", row)
        row = self._add_checkbox(inner, "모의투자 모드", "is_mock", row)

        # 매매
        row = self._add_section(inner, "매매 설정", row)
        row = self._add_setting(inner, "1회 최대 매수금액 (원)", "max_buy_amount", row)
        row = self._add_setting(inner, "최대 보유 종목 수", "max_hold_count", row)
        row = self._add_setting(inner, "손절 비율 (%)", "stop_loss_pct", row)
        row = self._add_setting(inner, "익절 비율 (%)", "take_profit_pct", row)
        row = self._add_setting(inner, "매매 시작 시간", "trading_start_time", row)
        row = self._add_setting(inner, "매매 종료 시간", "trading_end_time", row)

        # AI 분석
        row = self._add_section(inner, "AI 분석 설정", row)
        row = self._add_setting(inner, "OpenAI API Key", "openai_api_key", row, show="*")
        row = self._add_setting(inner, "Anthropic API Key", "anthropic_api_key", row, show="*")

        # 네이버 API
        row = self._add_section(inner, "네이버 검색 API (선택)", row)
        row = self._add_setting(inner, "Client ID", "naver_client_id", row)
        row = self._add_setting(inner, "Client Secret", "naver_client_secret", row, show="*")

        # 화면 설정
        row = self._add_section(inner, "화면 설정", row)
        row = self._add_radio(inner, "테마", "theme", ["dark", "light"], ["다크 모드", "라이트 모드"], row)

        # 자가학습
        row = self._add_section(inner, "자가 학습 / 자동 진화", row)
        row = self._add_checkbox(inner, "자동 전략 최적화 활성화", "auto_optimize", row)
        row = self._add_checkbox(inner, "자동 진화 활성화", "auto_evolve", row)
        row = self._add_setting(inner, "학습 주기 (거래 건수)", "learn_interval", row)

        # 저장 버튼
        btn_frame = tk.Frame(inner)
        btn_frame.grid(row=row, column=0, columnspan=2, pady=20, padx=20)

        self.save_btn = tk.Button(
            btn_frame, text="  설정 저장  ", font=("Helvetica", 11, "bold"),
            command=self._save_settings, relief=tk.FLAT, padx=20, pady=8,
            cursor="hand2",
        )
        self.save_btn.pack(side=tk.LEFT, padx=5)

        self.validate_btn = tk.Button(
            btn_frame, text="  설정 검증  ", command=self._validate_settings,
            font=("Helvetica", 10), relief=tk.FLAT, padx=14, pady=8,
            cursor="hand2",
        )
        self.validate_btn.pack(side=tk.LEFT, padx=5)

    def _add_section(self, parent, title, row):
        lbl = tk.Label(parent, text=title, font=("Helvetica", 13, "bold"))
        lbl.grid(row=row, column=0, columnspan=2, sticky=tk.W, padx=20, pady=(20, 5))
        sep = tk.Frame(parent, height=1)
        sep.grid(row=row + 1, column=0, columnspan=2, sticky="ew", padx=20)
        return row + 2

    def _add_setting(self, parent, label, key, row, show=""):
        tk.Label(parent, text=label, font=("Helvetica", 10)).grid(
            row=row, column=0, sticky=tk.W, padx=(35, 10), pady=4,
        )
        var = tk.StringVar()
        entry = tk.Entry(parent, textvariable=var, width=35, show=show,
                         font=("Helvetica", 10), relief=tk.FLAT)
        entry.grid(row=row, column=1, sticky=tk.W, pady=4, padx=(0, 20))
        self.setting_vars[key] = var
        return row + 1

    def _add_checkbox(self, parent, label, key, row):
        var = tk.BooleanVar()
        cb = tk.Checkbutton(parent, text=label, variable=var, font=("Helvetica", 10))
        cb.grid(row=row, column=0, columnspan=2, sticky=tk.W, padx=35, pady=4)
        self.setting_vars[key] = var
        return row + 1

    def _add_radio(self, parent, label, key, values, labels, row):
        tk.Label(parent, text=label, font=("Helvetica", 10)).grid(
            row=row, column=0, sticky=tk.W, padx=(35, 10), pady=4,
        )
        var = tk.StringVar(value=values[0])
        radio_frame = tk.Frame(parent)
        radio_frame.grid(row=row, column=1, sticky=tk.W, pady=4)
        for v, l in zip(values, labels):
            tk.Radiobutton(radio_frame, text=l, variable=var, value=v,
                           font=("Helvetica", 10)).pack(side=tk.LEFT, padx=5)
        self.setting_vars[key] = var
        return row + 1

    # ─────────────────── 테마 ───────────────────

    def _apply_theme(self):
        c = self.c

        # Root
        self.root.configure(bg=c["bg"])

        # Header
        self.header.configure(bg=c["header_bg"])
        self.logo_badge.configure(bg=c["accent"], fg="#ffffff")
        self.title_label.configure(bg=c["header_bg"], fg=c["accent"])
        self.subtitle_label.configure(bg=c["header_bg"], fg=c["dim"])
        self.status_label.configure(bg=c["header_bg"], fg=c["fg"])
        self.status_dot.configure(bg=c["header_bg"], fg=c["dim"])
        self.evo_label.configure(bg=c["header_bg"], fg=c["yellow"])
        self.header_line.configure(bg=c["card_border"])

        # ttk Style
        style = ttk.Style()
        style.theme_use("clam")

        # Notebook (탭)
        style.configure("TNotebook", background=c["bg"], borderwidth=0)
        style.configure(
            "TNotebook.Tab",
            background=c["tab_inactive"],
            foreground=c["dim"],
            padding=[18, 8],
            font=("Helvetica", 10, "bold"),
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", c["bg_surface"])],
            foreground=[("selected", c["accent"])],
        )

        # Treeview
        style.configure(
            "Treeview",
            background=c["card"],
            foreground=c["fg"],
            fieldbackground=c["card"],
            borderwidth=0,
            font=("Helvetica", 10),
            rowheight=28,
        )
        style.configure(
            "Treeview.Heading",
            background=c["bg_surface"],
            foreground=c["dim"],
            font=("Helvetica", 9, "bold"),
            borderwidth=0,
        )
        style.map(
            "Treeview",
            background=[("selected", c["accent"] + "30")],
            foreground=[("selected", c["accent"])],
        )

        # Combobox
        style.configure(
            "TCombobox",
            fieldbackground=c["input_bg"],
            background=c["button"],
            foreground=c["fg"],
        )

        # 스크롤바
        style.configure(
            "Vertical.TScrollbar",
            background=c["button"],
            troughcolor=c["bg"],
            borderwidth=0,
        )

        # 체크박스
        style.configure(
            "TCheckbutton",
            background=c["bg"],
            foreground=c["fg"],
        )

        # 각 탭 프레임 배경색 적용
        for tab_id in self.notebook.tabs():
            widget = self.notebook.nametowidget(tab_id)
            self._apply_bg_recursive(widget, c["bg"])

        # 카드 스타일 (대시보드)
        for key in list(self.card_labels.keys()):
            if key.endswith("_card"):
                self.card_labels[key].configure(bg=c["card"],
                                                highlightbackground=c["card_border"],
                                                highlightthickness=1)

        for key in list(self.stat_labels.keys()):
            if key.endswith("_card"):
                self.stat_labels[key].configure(bg=c["card"],
                                                highlightbackground=c["card_border"],
                                                highlightthickness=1)

        # 버튼 스타일
        for btn, color in [
            (self.refresh_btn, c["accent"]),
            (self.start_btn, c["green"]),
            (self.stop_btn, c["red"]),
            (self.save_btn, c["accent"]),
        ]:
            if hasattr(self, btn.winfo_name()):
                pass
            btn.configure(bg=color, fg="#ffffff" if self.theme == "dark" else c["bg"],
                         activebackground=color, activeforeground="#ffffff")

        if hasattr(self, 'analyze_btn'):
            self.analyze_btn.configure(bg=c["accent2"], fg="#ffffff",
                                       activebackground=c["accent2"], activeforeground="#ffffff")
        if hasattr(self, 'validate_btn'):
            self.validate_btn.configure(bg=c["button"], fg=c["fg"],
                                        activebackground=c["button"])

        # 로그 텍스트
        if hasattr(self, 'log_text'):
            self.log_text.configure(bg="#06060f" if self.theme == "dark" else "#f8f9fc",
                                    fg=c["fg"], insertbackground=c["accent"],
                                    selectbackground=c["accent"] + "40")
        if hasattr(self, 'analysis_text'):
            self.analysis_text.configure(bg="#06060f" if self.theme == "dark" else "#f8f9fc",
                                         fg=c["fg"], insertbackground=c["accent"],
                                         selectbackground=c["accent"] + "40")

    def _apply_bg_recursive(self, widget, color):
        """재귀적으로 배경색을 적용한다."""
        try:
            widget.configure(bg=color)
        except tk.TclError:
            pass
        for child in widget.winfo_children():
            self._apply_bg_recursive(child, color)

    # ─────────────────── 설정 로드/저장 ───────────────────

    def _load_user_prefs(self) -> dict:
        if self.SETTINGS_FILE.exists():
            try:
                return json.loads(self.SETTINGS_FILE.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _load_settings_to_ui(self):
        mapping = {
            "app_key": self.settings.app_key,
            "app_secret": self.settings.app_secret,
            "account_no": self.settings.account_no,
            "max_buy_amount": str(self.settings.max_buy_amount),
            "max_hold_count": str(self.settings.max_hold_count),
            "stop_loss_pct": str(self.settings.stop_loss_pct),
            "take_profit_pct": str(self.settings.take_profit_pct),
            "trading_start_time": self.settings.trading_start_time,
            "trading_end_time": self.settings.trading_end_time,
            "naver_client_id": self.user_prefs.get("naver_client_id", ""),
            "naver_client_secret": self.user_prefs.get("naver_client_secret", ""),
            "openai_api_key": self.settings.openai_api_key,
            "anthropic_api_key": self.user_prefs.get("anthropic_api_key", ""),
            "learn_interval": str(self.user_prefs.get("learn_interval", 20)),
        }
        for key, val in mapping.items():
            if key in self.setting_vars and isinstance(self.setting_vars[key], tk.StringVar):
                self.setting_vars[key].set(val)

        if "is_mock" in self.setting_vars:
            self.setting_vars["is_mock"].set(self.settings.is_mock)
        if "auto_optimize" in self.setting_vars:
            self.setting_vars["auto_optimize"].set(self.user_prefs.get("auto_optimize", True))
        if "auto_evolve" in self.setting_vars:
            self.setting_vars["auto_evolve"].set(self.user_prefs.get("auto_evolve", True))
        if "theme" in self.setting_vars:
            self.setting_vars["theme"].set(self.user_prefs.get("theme", "dark"))

    def _load_state_to_dashboard(self):
        """이전 세션 상태를 대시보드에 로드한다."""
        try:
            from trading.state_manager import StateManager
            self._state_mgr = StateManager()
            stats = self._state_mgr.get_stats_summary()

            if stats["total_trades"] > 0:
                self.stat_labels["cum_trades"].config(text=f"{stats['total_trades']} 건")
                self.stat_labels["cum_wins"].config(
                    text=f"{stats['win_rate']:.1f}%",
                    fg=self.c["green"] if stats["win_rate"] >= 50 else self.c["red"],
                )
                self.stat_labels["cum_profit"].config(
                    text=f"{stats['total_profit']:+,.0f} 원",
                    fg=self.c["green"] if stats["total_profit"] >= 0 else self.c["red"],
                )
                self.stat_labels["best_trade"].config(text=f"{stats['best_trade']:+,.0f} 원")
                self.stat_labels["evo_gen"].config(text=f"#{stats['evolution_gen']}")

            # 이전 세션 복원 정보
            resume = self._state_mgr.get_resume_info()
            if resume["last_active"]:
                self.resume_label.config(
                    text=f"이전 세션: {resume['strategy']} ({resume['market']}) | 마지막: {resume['last_active']}",
                )
                self.resume_btn.pack(side=tk.LEFT, padx=10)
        except Exception:
            pass

        self._load_trade_history()

    def _load_trade_history(self):
        """거래 내역 파일에서 최근 거래를 로드한다."""
        trade_file = Path("logs/trades.json")
        if not trade_file.exists():
            return
        try:
            data = json.loads(trade_file.read_text(encoding="utf-8"))
            for item in self.trades_tree.get_children():
                self.trades_tree.delete(item)

            today_count = 0
            from datetime import datetime
            today = datetime.now().strftime("%Y-%m-%d")

            for t in data[-10:]:
                ts = t.get("timestamp", "")[:16]
                self.trades_tree.insert("", 0, values=(
                    ts,
                    t.get("stock_name", ""),
                    t.get("side", ""),
                    t.get("quantity", 0),
                    f"{t.get('price', 0):,}",
                    f"{t.get('profit_loss', 0):+,}" if t.get("side") == "SELL" else "-",
                ))
                if ts.startswith(today):
                    today_count += 1

            self.card_labels["today_trades"].config(text=f"{today_count} 건")
        except (json.JSONDecodeError, OSError):
            pass

    def _save_settings(self):
        env_lines = [
            f"KIS_APP_KEY={self.setting_vars['app_key'].get()}",
            f"KIS_APP_SECRET={self.setting_vars['app_secret'].get()}",
            f"KIS_ACCOUNT_NO={self.setting_vars['account_no'].get()}",
            f"KIS_MOCK={'true' if self.setting_vars['is_mock'].get() else 'false'}",
            f"MAX_BUY_AMOUNT={self.setting_vars['max_buy_amount'].get()}",
            f"MAX_HOLD_COUNT={self.setting_vars['max_hold_count'].get()}",
            f"STOP_LOSS_PCT={self.setting_vars['stop_loss_pct'].get()}",
            f"TAKE_PROFIT_PCT={self.setting_vars['take_profit_pct'].get()}",
            f"TRADING_START_TIME={self.setting_vars['trading_start_time'].get()}",
            f"TRADING_END_TIME={self.setting_vars['trading_end_time'].get()}",
            f"NAVER_CLIENT_ID={self.setting_vars.get('naver_client_id', tk.StringVar()).get()}",
            f"NAVER_CLIENT_SECRET={self.setting_vars.get('naver_client_secret', tk.StringVar()).get()}",
            f"OPENAI_API_KEY={self.setting_vars.get('openai_api_key', tk.StringVar()).get()}",
            f"ANTHROPIC_API_KEY={self.setting_vars.get('anthropic_api_key', tk.StringVar()).get()}",
            f"LOG_LEVEL=INFO",
        ]
        Path(".env").write_text("\n".join(env_lines) + "\n", encoding="utf-8")

        prefs = {
            "theme": self.setting_vars.get("theme", tk.StringVar(value="dark")).get(),
            "auto_optimize": self.setting_vars.get("auto_optimize", tk.BooleanVar(value=True)).get(),
            "auto_evolve": self.setting_vars.get("auto_evolve", tk.BooleanVar(value=True)).get(),
            "learn_interval": self.setting_vars.get("learn_interval", tk.StringVar(value="20")).get(),
            "naver_client_id": self.setting_vars.get("naver_client_id", tk.StringVar()).get(),
            "naver_client_secret": self.setting_vars.get("naver_client_secret", tk.StringVar()).get(),
            "anthropic_api_key": self.setting_vars.get("anthropic_api_key", tk.StringVar()).get(),
        }
        self.SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        self.SETTINGS_FILE.write_text(json.dumps(prefs, ensure_ascii=False, indent=2), encoding="utf-8")

        self.settings = Settings.from_env()

        new_theme = prefs["theme"]
        if new_theme != self.theme:
            self.theme = new_theme
            self.c = self.THEME_COLORS[new_theme]
            self._apply_theme()

        messagebox.showinfo("설정", "설정이 저장되었습니다.")

    def _validate_settings(self):
        errors = self.settings.validate()
        if errors:
            messagebox.showwarning("설정 검증", "\n".join(errors))
        else:
            messagebox.showinfo("설정 검증", "모든 설정이 유효합니다.")

    # ─────────────────── 대시보드 ───────────────────

    def _get_market_code(self) -> str:
        display = self.market_var.get()
        for code, name in self.MARKETS.items():
            if display == name:
                return code
        return "KR"

    def _refresh_balance(self):
        errors = self.settings.validate()
        if errors:
            messagebox.showwarning("오류", "먼저 설정에서 API 키를 입력하세요.")
            return

        def _fetch():
            try:
                from api.kis_api import KISApi
                api = KISApi(self.settings)

                balance = api.get_balance()
                kr_holdings = balance.get("holdings", [])
                for h in kr_holdings:
                    h["market"] = "KR"

                try:
                    overseas = api.get_overseas_balance()
                    os_holdings = overseas.get("holdings", [])
                    kr_holdings.extend(os_holdings)
                    kr_summary = balance.get("summary", {})
                    os_summary = overseas.get("summary", {})
                    kr_summary["total_profit_loss"] = (
                        kr_summary.get("total_profit_loss", 0) +
                        os_summary.get("total_profit_loss", 0)
                    )
                except Exception:
                    pass

                balance["holdings"] = kr_holdings
                self.root.after(0, lambda: self._update_dashboard(balance))
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror("오류", str(e)))

        threading.Thread(target=_fetch, daemon=True).start()

    def _update_dashboard(self, balance):
        holdings = balance.get("holdings", [])
        summary = balance.get("summary", {})

        total_buy = summary.get("total_buy_amount", 0)
        total_eval = summary.get("total_eval_amount", 0)
        total_pl = summary.get("total_profit_loss", 0)
        cash = summary.get("available_cash", 0)
        rate = (total_pl / total_buy * 100) if total_buy > 0 else 0

        self.card_labels["total_asset"].config(text=f"{total_eval + cash:,.0f} 원")
        self.card_labels["total_profit"].config(
            text=f"{total_pl:+,.0f} 원",
            fg=self.c["green"] if total_pl >= 0 else self.c["red"],
        )
        self.card_labels["profit_rate"].config(
            text=f"{rate:+.2f}%",
            fg=self.c["green"] if rate >= 0 else self.c["red"],
        )
        self.card_labels["hold_count"].config(text=f"{len(holdings)} 개")
        self.holdings_count_lbl.config(text=f"{len(holdings)} 종목")

        for item in self.holdings_tree.get_children():
            self.holdings_tree.delete(item)
        for h in holdings:
            market = h.get("market", "KR")
            self.holdings_tree.insert("", tk.END, values=(
                h["stock_name"], market, h["quantity"],
                f"{h['avg_price']:,.0f}", f"{h['current_price']:,.0f}",
                f"{h['profit_loss']:+,.0f}", f"{h['profit_rate']:+.2f}%",
            ))

        self._load_trade_history()

        if self._state_mgr:
            stats = self._state_mgr.get_stats_summary()
            if stats["total_trades"] > 0:
                self.stat_labels["cum_trades"].config(text=f"{stats['total_trades']} 건")
                self.stat_labels["cum_wins"].config(
                    text=f"{stats['win_rate']:.1f}%",
                    fg=self.c["green"] if stats["win_rate"] >= 50 else self.c["red"],
                )
                self.stat_labels["cum_profit"].config(
                    text=f"{stats['total_profit']:+,.0f} 원",
                    fg=self.c["green"] if stats["total_profit"] >= 0 else self.c["red"],
                )

    def _auto_refresh(self):
        if self._is_trading and self.auto_refresh_var.get():
            self._refresh_balance()
        if self._is_trading:
            self._auto_refresh_id = self.root.after(30000, self._auto_refresh)

    # ─────────────────── 자동매매 ───────────────────

    def _get_selected_market(self):
        return self._get_market_code()

    def _resume_trading(self):
        if not self._state_mgr:
            return
        info = self._state_mgr.get_resume_info()
        self.strategy_var.set(info["strategy"])
        self.interval_var.set(str(info["interval"]))

        market = info.get("market", "KR")
        if market in self.MARKETS:
            self.market_var.set(self.MARKETS[market])

        stocks = info.get("target_stocks", [])
        if stocks:
            self.stocks_entry.delete(0, tk.END)
            self.stocks_entry.insert(0, ",".join(stocks))

        self.notebook.select(1)
        self._log(f"이전 세션 복원: {info['strategy']} ({market}) | 누적 수익: {info['total_profit']:+,.0f}원")
        self._start_trading()

    def _start_trading(self):
        errors = self.settings.validate()
        if errors:
            messagebox.showwarning("오류", "먼저 설정에서 API 키를 입력하세요.")
            return

        self._is_trading = True
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.status_label.config(text="매매중", fg=self.c["green"])
        self.status_dot.config(fg=self.c["green"])

        self._trading_thread = threading.Thread(target=self._trading_loop, daemon=True)
        self._trading_thread.start()

        self._auto_refresh_id = self.root.after(30000, self._auto_refresh)

    def _stop_trading(self):
        self._is_trading = False
        if self._trader:
            self._trader.stop()
        if self._auto_refresh_id:
            self.root.after_cancel(self._auto_refresh_id)
            self._auto_refresh_id = None
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self.status_label.config(text="대기중", fg=self.c["fg"])
        self.status_dot.config(fg=self.c["dim"])
        self._log("자동매매 중지됨")
        self._refresh_balance()

    def _trading_loop(self):
        import logging

        class GUILogHandler(logging.Handler):
            def __init__(self, callback):
                super().__init__()
                self.callback = callback

            def emit(self, record):
                msg = self.format(record)
                self.callback(msg)

        handler = GUILogHandler(lambda msg: self.root.after(0, self._log, msg))
        handler.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", datefmt="%H:%M:%S"))
        logging.getLogger("oshms").addHandler(handler)

        try:
            from api.kis_api import KISApi
            from strategy import ExpertStrategy, ScalpingStrategy, MomentumStrategy, CombinedStrategy
            from trading.trader import AutoTrader
            from trading.state_manager import StateManager

            self.settings = Settings.from_env()
            api = KISApi(self.settings)

            strategy_map = {
                "expert": lambda: ExpertStrategy(api=api, settings=self.settings),
                "scalping": ScalpingStrategy,
                "momentum": MomentumStrategy,
                "combined": CombinedStrategy,
            }

            name = self.strategy_var.get()
            factory = strategy_map.get(name, strategy_map["expert"])
            strategy = factory()

            self._trader = AutoTrader(api, self.settings, strategy)

            stocks_text = self.stocks_entry.get().strip()
            target = None
            if stocks_text and stocks_text != "자동선정":
                target = [s.strip() for s in stocks_text.split(",")]

            interval = int(self.interval_var.get())
            market = self._get_selected_market()

            if not self._state_mgr:
                self._state_mgr = StateManager()
            self._state_mgr.start_session(name, target or [], interval, market)

            gen = self._state_mgr.state.evolution_generation
            self.root.after(0, lambda: self.evo_label.config(text=f"진화 #{gen}"))

            self._trader.start(target_stocks=target, interval=interval)

        except Exception as e:
            self.root.after(0, lambda: self._log(f"오류: {e}"))
            self.root.after(0, self._stop_trading)
        finally:
            logging.getLogger("oshms").removeHandler(handler)

    def _log(self, message: str):
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
        lines = int(self.log_text.index("end-1c").split(".")[0])
        if lines > 500:
            self.log_text.delete("1.0", f"{lines - 400}.0")

    # ─────────────────── 종목분석 ───────────────────

    def _run_analysis(self):
        code = self.analyze_code.get().strip()
        name = self.analyze_name.get().strip() or code
        market = self.analyze_market_var.get()

        if not code:
            messagebox.showwarning("입력 오류", "종목코드를 입력하세요.")
            return

        errors = self.settings.validate()
        if errors:
            messagebox.showwarning("오류", "먼저 설정에서 API 키를 입력하세요.")
            return

        self.analysis_text.delete("1.0", tk.END)
        self.analysis_text.insert(tk.END, f"[{market}] {name}({code}) AI 분석 중...\n")

        def _analyze():
            try:
                from api.kis_api import KISApi
                from strategy.expert import ExpertStrategy
                from strategy.market_context import MarketContextAnalyzer

                self.settings = Settings.from_env()
                api = KISApi(self.settings)
                strategy = ExpertStrategy(api=api, settings=self.settings)

                try:
                    ctx = MarketContextAnalyzer(api).analyze()
                    strategy.set_market_context(ctx)
                except Exception:
                    pass

                if market == "KR":
                    current_price = api.get_current_price(code)
                    candles = api.get_minute_chart(code, period="3")
                    if len(candles) < 20:
                        candles = api.get_daily_chart(code, count=60)
                else:
                    price_data = api.get_overseas_price(market, code)
                    if not price_data:
                        self.root.after(0, lambda: self.analysis_text.insert(tk.END, "해외 시세 조회 실패\n"))
                        return
                    current_price = {
                        "price": int(price_data["price"]) if price_data["price"] > 100 else price_data["price"],
                        "stock_name": name,
                        "stock_code": code,
                        "open": price_data["open"],
                        "high": price_data["high"],
                        "low": price_data["low"],
                        "volume": price_data["volume"],
                        "change_rate": price_data["change_rate"],
                    }
                    candles = api.get_overseas_daily_chart(market, code, count=60)

                if not current_price:
                    self.root.after(0, lambda: self.analysis_text.insert(tk.END, "시세 조회 실패\n"))
                    return

                current_price["stock_name"] = name

                if not candles:
                    self.root.after(0, lambda: self.analysis_text.insert(tk.END, "차트 데이터 조회 실패\n"))
                    return

                analysis = strategy.full_analysis(code, name, candles, current_price)
                result = f"[시장: {market}]\n" + analysis.summary()

                # 기술적 지표
                t = analysis.technical
                if t:
                    result += f"\n\n── 기술적 지표 ──"
                    result += f"\n  SMA(5/20/60): {t.sma_5:,.0f} / {t.sma_20:,.0f} / {t.sma_60:,.0f}"
                    result += f"\n  RSI: {t.rsi:.1f} | MACD: {t.macd_line:,.0f}"
                    result += f"\n  Stochastic: K={t.stoch_k:.1f} D={t.stoch_d:.1f}"
                    result += f"\n  BB: {t.bb_lower:,.0f} ~ {t.bb_middle:,.0f} ~ {t.bb_upper:,.0f}"
                    result += f"\n  VWAP: {t.vwap:,.0f} | ATR: {t.atr:,.0f} ({t.atr_pct:.2f}%)"
                    result += f"\n  일목: {t.ichimoku_signal} | 거래량비: x{t.volume_ratio:.1f}"

                if analysis.sentiment and analysis.sentiment.key_headlines:
                    result += "\n\n── 최신 뉴스 ──"
                    for h in analysis.sentiment.key_headlines:
                        result += f"\n  {h}"

                # AI 분석 리포트
                try:
                    from strategy.ai_analyst import AIAnalyst
                    ai = AIAnalyst(api_key=self.settings.openai_api_key)
                    ai_data = AIAnalyst.extract_analysis_data(analysis)
                    ai_result = ai.analyze(ai_data)
                    result += "\n\n" + ai_result.format_report()
                except Exception as ai_err:
                    result += f"\n\nAI 분석: {ai_err}"

                self.root.after(0, lambda: (
                    self.analysis_text.delete("1.0", tk.END),
                    self.analysis_text.insert(tk.END, result),
                ))
            except Exception as e:
                self.root.after(0, lambda: self.analysis_text.insert(tk.END, f"\n오류: {e}\n"))

        threading.Thread(target=_analyze, daemon=True).start()
