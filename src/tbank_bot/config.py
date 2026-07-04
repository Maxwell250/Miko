from __future__ import annotations

from enum import Enum
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class TradingMode(str, Enum):
    PAPER = "paper"
    LIVE = "live"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    tbank_token: str = Field(default="", alias="TBANK_TOKEN")
    tbank_account_id: str = Field(default="", alias="TBANK_ACCOUNT_ID")
    tbank_sandbox: bool = Field(default=True, alias="TBANK_SANDBOX")
    tbank_app_name: str = Field(default="tbank-futures-bot", alias="TBANK_APP_NAME")
    tbank_futures_tickers: str = Field(default="", alias="TBANK_FUTURES_TICKERS")

    trading_mode: TradingMode = Field(default=TradingMode.PAPER, alias="TRADING_MODE")

    max_risk_per_trade_pct: float = Field(default=1.0, alias="MAX_RISK_PER_TRADE_PCT")
    max_daily_loss_pct: float = Field(default=3.0, alias="MAX_DAILY_LOSS_PCT")
    max_open_positions: int = Field(default=2, alias="MAX_OPEN_POSITIONS")
    min_signal_confidence: float = Field(default=65.0, alias="MIN_SIGNAL_CONFIDENCE")
    default_stop_atr_mult: float = Field(default=1.5, alias="DEFAULT_STOP_ATR_MULT")
    default_tp_atr_mult: float = Field(default=3.0, alias="DEFAULT_TP_ATR_MULT")

    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str = Field(default="", alias="TELEGRAM_CHAT_ID")
    telegram_admin_ids: str = Field(default="", alias="TELEGRAM_ADMIN_IDS")
    telegram_allowed_usernames: str = Field(default="", alias="TELEGRAM_ALLOWED_USERNAMES")

    loop_interval_sec: int = Field(default=300, alias="LOOP_INTERVAL_SEC")

    @property
    def admin_id_list(self) -> List[str]:
        if not self.telegram_admin_ids.strip():
            return []
        return [x.strip() for x in self.telegram_admin_ids.split(",") if x.strip()]

    @property
    def allowed_username_list(self) -> List[str]:
        if not self.telegram_allowed_usernames.strip():
            return []
        return [
            x.strip().lstrip("@").lower()
            for x in self.telegram_allowed_usernames.split(",")
            if x.strip()
        ]

    @property
    def futures_ticker_list(self) -> List[str]:
        if not self.tbank_futures_tickers.strip():
            return []
        return [t.strip().upper() for t in self.tbank_futures_tickers.split(",") if t.strip()]

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token)

    def has_notification_target(self, owner_chat_id: str | None = None) -> bool:
        return bool(
            self.telegram_chat_id
            or self.admin_id_list
            or owner_chat_id
        )


def get_settings() -> Settings:
    return Settings()
