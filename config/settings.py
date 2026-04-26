"""Central configuration — reads from .env file."""
from pydantic_settings import BaseSettings
from pydantic import Field
import pytz

IST = pytz.timezone("Asia/Kolkata")

MARKET_OPEN = "09:15"
MARKET_CLOSE = "15:20"    # auto square-off 10 min before actual close
AVOID_FIRST_MINUTES = 15  # skip 9:15–9:30 volatility window
AVOID_LAST_MINUTES = 15   # skip 3:00–3:15 expiry chaos

# Instruments we trade (Upstox instrument keys)
INSTRUMENTS = {
    "NIFTY":     "NSE_INDEX|Nifty 50",
    "BANKNIFTY": "NSE_INDEX|Nifty Bank",
    "FINNIFTY":  "NSE_INDEX|Nifty Fin Service",
}

# Timeframes for signals (minutes)
PRIMARY_TF = 5    # main signal timeframe
CONFIRM_TF = 15   # confirmation timeframe


class Settings(BaseSettings):
    # Upstox
    upstox_api_key: str = Field(default="", env="UPSTOX_API_KEY")
    upstox_api_secret: str = Field(default="", env="UPSTOX_API_SECRET")
    upstox_redirect_uri: str = Field(default="http://localhost:8000/upstox/callback", env="UPSTOX_REDIRECT_URI")
    upstox_access_token: str = Field(default="", env="UPSTOX_ACCESS_TOKEN")

    # Telegram
    telegram_bot_token: str = Field(default="", env="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str = Field(default="", env="TELEGRAM_CHAT_ID")

    # Webhook
    webhook_secret: str = Field(default="", env="WEBHOOK_SECRET")

    # Trading mode
    live_trading: bool = Field(default=False, env="LIVE_TRADING")
    paper_capital: float = Field(default=1_000_000, env="PAPER_CAPITAL")

    # Risk parameters
    max_positions: int = Field(default=2, env="MAX_POSITIONS")
    risk_per_trade_pct: float = Field(default=0.5, env="RISK_PER_TRADE_PCT")
    daily_loss_limit_pct: float = Field(default=2.0, env="DAILY_LOSS_LIMIT_PCT")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
