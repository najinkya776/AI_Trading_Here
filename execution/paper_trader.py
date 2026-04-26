"""
Paper Trade Executor — simulates trades with virtual money (no real orders).
Tracks portfolio, open positions, PnL, and writes a trade log to SQLite.
"""
import uuid
import logging
from datetime import datetime
from dataclasses import dataclass, field, asdict
from typing import Optional

from sqlalchemy import create_engine, text
from config.settings import settings, IST

log = logging.getLogger(__name__)

DB_PATH = "logs/paper_trades.db"
engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id          TEXT PRIMARY KEY,
    symbol      TEXT,
    action      TEXT,
    entry_price REAL,
    sl          REAL,
    target      REAL,
    qty         INTEGER,
    entry_time  TEXT,
    exit_price  REAL,
    exit_time   TEXT,
    pnl         REAL,
    status      TEXT,
    reason      TEXT
);
CREATE TABLE IF NOT EXISTS portfolio (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

with engine.connect() as conn:
    for stmt in _SCHEMA.strip().split(";"):
        if stmt.strip():
            conn.execute(text(stmt))
    conn.commit()


@dataclass
class Position:
    id: str
    symbol: str
    action: str          # BUY or SELL
    entry_price: float
    sl: float
    target: float
    qty: int
    entry_time: str
    exit_price: float = 0.0
    exit_time: str = ""
    pnl: float = 0.0
    status: str = "OPEN"  # OPEN | CLOSED
    reason: str = ""


class PaperTrader:
    def __init__(self):
        self.capital = self._load_capital()
        self.open_positions: list[Position] = self._load_open_positions()

    # ── Public API ──────────────────────────────────────────────────────────

    def place_order(self, symbol: str, action: str, price: float,
                    sl: float, target: float, qty: int, reason: str = "") -> dict:
        if action == "EXIT":
            return self._exit_position(symbol, price)

        if len(self.open_positions) >= settings.max_positions:
            return {"error": f"Max positions ({settings.max_positions}) reached"}

        pos = Position(
            id=str(uuid.uuid4())[:8],
            symbol=symbol,
            action=action,
            entry_price=price,
            sl=sl,
            target=target,
            qty=qty,
            entry_time=datetime.now(IST).isoformat(),
            reason=reason,
        )
        self.open_positions.append(pos)
        self._save_position(pos)
        self._notify(f"ENTRY {action} {symbol} @ {price} | SL={sl} | TGT={target} | Qty={qty}")
        log.info(f"Paper ENTRY | {pos.id} | {action} {symbol} @ {price}")
        return asdict(pos)

    def update_prices(self, prices: dict[str, float]):
        """Call this on every price tick to check SL/target hits."""
        for pos in list(self.open_positions):
            ltp = prices.get(pos.symbol)
            if ltp is None:
                continue
            hit = self._check_sl_target(pos, ltp)
            if hit:
                self._close_position(pos, ltp, hit)

    def get_portfolio_summary(self) -> dict:
        daily_pnl = sum(p.pnl for p in self._load_all_positions() if self._is_today(p.exit_time))
        open_pnl = sum(self._unrealized_pnl(p) for p in self.open_positions)
        return {
            "capital": self.capital,
            "open_positions": len(self.open_positions),
            "daily_realized_pnl": round(daily_pnl, 2),
            "daily_open_pnl": round(open_pnl, 2),
            "total_daily_pnl": round(daily_pnl + open_pnl, 2),
        }

    # ── Internal ────────────────────────────────────────────────────────────

    def _check_sl_target(self, pos: Position, ltp: float) -> Optional[str]:
        if pos.action == "BUY":
            if ltp <= pos.sl:
                return "SL_HIT"
            if ltp >= pos.target:
                return "TARGET_HIT"
        else:  # SELL
            if ltp >= pos.sl:
                return "SL_HIT"
            if ltp <= pos.target:
                return "TARGET_HIT"
        return None

    def _close_position(self, pos: Position, exit_price: float, reason: str):
        multiplier = 1 if pos.action == "BUY" else -1
        pos.pnl = round(multiplier * (exit_price - pos.entry_price) * pos.qty, 2)
        pos.exit_price = exit_price
        pos.exit_time = datetime.now(IST).isoformat()
        pos.status = "CLOSED"
        self.capital += pos.pnl
        self.open_positions = [p for p in self.open_positions if p.id != pos.id]
        self._update_position_db(pos)
        self._save_capital(self.capital)
        self._notify(f"EXIT {pos.symbol} | {reason} | PnL={pos.pnl}")
        log.info(f"Paper EXIT | {pos.id} | {reason} | PnL={pos.pnl}")

    def _exit_position(self, symbol: str, price: float) -> dict:
        matches = [p for p in self.open_positions if p.symbol == symbol]
        if not matches:
            return {"error": f"No open position for {symbol}"}
        pos = matches[0]
        self._close_position(pos, price, "MANUAL_EXIT")
        return {"status": "closed", "pnl": pos.pnl}

    def _unrealized_pnl(self, pos: Position) -> float:
        return 0.0  # updated when price ticks arrive

    def _save_position(self, pos: Position):
        with engine.connect() as conn:
            conn.execute(text(
                "INSERT INTO trades VALUES (:id,:symbol,:action,:entry_price,:sl,:target,:qty,"
                ":entry_time,:exit_price,:exit_time,:pnl,:status,:reason)"
            ), asdict(pos))
            conn.commit()

    def _update_position_db(self, pos: Position):
        with engine.connect() as conn:
            conn.execute(text(
                "UPDATE trades SET exit_price=:exit_price, exit_time=:exit_time, "
                "pnl=:pnl, status=:status WHERE id=:id"
            ), {"exit_price": pos.exit_price, "exit_time": pos.exit_time,
                "pnl": pos.pnl, "status": pos.status, "id": pos.id})
            conn.commit()

    def _load_open_positions(self) -> list[Position]:
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT * FROM trades WHERE status='OPEN'")).fetchall()
        return [Position(*r) for r in rows]

    def _load_all_positions(self) -> list[Position]:
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT * FROM trades")).fetchall()
        return [Position(*r) for r in rows]

    def _load_capital(self) -> float:
        with engine.connect() as conn:
            row = conn.execute(text("SELECT value FROM portfolio WHERE key='capital'")).fetchone()
        return float(row[0]) if row else settings.paper_capital

    def _save_capital(self, capital: float):
        with engine.connect() as conn:
            conn.execute(text(
                "INSERT OR REPLACE INTO portfolio(key,value) VALUES('capital',:v)"
            ), {"v": str(capital)})
            conn.commit()

    @staticmethod
    def _is_today(ts: str) -> bool:
        if not ts:
            return False
        try:
            return datetime.fromisoformat(ts).date() == datetime.now(IST).date()
        except Exception:
            return False

    @staticmethod
    def _notify(msg: str):
        try:
            from notifications.telegram_bot import send_message
            import asyncio
            asyncio.run(send_message(msg))
        except Exception:
            pass  # notifications are best-effort
