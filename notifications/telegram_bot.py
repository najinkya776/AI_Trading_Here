"""
Telegram notification module.
Sends trade alerts, daily PnL summary, and risk warnings to your Telegram.

Setup:
  1. Create a bot via @BotFather → get token
  2. Message the bot → get your chat ID via @userinfobot
  3. Add both to .env
"""
import logging
from telegram import Bot
from telegram.error import TelegramError

from config.settings import settings

log = logging.getLogger(__name__)
_bot: Bot | None = None


def _get_bot() -> Bot | None:
    global _bot
    if not settings.telegram_bot_token:
        return None
    if _bot is None:
        _bot = Bot(token=settings.telegram_bot_token)
    return _bot


async def send_message(text: str):
    """Send a message to your Telegram chat."""
    bot = _get_bot()
    if not bot or not settings.telegram_chat_id:
        log.debug("Telegram not configured — skipping notification")
        return
    try:
        await bot.send_message(
            chat_id=settings.telegram_chat_id,
            text=f"*AI Trading Bot*\n{text}",
            parse_mode="Markdown",
        )
    except TelegramError as e:
        log.error(f"Telegram send failed: {e}")


async def send_daily_summary():
    """Call this at end of day to send the full PnL summary."""
    from execution.paper_trader import PaperTrader
    trader = PaperTrader()
    summary = trader.get_portfolio_summary()

    pnl = summary["total_daily_pnl"]
    emoji = "+" if pnl >= 0 else "-"
    mode = "PAPER" if not settings.live_trading else "LIVE"

    msg = (
        f"*Daily Summary [{mode}]*\n"
        f"PnL: `{emoji}₹{abs(pnl):,.0f}`\n"
        f"Open positions: `{summary['open_positions']}`\n"
        f"Capital: `₹{summary['capital']:,.0f}`"
    )
    await send_message(msg)


async def send_trade_alert(action: str, symbol: str, price: float,
                           sl: float, target: float, qty: int, reason: str = ""):
    direction = "BUY" if action == "BUY" else "SELL"
    msg = (
        f"*{direction} Signal*\n"
        f"Symbol: `{symbol}`\n"
        f"Entry: `₹{price:,.2f}`\n"
        f"SL: `₹{sl:,.2f}`\n"
        f"Target: `₹{target:,.2f}`\n"
        f"Qty: `{qty}`\n"
        f"Reason: _{reason}_"
    )
    await send_message(msg)
