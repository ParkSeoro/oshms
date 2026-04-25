"""설정 관리 모듈."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


@dataclass
class Settings:
    """애플리케이션 전체 설정."""

    # API 인증
    app_key: str = ""
    app_secret: str = ""
    account_no: str = ""
    is_mock: bool = True

    # 매매 설정
    # v4.7: initial_capital(100,000) × max_hold_count(2) 완전 활용 기준
    # max_buy_amount = initial_capital / max_hold_count 로 수학적 일관성 유지
    max_buy_amount: int = 50_000   # v4.7: 500,000→50,000 (자본 100,000의 50% = 2종목 집중)
    max_hold_count: int = 2        # v4.3: 5→2 (소액은 집중 투자가 유리)
    stop_loss_pct: float = -4.0    # v4.7: -2.5→-4.0 (하드 손절은 안전망, 논지매도가 주력)
    take_profit_pct: float = 1.5   # v4.3: 3.0→1.5 (작은 수익 자주 확정)
    initial_capital: int = 100_000  # 시작 자본금 (원)

    # 매매 시간
    trading_start_time: str = "09:05"
    trading_end_time: str = "15:10"

    # ── 계좌 보호 파라미터 (v4.8) ──────────────────────────────────────────────
    # 일일 손실 한도: 이 비율 초과 손실 시 방어 모드 전환 (% 단위, 음수)
    daily_loss_limit: float = -3.0
    # 연속 손실 횟수 임계: 이 횟수 연속 손실 시 자동 매매 일시 정지
    consecutive_loss_limit: int = 3
    # 방어 모드에서 복구 기준: 손실의 이 비율 회복 시 정상 모드 복귀 (0~1)
    recovery_threshold: float = 0.5

    # ── 진입 필터 파라미터 (v4.8) ──────────────────────────────────────────────
    # 체결강도 최소 기준: 매수호가/매도호가 비율 × 100 (기본 120 = 매수 우위)
    min_contract_strength: float = 120.0
    # VI 발동 후 진입 금지 시간 (분): 급격한 등락 직후 진입 방지
    vi_guard_minutes: int = 5
    # 당일 급등 추격 금지: 당일 변동률 이 값 초과 종목은 진입 안 함 (%)
    max_chase_rate: float = 5.0
    # 오후장 강제 청산 기준 시각 (HH:MM): 이 시간 이후 손실/보합 포지션 정리
    afternoon_force_sell_time: str = "14:00"
    # 오후장 강제 청산 최소 손실 기준 (%): 이 값 이상 손실이면 14시 이후 강제 청산
    afternoon_force_sell_pct: float = -0.5

    # 로그
    log_level: str = "INFO"

    # AI 분석
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    # API URL
    base_url: str = field(init=False)

    MOCK_URL: str = "https://openapivts.koreainvestment.com:29443"
    REAL_URL: str = "https://openapi.koreainvestment.com:9443"

    def __post_init__(self):
        self.base_url = self.MOCK_URL if self.is_mock else self.REAL_URL

    @classmethod
    def from_env(cls, env_path: str | None = None) -> "Settings":
        """환경변수에서 설정을 로드한다."""
        if env_path:
            load_dotenv(env_path, override=True)
        else:
            load_dotenv(override=True)

        return cls(
            app_key=os.getenv("KIS_APP_KEY", ""),
            app_secret=os.getenv("KIS_APP_SECRET", ""),
            account_no=os.getenv("KIS_ACCOUNT_NO", ""),
            is_mock=os.getenv("KIS_MOCK", "true").lower() == "true",
            max_buy_amount=int(os.getenv("MAX_BUY_AMOUNT", "50000")),
            max_hold_count=int(os.getenv("MAX_HOLD_COUNT", "2")),
            stop_loss_pct=float(os.getenv("STOP_LOSS_PCT", "-4.0")),
            take_profit_pct=float(os.getenv("TAKE_PROFIT_PCT", "1.5")),
            initial_capital=int(os.getenv("INITIAL_CAPITAL", "100000")),
            trading_start_time=os.getenv("TRADING_START_TIME", "09:05"),
            trading_end_time=os.getenv("TRADING_END_TIME", "15:10"),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            openai_api_key=os.getenv("OPENAI_API_KEY", ""),
            openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            # v4.8 계좌 보호
            daily_loss_limit=float(os.getenv("DAILY_LOSS_LIMIT", "-3.0")),
            consecutive_loss_limit=int(os.getenv("CONSECUTIVE_LOSS_LIMIT", "3")),
            recovery_threshold=float(os.getenv("RECOVERY_THRESHOLD", "0.5")),
            # v4.8 진입 필터
            min_contract_strength=float(os.getenv("MIN_CONTRACT_STRENGTH", "120.0")),
            vi_guard_minutes=int(os.getenv("VI_GUARD_MINUTES", "5")),
            max_chase_rate=float(os.getenv("MAX_CHASE_RATE", "5.0")),
            afternoon_force_sell_time=os.getenv("AFTERNOON_FORCE_SELL_TIME", "14:00"),
            afternoon_force_sell_pct=float(os.getenv("AFTERNOON_FORCE_SELL_PCT", "-0.5")),
        )

    def reload_from_env(self, env_path: str | None = None) -> None:
        """v4.7: 현재 객체를 제자리에서 갱신한다 (hot-reload).

        기존 ``from_env()``는 새 ``Settings`` 객체를 만들어 반환하지만,
        실행 중인 ``AutoTrader``, ``OrderManager``, ``KISApi`` 등이
        이미 기존 객체의 **참조**를 붙들고 있기 때문에 새 객체로 바꿔치기해도
        돌고 있는 매매 로직엔 전혀 반영되지 않는다.

        이 메서드는 자신(self)의 필드를 직접 갱신하므로,
        같은 객체를 참조하는 모든 컴포넌트가 다음 틱부터 새 값을 본다.
        """
        fresh = Settings.from_env(env_path)
        # base_url은 __post_init__에서 is_mock을 따라 결정되므로 같이 갱신
        for field_name in (
            "app_key", "app_secret", "account_no", "is_mock",
            "max_buy_amount", "max_hold_count",
            "stop_loss_pct", "take_profit_pct", "initial_capital",
            "trading_start_time", "trading_end_time", "log_level",
            "openai_api_key", "openai_model", "base_url",
            "daily_loss_limit", "consecutive_loss_limit", "recovery_threshold",
            "min_contract_strength", "vi_guard_minutes", "max_chase_rate",
            "afternoon_force_sell_time", "afternoon_force_sell_pct",
        ):
            setattr(self, field_name, getattr(fresh, field_name))

    @property
    def account_number(self) -> str:
        """계좌번호 앞 8자리."""
        return self.account_no.split("-")[0] if "-" in self.account_no else self.account_no[:8]

    @property
    def account_suffix(self) -> str:
        """계좌번호 뒤 2자리."""
        return self.account_no.split("-")[1] if "-" in self.account_no else self.account_no[8:]

    def validate(self) -> list[str]:
        """설정값 유효성 검사. 오류 메시지 목록을 반환한다."""
        errors = []
        if not self.app_key:
            errors.append("KIS_APP_KEY가 설정되지 않았습니다.")
        if not self.app_secret:
            errors.append("KIS_APP_SECRET이 설정되지 않았습니다.")
        if not self.account_no:
            errors.append("KIS_ACCOUNT_NO가 설정되지 않았습니다.")
        if self.max_buy_amount <= 0:
            errors.append("MAX_BUY_AMOUNT는 양수여야 합니다.")
        if self.stop_loss_pct >= 0:
            errors.append("STOP_LOSS_PCT는 음수여야 합니다.")
        if self.take_profit_pct <= 0:
            errors.append("TAKE_PROFIT_PCT는 양수여야 합니다.")
        # v4.7: 설정 일관성 검증 — .env 잘못 편집으로 인한 v4.7 설계 무력화 방지
        if self.max_buy_amount * self.max_hold_count > self.initial_capital:
            errors.append(
                f"자본 부족: MAX_BUY_AMOUNT({self.max_buy_amount:,}) × "
                f"MAX_HOLD_COUNT({self.max_hold_count}) = "
                f"{self.max_buy_amount * self.max_hold_count:,}원이 "
                f"INITIAL_CAPITAL({self.initial_capital:,}원)을 초과합니다."
            )
        if self.stop_loss_pct > -3.0:
            errors.append(
                f"STOP_LOSS_PCT={self.stop_loss_pct}는 v4.7 논지 기반 매도 설계를 "
                f"무력화합니다 (하드 손절이 논지 체크보다 먼저 발동). "
                f"-3.0 이하를 권장 (기본값 -4.0)."
            )
        return errors
