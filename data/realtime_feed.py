"""
Upstox WebSocket Real-time Feed (Phase 2).
Streams live OHLCV ticks and routes them to the paper/live trader for SL/target checks.

Run standalone: python -m data.realtime_feed
"""
import asyncio
import logging
import ssl
import json
import websockets
from datetime import datetime

from config.settings import settings, IST, INSTRUMENTS

log = logging.getLogger(__name__)

UPSTOX_WS_URL = "wss://api.upstox.com/v2/feed/market-data-feed"

_latest_prices: dict[str, float] = {}


def get_latest_price(symbol: str) -> float | None:
    return _latest_prices.get(symbol.upper())


async def start_feed(symbols: list[str] = None):
    """
    Open Upstox WebSocket and stream live prices.
    Automatically checks SL/target on each tick via PaperTrader.
    """
    if not settings.upstox_access_token:
        log.warning("No Upstox access token — real-time feed disabled")
        return

    if symbols is None:
        symbols = list(INSTRUMENTS.keys())

    instrument_keys = [INSTRUMENTS[s] for s in symbols if s in INSTRUMENTS]

    headers = {"Authorization": f"Bearer {settings.upstox_access_token}"}

    log.info(f"Connecting to Upstox WebSocket | instruments={instrument_keys}")

    ssl_ctx = ssl.create_default_context()

    async with websockets.connect(UPSTOX_WS_URL, additional_headers=headers, ssl=ssl_ctx) as ws:
        # Subscribe to instruments
        subscribe_msg = {
            "guid": "ai_bot_feed",
            "method": "sub",
            "data": {
                "mode": "ltpc",
                "instrumentKeys": instrument_keys,
            },
        }
        await ws.send(json.dumps(subscribe_msg))
        log.info("Subscribed to live feed")

        from execution.paper_trader import PaperTrader
        trader = PaperTrader()

        async for raw in ws:
            try:
                data = json.loads(raw)
                _process_tick(data, trader)
            except Exception as e:
                log.debug(f"Tick processing error: {e}")


def _process_tick(data: dict, trader):
    feeds = data.get("feeds", {})
    prices = {}

    for key, feed_data in feeds.items():
        ltpc = feed_data.get("ltpc", {})
        ltp = ltpc.get("ltp")
        if ltp:
            # Map instrument key back to symbol name
            for symbol, instr_key in INSTRUMENTS.items():
                if instr_key == key:
                    _latest_prices[symbol] = float(ltp)
                    prices[symbol] = float(ltp)
                    break

    if prices:
        trader.update_prices(prices)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(start_feed())
