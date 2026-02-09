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
    max_buy_amount: int = 500_000
    max_hold_count: int = 5
    stop_loss_pct: float = -2.0
    take_profit_pct: float = 3.0

    # 매매 시간
    trading_start_time: str = "09:05"
    trading_end_time: str = "15:10"

    # 로그
    log_level: str = "INFO"

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
            load_dotenv(env_path)
        else:
            load_dotenv()

        return cls(
            app_key=os.getenv("KIS_APP_KEY", ""),
            app_secret=os.getenv("KIS_APP_SECRET", ""),
            account_no=os.getenv("KIS_ACCOUNT_NO", ""),
            is_mock=os.getenv("KIS_MOCK", "true").lower() == "true",
            max_buy_amount=int(os.getenv("MAX_BUY_AMOUNT", "500000")),
            max_hold_count=int(os.getenv("MAX_HOLD_COUNT", "5")),
            stop_loss_pct=float(os.getenv("STOP_LOSS_PCT", "-2.0")),
            take_profit_pct=float(os.getenv("TAKE_PROFIT_PCT", "3.0")),
            trading_start_time=os.getenv("TRADING_START_TIME", "09:05"),
            trading_end_time=os.getenv("TRADING_END_TIME", "15:10"),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
        )

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
        return errors
