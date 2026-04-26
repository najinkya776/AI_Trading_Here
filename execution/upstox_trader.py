"""
Phase 2: Upstox Live Order Execution.
Activated only when LIVE_TRADING=true in .env.

This module mirrors paper_trader.py's interface so the webhook server
needs zero code changes to switch from paper to live.
"""
import logging
from datetime import datetime
from typing import Optional

from config.settings import settings, IST

log = logging.getLogger(__name__)

# Upstox lot sizes for index F&O (update when SEBI changes them)
LOT_SIZES = {
    "NIFTY":     50,
    "BANKNIFTY": 15,
    "FINNIFTY":  40,
}


class UpstoxTrader:
    """Live order execution via Upstox API v2."""

    def __init__(self):
        if not settings.live_trading:
            raise RuntimeError("LIVE_TRADING is false. Use PaperTrader instead.")
        if not settings.upstox_access_token:
            raise RuntimeError("UPSTOX_ACCESS_TOKEN not set in .env")

        import upstox_client
        configuration = upstox_client.Configuration()
        configuration.access_token = settings.upstox_access_token
        api_client = upstox_client.ApiClient(configuration)
        self._order_api    = upstox_client.OrderApi(api_client)
        self._portfolio_api = upstox_client.PortfolioApi(api_client)
        log.info("UpstoxTrader initialized — LIVE MODE")

    def place_order(self, symbol: str, action: str, price: float,
                    sl: float, target: float, qty: int, reason: str = "") -> dict:
        """
        Place a live market order on Upstox.
        Qty here is in units — will be rounded to nearest lot size.
        """
        import upstox_client

        lot_size = LOT_SIZES.get(symbol.upper(), 1)
        lots = max(1, round(qty / lot_size))
        actual_qty = lots * lot_size

        transaction = (
            upstox_client.TransactionType.BUY
            if action == "BUY"
            else upstox_client.TransactionType.SELL
        )

        order_request = upstox_client.PlaceOrderRequest(
            quantity=actual_qty,
            product=upstox_client.ProductType.INTRADAY,
            validity=upstox_client.ValidityType.DAY,
            price=0,                                     # 0 = market order
            tag="ai_bot",
            instrument_token=self._get_instrument_token(symbol),
            order_type=upstox_client.OrderType.MARKET,
            transaction_type=transaction,
            disclosed_quantity=0,
            trigger_price=0,
            is_amo=False,
        )

        log.info(f"Placing LIVE order | {action} {symbol} | qty={actual_qty} | reason={reason}")

        try:
            resp = self._order_api.place_order(order_request, api_version="2.0")
            order_id = resp.data.order_id
            log.info(f"Order placed | order_id={order_id}")

            # Place SL order
            self._place_sl_order(symbol, action, sl, actual_qty)

            return {"order_id": order_id, "qty": actual_qty, "action": action, "symbol": symbol}

        except Exception as e:
            log.error(f"Order placement failed: {e}")
            raise

    def _place_sl_order(self, symbol: str, action: str, sl_price: float, qty: int):
        """Place a Stop-Loss order immediately after entry."""
        import upstox_client

        sl_transaction = (
            upstox_client.TransactionType.SELL
            if action == "BUY"
            else upstox_client.TransactionType.BUY
        )

        sl_order = upstox_client.PlaceOrderRequest(
            quantity=qty,
            product=upstox_client.ProductType.INTRADAY,
            validity=upstox_client.ValidityType.DAY,
            price=round(sl_price * 0.99, 2),   # limit slightly away from SL
            tag="ai_bot_sl",
            instrument_token=self._get_instrument_token(symbol),
            order_type=upstox_client.OrderType.SL,
            transaction_type=sl_transaction,
            disclosed_quantity=0,
            trigger_price=sl_price,
            is_amo=False,
        )
        resp = self._order_api.place_order(sl_order, api_version="2.0")
        log.info(f"SL order placed | sl={sl_price} | order_id={resp.data.order_id}")

    def square_off_all(self):
        """Emergency square-off — close all open intraday positions."""
        log.warning("SQUARE OFF ALL triggered")
        try:
            resp = self._portfolio_api.get_short_term_positions(api_version="2.0")
            for pos in resp.data:
                if pos.quantity != 0 and pos.product == "D":  # intraday
                    self.place_order(
                        symbol=pos.trading_symbol,
                        action="SELL" if pos.quantity > 0 else "BUY",
                        price=0,
                        sl=0, target=0,
                        qty=abs(pos.quantity),
                        reason="EMERGENCY_SQUARE_OFF",
                    )
        except Exception as e:
            log.error(f"Square-off failed: {e}")

    @staticmethod
    def _get_instrument_token(symbol: str) -> str:
        """Map index name to Upstox instrument token. Update for specific option contracts."""
        tokens = {
            "NIFTY":     "NSE_INDEX|Nifty 50",
            "BANKNIFTY": "NSE_INDEX|Nifty Bank",
            "FINNIFTY":  "NSE_INDEX|Nifty Fin Service",
        }
        token = tokens.get(symbol.upper())
        if not token:
            raise ValueError(f"Unknown symbol for live trading: {symbol}")
        return token
