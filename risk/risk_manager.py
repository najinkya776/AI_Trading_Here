"""
Risk Manager — answers two questions before every trade:
  1. How many lots/shares can we buy? (position sizing)
  2. Are we allowed to trade at all? (daily loss limit, max positions)
"""
import logging
from datetime import datetime
from sqlalchemy import text

from config.settings import settings, IST

log = logging.getLogger(__name__)


class RiskManager:
    def __init__(self):
        # Lazy import to avoid circular dependency at module load
        from execution.paper_trader import engine
        self._engine = engine

    def get_position_size(self, entry_price: float, sl_price: float) -> int:
        """
        Risk-based position sizing.
        Risk per trade = (capital * risk_pct) / (entry - SL)
        Returns number of units (lots/shares). 0 = do not trade.
        """
        if not self._can_trade():
            return 0

        capital = self._get_capital()
        risk_amount = capital * (settings.risk_per_trade_pct / 100)
        risk_per_unit = abs(entry_price - sl_price)

        if risk_per_unit <= 0:
            log.warning("SL equals entry price — cannot size position")
            return 0

        qty = int(risk_amount / risk_per_unit)
        log.info(f"Position size | capital={capital:.0f} | risk={risk_amount:.0f} | "
                 f"risk/unit={risk_per_unit:.2f} | qty={qty}")
        return max(qty, 0)

    def _can_trade(self) -> bool:
        """False if daily loss limit breached or max positions open."""
        # Check daily loss limit
        daily_pnl = self._get_daily_pnl()
        capital = self._get_capital()
        loss_limit = capital * (settings.daily_loss_limit_pct / 100)

        if daily_pnl < -loss_limit:
            log.warning(f"Daily loss limit hit | pnl={daily_pnl:.0f} | limit={-loss_limit:.0f}")
            return False

        # Check open position count
        open_count = self._get_open_positions_count()
        if open_count >= settings.max_positions:
            log.info(f"Max positions reached | open={open_count}")
            return False

        # Time filter: avoid first 15 min and last 15 min
        now = datetime.now(IST)
        hour, minute = now.hour, now.minute
        total_min = hour * 60 + minute
        market_open  = 9 * 60 + 15    # 9:15
        avoid_before = market_open + 15  # 9:30
        avoid_after  = 15 * 60 + 0    # 15:00

        if total_min < avoid_before:
            log.info("Skipping — within first 15 min opening chaos")
            return False
        if total_min >= avoid_after:
            log.info("Skipping — within last 20 min, approaching square-off")
            return False

        return True

    def _get_daily_pnl(self) -> float:
        today = datetime.now(IST).date().isoformat()
        with self._engine.connect() as conn:
            row = conn.execute(
                text("SELECT COALESCE(SUM(pnl),0) FROM trades WHERE status='CLOSED' AND exit_time LIKE :today"),
                {"today": f"{today}%"},
            ).fetchone()
        return float(row[0]) if row else 0.0

    def _get_capital(self) -> float:
        with self._engine.connect() as conn:
            row = conn.execute(
                text("SELECT value FROM portfolio WHERE key='capital'")
            ).fetchone()
        return float(row[0]) if row else settings.paper_capital

    def _get_open_positions_count(self) -> int:
        with self._engine.connect() as conn:
            row = conn.execute(
                text("SELECT COUNT(*) FROM trades WHERE status='OPEN'")
            ).fetchone()
        return int(row[0]) if row else 0
