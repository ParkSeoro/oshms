"""OSHMS 데스크톱 GUI 앱.

Tkinter 기반 메인 윈도우. 토스/로빈후드 영감 소프트 다크 테마.
탭 구성:
  - 대시보드: 자산 현황, 누적 통계, 진화 상세, 로드맵
  - 자동매매: 시작/중지, 실시간 로그, 해외주식 지원
  - 종목분석: 전문가 분석 + AI 리포트 (국내/해외), 분석 중지
  - AI 어시스턴트: 대화형 AI 매매 조언
  - 설정: API키, 매매 파라미터, AI 설정
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

    # ──── 소프트 다크 테마 (토스/로빈후드 영감) ────
    COLORS = {
        # 배경 계열 (따뜻한 다크 그레이 - 순수 검정 아님)
        "bg": "#1c1e26",
        "bg2": "#232530",
        "surface": "#2a2d3a",
        "card": "#303348",
        "border": "#3d4158",
        "border_accent": "#7b93ff",

        # 텍스트 계열 (부드러운 밝기)
        "fg": "#e8eaf2",
        "fg2": "#b8bdd0",
        "dim": "#808498",
        "dim2": "#5c607a",

        # 액센트 (소프트 인디고/라벤더)
        "accent": "#7b93ff",
        "accent2": "#b197fc",
        "accent_soft": "#3b4578",

        # 상태 색상 (부드럽고 선명한)
        "green": "#51cf66",
        "green_dim": "#2b8a3e",
        "red": "#ff6b6b",
        "red_dim": "#e03131",
        "yellow": "#fcc419",
        "orange": "#ff922b",

        # 입력 필드
        "input_bg": "#252838",
        "input_border": "#3d4158",

        # 채팅
        "chat_user_bg": "#3b4578",
        "chat_ai_bg": "#2a3a2e",
    }

    MARKETS = {
        "KR": "KR 한국",
        "NASD": "NASD 나스닥",
        "NYSE": "NYSE 뉴욕",
        "AMEX": "AMEX",
        "SEHK": "SEHK 홍콩",
        "TKSE": "TKSE 일본",
    }

    MARKET_HOURS = {
        "KR": ("09:00~15:30", False),
        "NASD": ("23:30~06:00 (KST)", True),
        "NYSE": ("23:30~06:00 (KST)", True),
        "AMEX": ("23:30~06:00 (KST)", True),
        "SEHK": ("10:30~17:00 (KST)", False),
        "TKSE": ("09:00~15:00 (KST)", False),
    }

    VERSION = "2.6.0"

    ROADMAP = [
        ("v2.4", "AI 분석 (OpenAI/Claude)", True),
        ("v2.4", "해외 주식 매매 지원", True),
        ("v2.4", "자동 진화 엔진", True),
        ("v2.5", "소프트 다크 테마 리디자인", True),
        ("v2.5", "AI 어시스턴트 (앱 내 대화)", True),
        ("v2.5", "동적 리스크 관리 (ATR)", True),
        ("v2.6", "실시간 차트 시각화", False),
        ("v2.6", "미국 프리/애프터마켓 주문", False),
        ("v2.7", "포트폴리오 리밸런싱", False),
        ("v2.7", "텔레그램 알림 봇", False),
        ("v3.0", "안드로이드 앱 (PWA)", False),
        ("v3.0", "멀티 계좌 지원", False),
    ]

    # ──── 폰트 설정 ────
    FONT = "Helvetica"
    MONO = "Consolas"

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("OSHMS - AI Stock Trading System")
        self.root.geometry("1240x900")
        self.root.minsize(1020, 740)
        self.c = self.COLORS

        self.settings = Settings.from_env()
        self.user_prefs = self._load_user_prefs()

        self._trader = None
        self._trading_thread = None
        self._is_trading = False
        self._auto_refresh_id = None
        self._state_mgr = None
        self._evolution = None
        self._analysis_cancel = threading.Event()
        self._analysis_thread = None
        self._kis_api = None  # KISApi 싱글톤 캐시

        # AI 채팅 상태
        self._chat_messages: list[tuple[str, str]] = []  # (role, content)
        self._chat_thread = None

        self._build_ui()
        self._apply_theme()
        self._load_settings_to_ui()
        self._load_state_to_dashboard()

    def run(self):
        self.root.mainloop()

    # ═══════════════════════════════════════════════
    # 헬퍼 컴포넌트
    # ═══════════════════════════════════════════════

    def _make_scroll_frame(self, parent):
        """마우스휠+스크롤바 지원 스크롤 프레임."""
        canvas = tk.Canvas(parent, highlightthickness=0, bg=self.c["bg"], bd=0)
        sb = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=canvas.yview)
        inner = tk.Frame(canvas, bg=self.c["bg"])
        inner.bind("<Configure>", lambda _: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw", tags="inner")
        canvas.configure(yscrollcommand=sb.set)
        canvas.bind("<Configure>", lambda evt: canvas.itemconfig("inner", width=evt.width))
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        def _on_wheel(evt):
            canvas.yview_scroll(-1 * (evt.delta // 120 or (1 if evt.num == 4 else -1)), "units")
        canvas.bind_all("<MouseWheel>", _on_wheel, add="+")
        canvas.bind_all("<Button-4>", _on_wheel, add="+")
        canvas.bind_all("<Button-5>", _on_wheel, add="+")
        return inner, canvas

    def _make_card(self, parent, **kw):
        """소프트 카드 프레임."""
        f = tk.Frame(parent, bg=self.c["card"],
                     highlightbackground=self.c["border"],
                     highlightthickness=1, **kw)
        return f

    def _make_entry(self, parent, var=None, width=25, show="", **kw):
        """스타일된 입력 필드 - 부드러운 테두리."""
        e = tk.Entry(parent, textvariable=var, width=width, show=show,
                     font=(self.MONO, 10), bg=self.c["input_bg"], fg=self.c["fg"],
                     insertbackground=self.c["accent"], relief=tk.FLAT,
                     highlightbackground=self.c["input_border"], highlightthickness=1,
                     highlightcolor=self.c["accent"], **kw)
        return e

    def _make_button(self, parent, text, command, color=None, **kw):
        """소프트 버튼."""
        bg = color or self.c["accent"]
        btn = tk.Button(parent, text=text, command=command,
                        font=(self.FONT, 10, "bold"),
                        bg=bg, fg="#ffffff",
                        activebackground=bg, activeforeground="#ffffff",
                        relief=tk.FLAT, padx=18, pady=7,
                        cursor="hand2", **kw)
        return btn

    def _make_label(self, parent, text, size=10, bold=False, color=None, **kw):
        """스타일된 라벨."""
        weight = "bold" if bold else "normal"
        fg = color or self.c["fg"]
        return tk.Label(parent, text=text, font=(self.FONT, size, weight),
                        bg=parent.cget("bg"), fg=fg, **kw)

    def _section_header(self, parent, text):
        """섹션 헤더 - 부드러운 구분선 포함."""
        f = tk.Frame(parent, bg=parent.cget("bg"))
        f.pack(fill=tk.X, padx=28, pady=(24, 8))
        tk.Label(f, text=text, font=(self.FONT, 13, "bold"),
                 bg=f.cget("bg"), fg=self.c["fg"]).pack(side=tk.LEFT)
        sep = tk.Frame(f, bg=self.c["border"], height=1)
        sep.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(16, 0), pady=1)

    # ═══════════════════════════════════════════════
    # UI 빌드
    # ═══════════════════════════════════════════════

    def _build_ui(self):
        c = self.c
        self.root.configure(bg=c["bg"])

        # ─── 헤더 ───
        self.header = tk.Frame(self.root, bg=c["bg2"], height=56)
        self.header.pack(fill=tk.X)
        self.header.pack_propagate(False)

        logo_f = tk.Frame(self.header, bg=c["bg2"])
        logo_f.pack(side=tk.LEFT, padx=20, pady=10)

        # 로고 뱃지
        badge = tk.Frame(logo_f, bg=c["accent"], padx=8, pady=2)
        badge.pack(side=tk.LEFT, padx=(0, 10))
        tk.Label(badge, text="O", font=(self.FONT, 13, "bold"),
                 bg=c["accent"], fg="#ffffff").pack()

        tk.Label(logo_f, text="OSHMS", font=(self.FONT, 17, "bold"),
                 bg=c["bg2"], fg=c["fg"]).pack(side=tk.LEFT)
        tk.Label(logo_f, text=f"v{self.VERSION}", font=(self.FONT, 8),
                 bg=c["bg2"], fg=c["dim2"]).pack(side=tk.LEFT, padx=(8, 0), pady=(6, 0))

        # 상태 표시
        status_f = tk.Frame(self.header, bg=c["bg2"])
        status_f.pack(side=tk.RIGHT, padx=20, pady=10)
        self.evo_label = tk.Label(status_f, text="", font=(self.FONT, 9),
                                  bg=c["bg2"], fg=c["yellow"])
        self.evo_label.pack(side=tk.LEFT, padx=(0, 20))
        self.status_dot = tk.Label(status_f, text="●", font=("", 12),
                                   bg=c["bg2"], fg=c["dim2"])
        self.status_dot.pack(side=tk.LEFT, padx=(0, 6))
        self.status_label = tk.Label(status_f, text="대기중",
                                     font=(self.FONT, 11, "bold"),
                                     bg=c["bg2"], fg=c["dim"])
        self.status_label.pack(side=tk.LEFT)

        # 헤더 하단 분리선
        tk.Frame(self.root, bg=c["border"], height=1).pack(fill=tk.X)

        # ─── 탭 노트북 ───
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        self._build_dashboard_tab()
        self._build_trading_tab()
        self._build_analysis_tab()
        self._build_chat_tab()
        self._build_settings_tab()

    # ═══════════════════════════════════════════════
    # 대시보드 탭
    # ═══════════════════════════════════════════════

    def _build_dashboard_tab(self):
        frame = tk.Frame(self.notebook, bg=self.c["bg"])
        self.notebook.add(frame, text="   대시보드   ")
        inner, self._dash_canvas = self._make_scroll_frame(frame)
        c = self.c

        # ── 자산 카드 행 ──
        self._section_header(inner, "자산 현황")
        cards_f = tk.Frame(inner, bg=c["bg"])
        cards_f.pack(fill=tk.X, padx=28, pady=(0, 4))

        self.card_labels = {}
        items = [
            ("total_asset", "총 자산", "-- 원", c["accent"]),
            ("total_profit", "총 손익", "-- 원", c["green"]),
            ("profit_rate", "수익률", "--", c["accent2"]),
            ("hold_count", "보유 종목", "0 개", c["yellow"]),
            ("today_trades", "오늘 거래", "0 건", c["dim"]),
        ]
        for i, (key, title, default, color) in enumerate(items):
            card = self._make_card(cards_f, padx=18, pady=14)
            card.grid(row=0, column=i, padx=(0, 10), sticky="nsew")
            cards_f.columnconfigure(i, weight=1)
            # 상단 액센트 라인
            tk.Frame(card, bg=color, height=3).pack(fill=tk.X, pady=(0, 10))
            tk.Label(card, text=title, font=(self.FONT, 9),
                     bg=c["card"], fg=c["dim"]).pack(anchor=tk.W)
            lbl = tk.Label(card, text=default,
                          font=(self.FONT, 17, "bold"),
                          bg=c["card"], fg=c["fg"])
            lbl.pack(anchor=tk.W, pady=(6, 0))
            self.card_labels[key] = lbl

        # ── 누적 통계 ──
        self._section_header(inner, "누적 통계")
        stats_f = tk.Frame(inner, bg=c["bg"])
        stats_f.pack(fill=tk.X, padx=28, pady=(0, 4))

        self.stat_labels = {}
        stat_items = [
            ("cum_trades", "총 거래", "0 건"),
            ("cum_wins", "승률", "--"),
            ("cum_profit", "누적 수익", "0 원"),
            ("best_trade", "최고 수익", "0 원"),
            ("evo_gen", "진화 세대", "#0"),
        ]
        for i, (key, title, default) in enumerate(stat_items):
            card = self._make_card(stats_f, padx=16, pady=12)
            card.grid(row=0, column=i, padx=(0, 10), sticky="nsew")
            stats_f.columnconfigure(i, weight=1)
            tk.Label(card, text=title, font=(self.FONT, 9),
                     bg=c["card"], fg=c["dim"]).pack(anchor=tk.W)
            lbl = tk.Label(card, text=default, font=(self.FONT, 14, "bold"),
                          bg=c["card"], fg=c["fg"])
            lbl.pack(anchor=tk.W, pady=(4, 0))
            self.stat_labels[key] = lbl

        # ── 진화 엔진 상태 ──
        self._section_header(inner, "진화 엔진 상태")
        evo_card = self._make_card(inner, padx=22, pady=18)
        evo_card.pack(fill=tk.X, padx=28, pady=(0, 4))
        self.evo_detail_text = tk.Text(evo_card, height=7,
                                        font=(self.MONO, 10),
                                        bg=c["card"], fg=c["fg2"],
                                        relief=tk.FLAT, wrap=tk.WORD,
                                        state=tk.DISABLED,
                                        highlightthickness=0)
        self.evo_detail_text.pack(fill=tk.X)
        self._load_evolution_details()

        # ── 보유 종목 ──
        hold_hdr = tk.Frame(inner, bg=c["bg"])
        hold_hdr.pack(fill=tk.X, padx=28, pady=(24, 8))
        self._make_label(hold_hdr, "보유 종목", 13, True).pack(side=tk.LEFT)
        self.holdings_count_lbl = tk.Label(hold_hdr, text="0 종목",
                                           font=(self.FONT, 10),
                                           bg=c["bg"], fg=c["dim"])
        self.holdings_count_lbl.pack(side=tk.RIGHT)

        cols = ("종목명", "시장", "수량", "평균가", "현재가", "손익", "수익률")
        tree_card = self._make_card(inner)
        tree_card.pack(fill=tk.X, padx=28, pady=(0, 4))
        self.holdings_tree = ttk.Treeview(tree_card, columns=cols,
                                          show="headings", height=5)
        for col in cols:
            self.holdings_tree.heading(col, text=col)
            w = 140 if col == "종목명" else 70 if col == "시장" else 100
            anchor = tk.W if col in ("종목명", "시장") else tk.E
            self.holdings_tree.column(col, width=w, anchor=anchor)
        tree_sb = ttk.Scrollbar(tree_card, orient=tk.VERTICAL,
                                command=self.holdings_tree.yview)
        self.holdings_tree.configure(yscrollcommand=tree_sb.set)
        self.holdings_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_sb.pack(side=tk.RIGHT, fill=tk.Y)

        # ── 최근 거래 ──
        self._section_header(inner, "최근 거래")
        trade_cols = ("시간", "종목", "매매", "수량", "가격", "손익")
        trades_card = self._make_card(inner)
        trades_card.pack(fill=tk.X, padx=28, pady=(0, 4))
        self.trades_tree = ttk.Treeview(trades_card, columns=trade_cols,
                                        show="headings", height=4)
        for col in trade_cols:
            self.trades_tree.heading(col, text=col)
            w = 150 if col == "시간" else 120 if col == "종목" else 85
            anchor = tk.E if col in ("수량", "가격", "손익") else tk.W
            self.trades_tree.column(col, width=w, anchor=anchor)
        self.trades_tree.pack(fill=tk.BOTH, expand=True)

        # ── 새로고침 버튼 ──
        btn_f = tk.Frame(inner, bg=c["bg"])
        btn_f.pack(fill=tk.X, padx=28, pady=(14, 4))
        self.refresh_btn = self._make_button(btn_f, "  새로고침  ",
                                              self._refresh_balance)
        self.refresh_btn.pack(side=tk.LEFT)
        self.auto_refresh_var = tk.BooleanVar(value=True)
        tk.Checkbutton(btn_f, text="자동 갱신 (30초)",
                       variable=self.auto_refresh_var,
                       font=(self.FONT, 10), bg=c["bg"], fg=c["dim"],
                       selectcolor=c["surface"], activebackground=c["bg"],
                       activeforeground=c["fg"]).pack(side=tk.LEFT, padx=14)

        # ── 개발 로드맵 ──
        self._section_header(inner, "개발 로드맵")
        roadmap_card = self._make_card(inner, padx=22, pady=18)
        roadmap_card.pack(fill=tk.X, padx=28, pady=(0, 4))
        for ver, feature, done in self.ROADMAP:
            row_f = tk.Frame(roadmap_card, bg=c["card"])
            row_f.pack(fill=tk.X, pady=3)
            icon = "●" if done else "○"
            icon_color = c["green"] if done else c["dim2"]
            tk.Label(row_f, text=icon, font=("", 8), bg=c["card"],
                     fg=icon_color).pack(side=tk.LEFT, padx=(0, 10))
            tk.Label(row_f, text=ver, font=(self.MONO, 9, "bold"),
                     bg=c["card"],
                     fg=c["accent"] if not done else c["dim"]
                     ).pack(side=tk.LEFT, padx=(0, 12))
            tk.Label(row_f, text=feature, font=(self.FONT, 10),
                     bg=c["card"],
                     fg=c["fg"] if done else c["dim"]
                     ).pack(side=tk.LEFT)
            if done:
                tk.Label(row_f, text=" 완료 ", font=(self.FONT, 8, "bold"),
                         bg=c["green_dim"], fg="#ffffff",
                         padx=6, pady=1).pack(side=tk.RIGHT)
            else:
                tk.Label(row_f, text=" 예정 ", font=(self.FONT, 8),
                         bg=c["card"], fg=c["orange"]).pack(side=tk.RIGHT)

        # ── 해외 시장 거래시간 ──
        self._section_header(inner, "해외 시장 거래시간 (한국시간 기준)")
        hours_card = self._make_card(inner, padx=22, pady=18)
        hours_card.pack(fill=tk.X, padx=28, pady=(0, 28))
        for mk, (hours, has_ext) in self.MARKET_HOURS.items():
            row_f = tk.Frame(hours_card, bg=c["card"])
            row_f.pack(fill=tk.X, pady=3)
            tk.Label(row_f, text=f"{self.MARKETS.get(mk, mk):16s}",
                     font=(self.MONO, 10, "bold"), bg=c["card"],
                     fg=c["accent"]).pack(side=tk.LEFT)
            tk.Label(row_f, text=hours, font=(self.MONO, 10),
                     bg=c["card"], fg=c["fg2"]).pack(side=tk.LEFT, padx=(10, 0))
            if has_ext:
                tk.Label(row_f, text=" 시간외 가능 ",
                         font=(self.FONT, 8, "bold"),
                         bg=c["yellow"], fg="#1a1a1a",
                         padx=6, pady=1).pack(side=tk.RIGHT)

        us_note = tk.Label(
            hours_card,
            text="* 미국: 프리마켓(18:00~23:30 KST), 애프터마켓(06:00~10:00 KST) 지정가 주문 가능\n"
                 "* KIS Open API에서 해외 시간외 주문 시 ORD_SVR_DVSN_CD='0', 지정가 사용",
            font=(self.FONT, 9), bg=c["card"], fg=c["dim"], justify=tk.LEFT,
        )
        us_note.pack(anchor=tk.W, pady=(12, 0))

    # ═══════════════════════════════════════════════
    # 자동매매 탭
    # ═══════════════════════════════════════════════

    def _build_trading_tab(self):
        frame = tk.Frame(self.notebook, bg=self.c["bg"])
        self.notebook.add(frame, text="   자동매매   ")
        c = self.c

        # 설정 카드
        top = self._make_card(frame, padx=22, pady=18)
        top.pack(fill=tk.X, padx=24, pady=(18, 10))

        # 시장 선택
        r1 = tk.Frame(top, bg=c["card"])
        r1.pack(fill=tk.X, pady=(0, 12))
        self._make_label(r1, "마켓", 11, True).pack(side=tk.LEFT, padx=(0, 12))
        self.market_var = tk.StringVar(value="KR")
        market_cb = ttk.Combobox(r1, textvariable=self.market_var, width=18,
                                 values=list(self.MARKETS.values()),
                                 state="readonly")
        market_cb.set("KR 한국")
        market_cb.pack(side=tk.LEFT, padx=6)
        self.market_hours_lbl = tk.Label(r1, text="09:00~15:30",
                                         font=(self.FONT, 9),
                                         bg=c["card"], fg=c["dim"])
        self.market_hours_lbl.pack(side=tk.LEFT, padx=(14, 0))
        market_cb.bind("<<ComboboxSelected>>", self._on_market_changed)

        # 종목/전략/주기
        r2 = tk.Frame(top, bg=c["card"])
        r2.pack(fill=tk.X, pady=(0, 12))
        self._make_label(r2, "종목").pack(side=tk.LEFT)
        self.stocks_entry = self._make_entry(r2, width=20)
        self.stocks_entry.pack(side=tk.LEFT, padx=(8, 0))
        self.stocks_entry.insert(0, "자동선정")

        self._make_label(r2, "전략").pack(side=tk.LEFT, padx=(18, 0))
        self.strategy_var = tk.StringVar(value="expert")
        ttk.Combobox(r2, textvariable=self.strategy_var, width=12,
                     values=["expert", "scalping", "momentum", "combined"],
                     state="readonly").pack(side=tk.LEFT, padx=6)

        self._make_label(r2, "주기(초)").pack(side=tk.LEFT, padx=(18, 0))
        self.interval_var = tk.StringVar(value="10")
        self._make_entry(r2, var=self.interval_var, width=5).pack(side=tk.LEFT, padx=(8, 0))

        # 이전 세션 복원
        r_resume = tk.Frame(top, bg=c["card"])
        r_resume.pack(fill=tk.X, pady=(0, 10))
        self.resume_label = tk.Label(r_resume, text="", font=(self.FONT, 9),
                                     bg=c["card"], fg=c["dim"])
        self.resume_label.pack(side=tk.LEFT)
        self.resume_btn = tk.Button(r_resume, text="이전 세션 이어하기",
                                    command=self._resume_trading,
                                    font=(self.FONT, 9, "bold"),
                                    bg=c["surface"], fg=c["accent"],
                                    relief=tk.FLAT, padx=12, pady=3,
                                    cursor="hand2",
                                    activebackground=c["card"],
                                    activeforeground=c["accent"])

        # 시작/중지 버튼
        btn_f = tk.Frame(top, bg=c["card"])
        btn_f.pack(fill=tk.X, pady=(6, 0))
        self.start_btn = self._make_button(btn_f, "  자동매매 시작  ",
                                           self._start_trading, c["green_dim"])
        self.start_btn.configure(font=(self.FONT, 12, "bold"), padx=28, pady=10)
        self.start_btn.pack(side=tk.LEFT, padx=(0, 12))
        self.stop_btn = self._make_button(btn_f, "  중지  ",
                                          self._stop_trading, c["red_dim"])
        self.stop_btn.configure(state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT)

        # 로그 영역
        log_hdr = tk.Frame(frame, bg=c["bg"])
        log_hdr.pack(fill=tk.X, padx=24, pady=(14, 6))
        self._make_label(log_hdr, "매매 로그", 13, True).pack(side=tk.LEFT)
        tk.Button(log_hdr, text="로그 지우기", font=(self.FONT, 9),
                  bg=c["bg"], fg=c["dim"], relief=tk.FLAT, cursor="hand2",
                  command=lambda: self.log_text.delete("1.0", tk.END),
                  activebackground=c["bg"],
                  activeforeground=c["accent"]).pack(side=tk.RIGHT)

        log_card = self._make_card(frame)
        log_card.pack(fill=tk.BOTH, expand=True, padx=24, pady=(0, 18))
        self.log_text = scrolledtext.ScrolledText(
            log_card, font=(self.MONO, 10), wrap=tk.WORD,
            bg=c["bg"], fg=c["fg2"], relief=tk.FLAT,
            insertbackground=c["accent"],
            selectbackground=c["accent_soft"],
            highlightthickness=0,
        )
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)

    # ═══════════════════════════════════════════════
    # 종목분석 탭
    # ═══════════════════════════════════════════════

    def _build_analysis_tab(self):
        frame = tk.Frame(self.notebook, bg=self.c["bg"])
        self.notebook.add(frame, text="   종목분석   ")
        c = self.c

        search_card = self._make_card(frame, padx=22, pady=18)
        search_card.pack(fill=tk.X, padx=24, pady=(18, 10))

        r1 = tk.Frame(search_card, bg=c["card"])
        r1.pack(fill=tk.X, pady=(0, 10))

        self._make_label(r1, "시장").pack(side=tk.LEFT)
        self.analyze_market_var = tk.StringVar(value="KR")
        mkt_cb = ttk.Combobox(r1, textvariable=self.analyze_market_var,
                              width=10,
                              values=["KR", "NASD", "NYSE", "AMEX", "SEHK", "TKSE"],
                              state="readonly")
        mkt_cb.pack(side=tk.LEFT, padx=(8, 18))

        self._make_label(r1, "코드").pack(side=tk.LEFT)
        self.analyze_code = self._make_entry(r1, width=10)
        self.analyze_code.pack(side=tk.LEFT, padx=(8, 18))
        self.analyze_code.insert(0, "005930")

        self._make_label(r1, "종목명").pack(side=tk.LEFT)
        self.analyze_name = self._make_entry(r1, width=12)
        self.analyze_name.pack(side=tk.LEFT, padx=(8, 18))
        self.analyze_name.insert(0, "삼성전자")

        self.analyze_btn = self._make_button(r1, "  AI 분석  ",
                                              self._run_analysis, c["accent2"])
        self.analyze_btn.pack(side=tk.LEFT, padx=(4, 8))
        self.analyze_stop_btn = self._make_button(r1, " 중지 ",
                                                   self._stop_analysis,
                                                   c["red_dim"])
        self.analyze_stop_btn.configure(state=tk.DISABLED,
                                         font=(self.FONT, 10))
        self.analyze_stop_btn.pack(side=tk.LEFT)

        tk.Label(search_card,
                 text="KR: 005930(삼성전자)  |  NASD: AAPL(애플), TSLA  |  NYSE: BRK.B  |  SEHK: 00700",
                 font=(self.FONT, 9), bg=c["card"], fg=c["dim2"]).pack(anchor=tk.W)

        result_card = self._make_card(frame)
        result_card.pack(fill=tk.BOTH, expand=True, padx=24, pady=(4, 18))
        self.analysis_text = scrolledtext.ScrolledText(
            result_card, font=(self.MONO, 10), wrap=tk.WORD,
            bg=c["bg"], fg=c["fg2"], relief=tk.FLAT,
            insertbackground=c["accent"],
            selectbackground=c["accent_soft"],
            highlightthickness=0,
        )
        self.analysis_text.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)

    # ═══════════════════════════════════════════════
    # AI 어시스턴트 탭 (NEW)
    # ═══════════════════════════════════════════════

    def _build_chat_tab(self):
        frame = tk.Frame(self.notebook, bg=self.c["bg"])
        self.notebook.add(frame, text="   AI 어시스턴트   ")
        c = self.c

        # 상단 안내
        info_f = tk.Frame(frame, bg=c["bg2"])
        info_f.pack(fill=tk.X, padx=0, pady=0)
        tk.Label(info_f,
                 text="  AI에게 매매 전략, 종목 분석, 시장 상황 등을 질문하세요",
                 font=(self.FONT, 10), bg=c["bg2"], fg=c["dim"],
                 pady=8).pack(side=tk.LEFT, padx=16)

        # 빠른 질문 버튼
        quick_f = tk.Frame(frame, bg=c["bg"])
        quick_f.pack(fill=tk.X, padx=24, pady=(12, 8))
        quick_questions = [
            ("현재 포트폴리오 분석", "내 현재 포트폴리오 상태를 분석하고 조언해줘"),
            ("오늘 시장 전망", "오늘 한국 주식 시장 전망은 어때? 어떤 전략이 좋을까?"),
            ("매매 전략 추천", "지금 시점에서 가장 효과적인 매매 전략을 추천해줘"),
            ("리스크 점검", "현재 설정된 리스크 관리 파라미터가 적절한지 점검해줘"),
        ]
        for label, question in quick_questions:
            btn = tk.Button(quick_f, text=label,
                           font=(self.FONT, 9),
                           bg=c["accent_soft"], fg=c["accent"],
                           relief=tk.FLAT, padx=12, pady=4,
                           cursor="hand2",
                           activebackground=c["card"],
                           activeforeground=c["fg"],
                           command=lambda q=question: self._send_quick_chat(q))
            btn.pack(side=tk.LEFT, padx=(0, 8))

        # 채팅 디스플레이
        chat_card = self._make_card(frame)
        chat_card.pack(fill=tk.BOTH, expand=True, padx=24, pady=(4, 8))

        self.chat_display = tk.Text(
            chat_card, font=(self.FONT, 10), wrap=tk.WORD,
            bg=c["bg"], fg=c["fg2"], relief=tk.FLAT,
            state=tk.DISABLED, highlightthickness=0,
            padx=16, pady=12, spacing3=4,
        )
        chat_sb = ttk.Scrollbar(chat_card, orient=tk.VERTICAL,
                                command=self.chat_display.yview)
        self.chat_display.configure(yscrollcommand=chat_sb.set)
        self.chat_display.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        chat_sb.pack(side=tk.RIGHT, fill=tk.Y)

        # 채팅 텍스트 태그 설정
        self.chat_display.tag_configure("user_name",
                                         foreground=c["accent"],
                                         font=(self.FONT, 10, "bold"))
        self.chat_display.tag_configure("ai_name",
                                         foreground=c["green"],
                                         font=(self.FONT, 10, "bold"))
        self.chat_display.tag_configure("user_msg",
                                         foreground=c["fg"],
                                         font=(self.FONT, 10),
                                         lmargin1=16, lmargin2=16,
                                         spacing3=8)
        self.chat_display.tag_configure("ai_msg",
                                         foreground=c["fg2"],
                                         font=(self.FONT, 10),
                                         lmargin1=16, lmargin2=16,
                                         spacing3=8)
        self.chat_display.tag_configure("system_msg",
                                         foreground=c["dim"],
                                         font=(self.FONT, 9, "italic"),
                                         justify=tk.CENTER,
                                         spacing3=8)
        self.chat_display.tag_configure("divider",
                                         foreground=c["border"],
                                         font=("", 2))

        # 초기 환영 메시지
        self._add_chat_system("OSHMS AI 어시스턴트에 오신 것을 환영합니다.")
        self._add_chat_system("매매 전략, 종목 분석, 시장 상황 등 무엇이든 질문하세요.")

        # 입력 영역
        input_f = tk.Frame(frame, bg=c["bg"])
        input_f.pack(fill=tk.X, padx=24, pady=(0, 18))

        input_card = tk.Frame(input_f, bg=c["input_bg"],
                              highlightbackground=c["input_border"],
                              highlightthickness=1,
                              highlightcolor=c["accent"])
        input_card.pack(fill=tk.X)

        self.chat_input = tk.Entry(input_card, font=(self.FONT, 11),
                                   bg=c["input_bg"], fg=c["fg"],
                                   relief=tk.FLAT,
                                   insertbackground=c["accent"])
        self.chat_input.pack(side=tk.LEFT, fill=tk.X, expand=True,
                            padx=(14, 8), pady=10)
        self.chat_input.bind("<Return>", lambda e: self._send_chat())

        self.chat_send_btn = tk.Button(input_card, text="전송",
                                        font=(self.FONT, 10, "bold"),
                                        bg=c["accent"], fg="#ffffff",
                                        relief=tk.FLAT, padx=20, pady=6,
                                        cursor="hand2",
                                        activebackground=c["accent2"],
                                        activeforeground="#ffffff",
                                        command=self._send_chat)
        self.chat_send_btn.pack(side=tk.RIGHT, padx=(0, 6), pady=6)

    def _add_chat_system(self, text):
        """시스템 메시지를 채팅에 표시."""
        self.chat_display.configure(state=tk.NORMAL)
        self.chat_display.insert(tk.END, f"{text}\n", "system_msg")
        self.chat_display.configure(state=tk.DISABLED)
        self.chat_display.see(tk.END)

    def _add_chat_message(self, role, text):
        """사용자/AI 메시지를 채팅에 표시."""
        self.chat_display.configure(state=tk.NORMAL)

        if role == "user":
            self.chat_display.insert(tk.END, "\n나  ", "user_name")
            self.chat_display.insert(tk.END, f"{text}\n", "user_msg")
        else:
            self.chat_display.insert(tk.END, "\nAI  ", "ai_name")
            self.chat_display.insert(tk.END, f"{text}\n", "ai_msg")

        self.chat_display.insert(tk.END,
                                  "─" * 60 + "\n", "divider")
        self.chat_display.configure(state=tk.DISABLED)
        self.chat_display.see(tk.END)

    def _send_quick_chat(self, question):
        """빠른 질문 버튼 클릭."""
        self.chat_input.delete(0, tk.END)
        self.chat_input.insert(0, question)
        self._send_chat()

    def _send_chat(self):
        """사용자 메시지를 전송하고 AI 응답을 받는다."""
        msg = self.chat_input.get().strip()
        if not msg:
            return

        self.chat_input.delete(0, tk.END)
        self._add_chat_message("user", msg)
        self._chat_messages.append(("user", msg))

        # UI 비활성화
        self.chat_send_btn.configure(state=tk.DISABLED, text="생각중...")

        def _get_response():
            try:
                response = self._get_ai_response(msg)
                self._chat_messages.append(("assistant", response))
                self.root.after(0, lambda: self._add_chat_message("ai", response))
            except Exception as e:
                err = f"오류가 발생했습니다: {e}"
                self.root.after(0, lambda: self._add_chat_message("ai", err))
            finally:
                self.root.after(0, lambda: self.chat_send_btn.configure(
                    state=tk.NORMAL, text="전송"))

        self._chat_thread = threading.Thread(target=_get_response, daemon=True)
        self._chat_thread.start()

    def _get_ai_response(self, user_msg: str) -> str:
        """AI API를 호출하여 응답을 받는다."""
        # 시스템 컨텍스트 구성
        context = self._build_chat_context()

        # OpenAI API 키 확인
        api_key = self.settings.openai_api_key
        if not api_key:
            api_key = self.user_prefs.get("openai_api_key", "")
        anthropic_key = self.user_prefs.get("anthropic_api_key", "")

        if api_key:
            return self._call_openai(api_key, context, user_msg)
        elif anthropic_key:
            return self._call_anthropic(anthropic_key, context, user_msg)
        else:
            return self._rule_based_response(user_msg, context)

    def _build_chat_context(self) -> str:
        """현재 시스템 상태를 요약한 컨텍스트를 구성한다."""
        lines = ["당신은 OSHMS 주식 자동매매 시스템의 AI 어시스턴트입니다.",
                 "한국어로 답변하세요. 간결하고 실용적인 조언을 제공하세요.",
                 "",
                 "=== 시스템 상태 ==="]

        # 설정 정보
        lines.append(f"모드: {'모의투자' if self.settings.is_mock else '실전투자'}")
        lines.append(f"최대 매수금액: {self.settings.max_buy_amount:,}원")
        lines.append(f"최대 보유종목: {self.settings.max_hold_count}개")
        lines.append(f"손절: {self.settings.stop_loss_pct}% / 익절: {self.settings.take_profit_pct}%")
        lines.append(f"매매시간: {self.settings.trading_start_time}~{self.settings.trading_end_time}")

        # 거래 기록
        trade_file = Path("logs/trades.json")
        if trade_file.exists():
            try:
                data = json.loads(trade_file.read_text(encoding="utf-8"))
                recent = data[-10:]
                total_pl = sum(t.get("profit_loss", 0) for t in data if t.get("side") == "SELL")
                wins = sum(1 for t in data if t.get("side") == "SELL" and t.get("profit_loss", 0) > 0)
                sells = sum(1 for t in data if t.get("side") == "SELL")
                wr = (wins / sells * 100) if sells > 0 else 0
                lines.append(f"\n총 거래: {len(data)}건 | 매도: {sells}건 | 승률: {wr:.1f}%")
                lines.append(f"누적 손익: {total_pl:+,}원")
                if recent:
                    lines.append("\n최근 거래:")
                    for t in recent[-5:]:
                        pl = f" → {t['profit_loss']:+,}원" if t.get("side") == "SELL" else ""
                        lines.append(f"  {t.get('timestamp','')[:16]} {t.get('side','')} "
                                    f"{t.get('stock_name','')} {t.get('quantity',0)}주{pl}")
            except (json.JSONDecodeError, OSError):
                pass

        # 진화 상태
        evo_file = Path("data/evolution_state.json")
        if evo_file.exists():
            try:
                evo = json.loads(evo_file.read_text(encoding="utf-8"))
                lines.append(f"\n진화 세대: #{evo.get('generation', 0)}")
                lines.append(f"최고 적합도: {evo.get('best_fitness', 0):.1f}")
            except (json.JSONDecodeError, OSError):
                pass

        return "\n".join(lines)

    def _call_openai(self, api_key: str, context: str, user_msg: str) -> str:
        """OpenAI API 호출."""
        try:
            from openai import OpenAI
        except ImportError:
            try:
                import subprocess, sys
                subprocess.check_call(
                    [sys.executable, "-m", "pip", "install", "openai"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                from openai import OpenAI
            except Exception:
                return ("openai 패키지가 설치되어 있지 않습니다.\n\n"
                        "터미널에서 다음 명령어를 실행해주세요:\n"
                        "  pip install openai")
        try:
            client = OpenAI(api_key=api_key)

            messages = [{"role": "system", "content": context}]
            # 최근 대화 히스토리 (마지막 10개)
            for role, content in self._chat_messages[-10:]:
                messages.append({"role": role, "content": content})

            resp = client.chat.completions.create(
                model=self.settings.openai_model or "gpt-4o-mini",
                messages=messages,
                max_tokens=1000,
                temperature=0.7,
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            return f"OpenAI API 오류: {e}\n\n설정에서 API 키를 확인해주세요."

    def _call_anthropic(self, api_key: str, context: str, user_msg: str) -> str:
        """Anthropic API 호출."""
        try:
            import anthropic
        except ImportError:
            # 자동 설치 시도
            try:
                import subprocess, sys
                subprocess.check_call(
                    [sys.executable, "-m", "pip", "install", "anthropic"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                import anthropic
            except Exception:
                return ("anthropic 패키지가 설치되어 있지 않습니다.\n\n"
                        "터미널에서 다음 명령어를 실행해주세요:\n"
                        "  pip install anthropic\n\n"
                        "또는 설정에서 OpenAI API 키를 사용하시면 별도 설치 없이 이용 가능합니다.")
        try:
            client = anthropic.Anthropic(api_key=api_key)

            messages = []
            for role, content in self._chat_messages[-10:]:
                messages.append({"role": role, "content": content})

            resp = client.messages.create(
                model="claude-sonnet-4-5-20250929",
                max_tokens=1000,
                system=context,
                messages=messages,
            )
            return resp.content[0].text.strip()
        except Exception as e:
            err = str(e)
            if "credit balance" in err.lower() or "billing" in err.lower():
                return ("Anthropic API 크레딧이 부족합니다.\n\n"
                        "해결 방법:\n"
                        "1. console.anthropic.com → Plans & Billing에서 크레딧 충전\n"
                        "2. 또는 설정에서 OpenAI API 키를 입력하여 사용\n\n"
                        "OpenAI API 키는 platform.openai.com에서 발급받을 수 있습니다.")
            if "invalid" in err.lower() and "api" in err.lower():
                return ("Anthropic API 키가 유효하지 않습니다.\n\n"
                        "설정에서 API 키를 확인해주세요.\n"
                        "console.anthropic.com → API Keys에서 확인 가능합니다.")
            return f"Anthropic API 오류: {e}"

    def _rule_based_response(self, msg: str, context: str) -> str:
        """API 키가 없을 때 규칙 기반 응답."""
        msg_lower = msg.lower()

        if "포트폴리오" in msg or "잔고" in msg or "보유" in msg:
            return ("포트폴리오 분석을 위해서는 먼저 대시보드의 '새로고침' 버튼으로 "
                    "잔고를 갱신해주세요.\n\n"
                    "더 상세한 AI 분석을 원하시면 설정에서 OpenAI API 키를 입력하세요.\n"
                    "OpenAI API 키는 platform.openai.com에서 발급받을 수 있습니다.")

        if "시장" in msg or "전망" in msg or "오늘" in msg:
            return ("실시간 시장 분석은 AI API가 필요합니다.\n\n"
                    "현재 시스템 전략 설정:\n"
                    f"• 손절: {self.settings.stop_loss_pct}%\n"
                    f"• 익절: {self.settings.take_profit_pct}%\n"
                    f"• 최대 보유: {self.settings.max_hold_count}종목\n\n"
                    "AI 기능을 활성화하려면 설정에서 OpenAI 또는 Anthropic API 키를 입력하세요.")

        if "전략" in msg or "추천" in msg or "어떻게" in msg:
            return ("현재 OSHMS는 Expert 전략을 사용 중입니다.\n\n"
                    "Expert 전략 구성:\n"
                    "• 기술적 분석 40% (추세, MACD, RSI, 볼린저)\n"
                    "• 가격위치 분석 20% (지지/저항, VWAP)\n"
                    "• 시장환경 15% (KOSPI/KOSDAQ 레짐)\n"
                    "• 캔들 패턴 15% (망치형, 장악형 등)\n"
                    "• 뉴스 감성 10% (네이버 뉴스 분석)\n\n"
                    "더 정교한 전략 조언은 OpenAI API 키를 설정하면 가능합니다.")

        if "리스크" in msg or "손절" in msg or "위험" in msg:
            return (f"현재 리스크 설정:\n"
                    f"• 손절: {self.settings.stop_loss_pct}% (ATR 기반 동적 조절)\n"
                    f"• 익절: {self.settings.take_profit_pct}% (ATR 기반 동적 조절)\n"
                    f"• 트레일링 스탑: 수익률 구간별 차등 (1.5~2.5%)\n"
                    f"• 장마감 자동 청산: 15:15\n"
                    f"• 손절 후 쿨다운: 10분\n\n"
                    f"1회 최대 매수: {self.settings.max_buy_amount:,}원\n"
                    f"최대 보유: {self.settings.max_hold_count}종목")

        return ("AI 어시스턴트를 사용하려면 설정에서 API 키를 입력해주세요.\n\n"
                "지원 API:\n"
                "• OpenAI (GPT-4o) - platform.openai.com\n"
                "• Anthropic (Claude) - console.anthropic.com\n\n"
                "위 버튼들로 빠른 질문도 가능합니다.")

    # ═══════════════════════════════════════════════
    # 설정 탭
    # ═══════════════════════════════════════════════

    def _build_settings_tab(self):
        frame = tk.Frame(self.notebook, bg=self.c["bg"])
        self.notebook.add(frame, text="   설정   ")
        inner, self._settings_canvas = self._make_scroll_frame(frame)
        c = self.c

        self.setting_vars = {}

        self._add_settings_section(inner, "한국투자증권 API")
        self._add_field(inner, "APP Key", "app_key", show="*")
        self._add_field(inner, "APP Secret", "app_secret", show="*")
        self._add_field(inner, "계좌번호 (예: 12345678-01)", "account_no")
        self._add_checkbox(inner, "모의투자 모드", "is_mock")

        self._add_settings_section(inner, "매매 설정")
        self._add_field(inner, "1회 최대 매수금액 (원)", "max_buy_amount")
        self._add_field(inner, "최대 보유 종목 수", "max_hold_count")
        self._add_field(inner, "손절 비율 (%)", "stop_loss_pct")
        self._add_field(inner, "익절 비율 (%)", "take_profit_pct")
        self._add_field(inner, "매매 시작 시간 (HH:MM)", "trading_start_time")
        self._add_field(inner, "매매 종료 시간 (HH:MM)", "trading_end_time")

        self._add_settings_section(inner, "AI 분석 설정")
        self._add_field(inner, "OpenAI API Key", "openai_api_key", show="*")
        self._add_field(inner, "Anthropic API Key", "anthropic_api_key", show="*")
        tk.Label(inner,
                 text="  API 키 없어도 규칙 기반 AI 분석이 자동으로 사용됩니다.",
                 font=(self.FONT, 9), bg=c["bg"], fg=c["dim"]
                 ).pack(anchor=tk.W, padx=36, pady=(0, 6))

        self._add_settings_section(inner, "네이버 검색 API (선택)")
        self._add_field(inner, "Client ID", "naver_client_id")
        self._add_field(inner, "Client Secret", "naver_client_secret", show="*")

        self._add_settings_section(inner, "자가 학습 / 자동 진화")
        self._add_checkbox(inner, "자동 전략 최적화 활성화", "auto_optimize")
        self._add_checkbox(inner, "자동 진화 활성화", "auto_evolve")
        self._add_field(inner, "학습 주기 (거래 건수)", "learn_interval")

        # 저장/검증 버튼
        btn_f = tk.Frame(inner, bg=c["bg"])
        btn_f.pack(fill=tk.X, padx=32, pady=(28, 36))
        self.save_btn = self._make_button(btn_f, "  설정 저장  ",
                                          self._save_settings)
        self.save_btn.configure(font=(self.FONT, 12, "bold"), padx=28, pady=10)
        self.save_btn.pack(side=tk.LEFT, padx=(0, 12))
        self.validate_btn = self._make_button(btn_f, "  설정 검증  ",
                                              self._validate_settings,
                                              c["surface"])
        self.validate_btn.configure(fg=c["fg"])
        self.validate_btn.pack(side=tk.LEFT)

    def _add_settings_section(self, parent, title):
        c = self.c
        f = tk.Frame(parent, bg=c["bg"])
        f.pack(fill=tk.X, padx=28, pady=(24, 6))
        tk.Label(f, text=title, font=(self.FONT, 13, "bold"),
                 bg=c["bg"], fg=c["fg"]).pack(side=tk.LEFT)
        sep = tk.Frame(f, bg=c["border"], height=1)
        sep.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(16, 0), pady=1)

    def _add_field(self, parent, label, key, show=""):
        c = self.c
        f = tk.Frame(parent, bg=c["bg"])
        f.pack(fill=tk.X, padx=36, pady=4)
        tk.Label(f, text=label, font=(self.FONT, 10), bg=c["bg"],
                 fg=c["fg2"], width=28, anchor=tk.W).pack(side=tk.LEFT)
        var = tk.StringVar()
        self._make_entry(f, var=var, width=35, show=show).pack(side=tk.LEFT, padx=(10, 0))
        self.setting_vars[key] = var

    def _add_checkbox(self, parent, label, key):
        c = self.c
        var = tk.BooleanVar()
        cb = tk.Checkbutton(parent, text=label, variable=var,
                            font=(self.FONT, 10),
                            bg=c["bg"], fg=c["fg2"],
                            selectcolor=c["surface"],
                            activebackground=c["bg"],
                            activeforeground=c["fg"])
        cb.pack(anchor=tk.W, padx=36, pady=4)
        self.setting_vars[key] = var

    # ═══════════════════════════════════════════════
    # 테마 적용
    # ═══════════════════════════════════════════════

    def _apply_theme(self):
        c = self.c
        style = ttk.Style()
        style.theme_use("clam")

        # 노트북 탭
        style.configure("TNotebook", background=c["bg"], borderwidth=0)
        style.configure("TNotebook.Tab",
                        background=c["bg2"], foreground=c["dim"],
                        padding=[22, 10],
                        font=(self.FONT, 10, "bold"))
        style.map("TNotebook.Tab",
                  background=[("selected", c["surface"])],
                  foreground=[("selected", c["accent"])])

        # 트리뷰
        style.configure("Treeview",
                        background=c["bg"], foreground=c["fg"],
                        fieldbackground=c["bg"], borderwidth=0,
                        font=(self.FONT, 10), rowheight=30)
        style.configure("Treeview.Heading",
                        background=c["surface"], foreground=c["dim"],
                        font=(self.FONT, 9, "bold"), borderwidth=0)
        style.map("Treeview",
                  background=[("selected", c["accent_soft"])],
                  foreground=[("selected", c["fg"])])

        # 콤보박스
        style.configure("TCombobox",
                        fieldbackground=c["input_bg"],
                        background=c["card"], foreground=c["fg"])

        # 스크롤바
        style.configure("Vertical.TScrollbar",
                        background=c["surface"],
                        troughcolor=c["bg"], borderwidth=0,
                        arrowcolor=c["dim"])

    # ═══════════════════════════════════════════════
    # 진화 상세
    # ═══════════════════════════════════════════════

    def _load_evolution_details(self):
        """진화 엔진 상태를 상세히 표시한다."""
        self.evo_detail_text.configure(state=tk.NORMAL)
        self.evo_detail_text.delete("1.0", tk.END)

        evo_file = Path("data/evolution_state.json")
        if not evo_file.exists():
            self.evo_detail_text.insert(tk.END,
                "진화 엔진 대기중\n\n"
                "진화 엔진은 거래가 쌓이면 자동으로 작동합니다.\n"
                "매 15건 거래마다 진화 사이클이 실행됩니다.\n\n"
                "진화 과정:\n"
                " 1. 거래 패턴 학습 → 인사이트 발견\n"
                " 2. 매매 규칙 자동 생성/업데이트\n"
                " 3. 지표별 가중치 성과 기반 조정\n"
                " 4. 전략 파라미터 백테스트 최적화\n"
                " 5. 적합도(fitness) 평가 → 세대 기록")
        else:
            try:
                data = json.loads(evo_file.read_text(encoding="utf-8"))
                gen = data.get("generation", 0)
                best = data.get("best_fitness", 0)
                best_gen = data.get("best_generation", 0)
                last = data.get("last_evolution", "없음")
                rules = data.get("active_rules", [])
                fh = data.get("fitness_history", [])
                wh = data.get("weight_history", [])

                txt = f"세대: #{gen}  |  최고 적합도: {best:.1f} (#{best_gen})  |  마지막: {last}\n"
                txt += f"활성 규칙: {len(rules)}개  |  가중치 조정 이력: {len(wh)}건\n\n"

                if fh:
                    recent = fh[-5:]
                    txt += "최근 적합도 추이:\n"
                    for f in recent:
                        bar_len = int(f["fitness"] / 5)
                        bar = "█" * bar_len + "░" * (20 - bar_len)
                        txt += f"  #{f['generation']:3d}  {bar}  {f['fitness']:5.1f}\n"

                if rules:
                    txt += f"\n활성 규칙 ({len(rules)}개):\n"
                    for r in rules[:5]:
                        conf = r.get("confidence", 0)
                        txt += f"  [{r.get('type','')}] {r.get('action','')} (신뢰도: {conf:.0%})\n"
                    if len(rules) > 5:
                        txt += f"  ... 외 {len(rules) - 5}개\n"

                self.evo_detail_text.insert(tk.END, txt)
            except Exception:
                self.evo_detail_text.insert(tk.END, "진화 상태 로드 실패")

        self.evo_detail_text.configure(state=tk.DISABLED)

    # ═══════════════════════════════════════════════
    # 설정 로드/저장
    # ═══════════════════════════════════════════════

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

    def _load_state_to_dashboard(self):
        try:
            from trading.state_manager import StateManager
            self._state_mgr = StateManager()

            resume = self._state_mgr.get_resume_info()
            if resume["last_active"]:
                self.resume_label.config(
                    text=f"이전: {resume['strategy']} ({resume['market']}) | {resume['last_active']}")
                self.resume_btn.pack(side=tk.LEFT, padx=10)
        except Exception:
            pass

        self._load_trade_history()
        self._update_cumulative_stats()

    def _update_cumulative_stats(self):
        """logs/trades.json에서 누적 통계를 직접 계산."""
        trade_file = Path("logs/trades.json")
        if not trade_file.exists():
            return
        try:
            data = json.loads(trade_file.read_text(encoding="utf-8"))
            sells = [t for t in data if t.get("side") == "SELL"]
            if not sells:
                return

            total_sells = len(sells)
            wins = sum(1 for t in sells if t.get("profit_loss", 0) > 0)
            total_profit = sum(t.get("profit_loss", 0) for t in sells)
            profits = [t.get("profit_loss", 0) for t in sells]
            best = max(profits) if profits else 0
            win_rate = (wins / total_sells * 100) if total_sells > 0 else 0

            c = self.c
            self.stat_labels["cum_trades"].config(text=f"{total_sells} 건")
            self.stat_labels["cum_wins"].config(
                text=f"{win_rate:.1f}%",
                fg=c["green"] if win_rate >= 50 else c["red"])
            self.stat_labels["cum_profit"].config(
                text=f"{total_profit:+,.0f} 원",
                fg=c["green"] if total_profit >= 0 else c["red"])
            self.stat_labels["best_trade"].config(
                text=f"{best:+,.0f} 원")

            # 진화 세대 표시
            evo_file = Path("data/evolution_state.json")
            if evo_file.exists():
                try:
                    evo = json.loads(evo_file.read_text(encoding="utf-8"))
                    gen = evo.get("generation", 0)
                    self.stat_labels["evo_gen"].config(text=f"#{gen}")
                except Exception:
                    pass
        except (json.JSONDecodeError, OSError):
            pass

    def _load_trade_history(self):
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
                    ts, t.get("stock_name", ""), t.get("side", ""),
                    t.get("quantity", 0), f"{t.get('price', 0):,}",
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
            "auto_optimize": self.setting_vars.get("auto_optimize", tk.BooleanVar(value=True)).get(),
            "auto_evolve": self.setting_vars.get("auto_evolve", tk.BooleanVar(value=True)).get(),
            "learn_interval": self.setting_vars.get("learn_interval", tk.StringVar(value="20")).get(),
            "naver_client_id": self.setting_vars.get("naver_client_id", tk.StringVar()).get(),
            "naver_client_secret": self.setting_vars.get("naver_client_secret", tk.StringVar()).get(),
            "anthropic_api_key": self.setting_vars.get("anthropic_api_key", tk.StringVar()).get(),
        }
        self.SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        self.SETTINGS_FILE.write_text(
            json.dumps(prefs, ensure_ascii=False, indent=2), encoding="utf-8")

        self.settings = Settings.from_env()
        self._reset_api()  # 설정 변경 시 API 재연결
        messagebox.showinfo("설정", "설정이 저장되었습니다.")

    def _validate_settings(self):
        errors = self.settings.validate()
        if errors:
            messagebox.showwarning("설정 검증", "\n".join(errors))
        else:
            messagebox.showinfo("설정 검증", "모든 설정이 유효합니다.")

    # ═══════════════════════════════════════════════
    # 대시보드 액션
    # ═══════════════════════════════════════════════

    def _on_market_changed(self, _event=None):
        code = self._get_market_code()
        hours, has_ext = self.MARKET_HOURS.get(code, ("--", False))
        ext = " + 시간외" if has_ext else ""
        self.market_hours_lbl.config(text=f"{hours}{ext}")

    def _get_market_code(self) -> str:
        display = self.market_var.get()
        for code, name in self.MARKETS.items():
            if display == name:
                return code
        return "KR"

    def _get_api(self):
        """KISApi 인스턴스를 캐싱하여 반환 (토큰 재발급 방지)."""
        if self._kis_api is None:
            from api.kis_api import KISApi
            self._kis_api = KISApi(self.settings)
        return self._kis_api

    def _reset_api(self):
        """설정 변경 시 API 인스턴스 초기화."""
        self._kis_api = None

    def _refresh_balance(self):
        errors = self.settings.validate()
        if errors:
            messagebox.showwarning("오류", "먼저 설정에서 API 키를 입력하세요.")
            return

        def _fetch():
            try:
                api = self._get_api()
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
                        os_summary.get("total_profit_loss", 0))
                except Exception:
                    pass
                balance["holdings"] = kr_holdings
                self.root.after(0, lambda: self._update_dashboard(balance))
            except Exception as e:
                err_msg = str(e)
                self.root.after(0, lambda: messagebox.showerror("오류", err_msg))

        threading.Thread(target=_fetch, daemon=True).start()

    def _update_dashboard(self, balance):
        c = self.c
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
            fg=c["green"] if total_pl >= 0 else c["red"])
        self.card_labels["profit_rate"].config(
            text=f"{rate:+.2f}%",
            fg=c["green"] if rate >= 0 else c["red"])
        self.card_labels["hold_count"].config(text=f"{len(holdings)} 개")
        self.holdings_count_lbl.config(text=f"{len(holdings)} 종목")

        for item in self.holdings_tree.get_children():
            self.holdings_tree.delete(item)
        for h in holdings:
            self.holdings_tree.insert("", tk.END, values=(
                h["stock_name"], h.get("market", "KR"), h["quantity"],
                f"{h['avg_price']:,.0f}", f"{h['current_price']:,.0f}",
                f"{h['profit_loss']:+,.0f}", f"{h['profit_rate']:+.2f}%"))

        self._load_trade_history()
        self._load_evolution_details()
        self._update_cumulative_stats()

    def _auto_refresh(self):
        if self._is_trading and self.auto_refresh_var.get():
            self._refresh_balance()
        if self._is_trading:
            self._auto_refresh_id = self.root.after(30000, self._auto_refresh)

    # ═══════════════════════════════════════════════
    # 자동매매 액션
    # ═══════════════════════════════════════════════

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
        self.status_label.config(text="대기중", fg=self.c["dim"])
        self.status_dot.config(fg=self.c["dim2"])
        self._log("자동매매 중지됨")
        self._refresh_balance()

    def _trading_loop(self):
        import logging

        class GUILogHandler(logging.Handler):
            def __init__(self, callback):
                super().__init__()
                self.callback = callback
            def emit(self, record):
                self.callback(self.format(record))

        handler = GUILogHandler(lambda msg: self.root.after(0, self._log, msg))
        handler.setFormatter(logging.Formatter(
            "[%(asctime)s] %(message)s", datefmt="%H:%M:%S"))
        logging.getLogger("oshms").addHandler(handler)

        try:
            from strategy import (ExpertStrategy, ScalpingStrategy,
                                  MomentumStrategy, CombinedStrategy)
            from trading.trader import AutoTrader
            from trading.state_manager import StateManager

            self.settings = Settings.from_env()
            self._reset_api()
            api = self._get_api()
            strategy_map = {
                "expert": lambda: ExpertStrategy(api=api, settings=self.settings),
                "scalping": ScalpingStrategy,
                "momentum": MomentumStrategy,
                "combined": CombinedStrategy,
            }
            name = self.strategy_var.get()
            strategy = strategy_map.get(name, strategy_map["expert"])()
            self._trader = AutoTrader(api, self.settings, strategy)

            stocks_text = self.stocks_entry.get().strip()
            target = None
            if stocks_text and stocks_text != "자동선정":
                target = [s.strip() for s in stocks_text.split(",")]
            interval = int(self.interval_var.get())
            market = self._get_market_code()

            if not self._state_mgr:
                self._state_mgr = StateManager()
            self._state_mgr.start_session(name, target or [], interval, market)
            gen = self._state_mgr.state.evolution_generation
            self.root.after(0, lambda: self.evo_label.config(
                text=f"진화 #{gen}"))
            self._trader.start(target_stocks=target, interval=interval)
        except Exception as e:
            err_msg = str(e)
            self.root.after(0, lambda: self._log(f"오류: {err_msg}"))
            self.root.after(0, self._stop_trading)
        finally:
            logging.getLogger("oshms").removeHandler(handler)

    def _log(self, message: str):
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
        lines = int(self.log_text.index("end-1c").split(".")[0])
        if lines > 500:
            self.log_text.delete("1.0", f"{lines - 400}.0")

    # ═══════════════════════════════════════════════
    # 종목분석 액션
    # ═══════════════════════════════════════════════

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

        self._analysis_cancel.clear()
        self.analyze_btn.config(state=tk.DISABLED)
        self.analyze_stop_btn.config(state=tk.NORMAL)
        self.analysis_text.delete("1.0", tk.END)
        self.analysis_text.insert(tk.END,
                                  f"[{market}] {name}({code}) AI 분석 중...\n")

        cancel = self._analysis_cancel

        def _on_done():
            self.analyze_btn.config(state=tk.NORMAL)
            self.analyze_stop_btn.config(state=tk.DISABLED)

        def _analyze():
            try:
                from strategy.expert import ExpertStrategy
                from strategy.market_context import MarketContextAnalyzer

                if cancel.is_set():
                    return
                api = self._get_api()
                strategy = ExpertStrategy(api=api, settings=self.settings)
                try:
                    ctx = MarketContextAnalyzer(api).analyze()
                    strategy.set_market_context(ctx)
                except Exception:
                    pass
                if cancel.is_set():
                    return

                if market == "KR":
                    current_price = api.get_current_price(code)
                    candles = api.get_minute_chart(code, period="3")
                    if len(candles) < 20:
                        candles = api.get_daily_chart(code, count=60)
                else:
                    price_data = api.get_overseas_price(market, code)
                    if not price_data:
                        self.root.after(0, lambda: self.analysis_text.insert(
                            tk.END, "해외 시세 조회 실패\n"))
                        return
                    current_price = {
                        "price": int(price_data["price"]) if price_data["price"] > 100 else price_data["price"],
                        "stock_name": name, "stock_code": code,
                        "open": price_data["open"], "high": price_data["high"],
                        "low": price_data["low"], "volume": price_data["volume"],
                        "change_rate": price_data["change_rate"],
                    }
                    candles = api.get_overseas_daily_chart(market, code, count=60)
                if cancel.is_set():
                    return
                if not current_price:
                    self.root.after(0, lambda: self.analysis_text.insert(
                        tk.END, "시세 조회 실패\n"))
                    return
                current_price["stock_name"] = name
                if not candles:
                    self.root.after(0, lambda: self.analysis_text.insert(
                        tk.END, "차트 데이터 조회 실패\n"))
                    return

                analysis = strategy.full_analysis(code, name, candles, current_price)
                if cancel.is_set():
                    return
                result = f"[시장: {market}]\n" + analysis.summary()

                t = analysis.technical
                if t:
                    result += "\n\n══ 기술적 지표 ══"
                    result += f"\n  SMA(5/20/60): {t.sma_5:,.0f} / {t.sma_20:,.0f} / {t.sma_60:,.0f}"
                    result += f"\n  RSI: {t.rsi:.1f}  |  MACD: {t.macd_line:,.0f} (Signal: {t.macd_signal:,.0f})"
                    result += f"\n  Stochastic: K={t.stoch_k:.1f}  D={t.stoch_d:.1f}"
                    result += f"\n  BB: {t.bb_lower:,.0f} ~ {t.bb_middle:,.0f} ~ {t.bb_upper:,.0f} (폭={t.bb_width:.1f}%)"
                    result += f"\n  VWAP: {t.vwap:,.0f}  |  ATR: {t.atr:,.0f} ({t.atr_pct:.2f}%)"
                    result += f"\n  일목균형: {t.ichimoku_signal}  |  거래량비: x{t.volume_ratio:.1f}"
                    if t.support_levels:
                        result += f"\n  지지선: {', '.join(f'{s:,.0f}' for s in t.support_levels)}"
                    if t.resistance_levels:
                        result += f"\n  저항선: {', '.join(f'{r:,.0f}' for r in t.resistance_levels)}"

                if analysis.sentiment and analysis.sentiment.key_headlines:
                    result += "\n\n══ 최신 뉴스 ══"
                    for h in analysis.sentiment.key_headlines:
                        result += f"\n  {h}"

                if cancel.is_set():
                    return

                try:
                    from strategy.ai_analyst import AIAnalyst
                    ai = AIAnalyst(api_key=self.settings.openai_api_key)
                    ai_data = AIAnalyst.extract_analysis_data(analysis)
                    ai_result = ai.analyze(ai_data)
                    result += "\n\n" + ai_result.format_report()
                except Exception as ai_err:
                    result += f"\n\nAI 분석: {ai_err}"

                if cancel.is_set():
                    return
                self.root.after(0, lambda: (
                    self.analysis_text.delete("1.0", tk.END),
                    self.analysis_text.insert(tk.END, result)))
            except Exception as e:
                err_msg = str(e)
                if not cancel.is_set():
                    self.root.after(0, lambda: self.analysis_text.insert(
                        tk.END, f"\n오류: {err_msg}\n"))
            finally:
                self.root.after(0, _on_done)

        self._analysis_thread = threading.Thread(target=_analyze, daemon=True)
        self._analysis_thread.start()

    def _stop_analysis(self):
        self._analysis_cancel.set()
        self.analyze_btn.config(state=tk.NORMAL)
        self.analyze_stop_btn.config(state=tk.DISABLED)
        self.analysis_text.insert(tk.END, "\n── 분석이 중지되었습니다 ──\n")
