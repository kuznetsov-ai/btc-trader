import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from typing import Optional
from config.settings import RiskConfig

log = logging.getLogger("bot.risk")


@dataclass
class Position:
    entry_price: float
    amount: float  # in BTC
    stop_loss: float
    take_profit: float
    regime: str
    entry_time: datetime
    hold_candles: int = 0


@dataclass
class DailyStats:
    date: str = ""
    trades: int = 0
    wins: int = 0
    losses: int = 0
    pnl: float = 0.0
    consecutive_losses: int = 0
    cooldown_until_candle: int = 0


class RiskManager:
    def __init__(self, cfg: RiskConfig):
        self.cfg = cfg
        self.position: Optional[Position] = None
        self.daily = DailyStats()
        self.total_trades = 0
        self.total_wins = 0
        self.total_pnl = 0.0
        self.max_drawdown = 0.0
        self.peak_balance = 0.0
        self.candle_count = 0
        self._initial_balance = 0.0

    def set_initial_balance(self, balance: float):
        self._initial_balance = balance
        self.peak_balance = balance

    def _reset_daily(self, current_date: Optional[str] = None):
        today = current_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self.daily.date != today:
            if self.daily.date:
                log.info("Daily summary [%s]: trades=%d wins=%d losses=%d PnL=%.2f%%",
                         self.daily.date, self.daily.trades, self.daily.wins,
                         self.daily.losses, self.daily.pnl * 100)
            self.daily = DailyStats(date=today)

    def can_trade(self, current_date: Optional[str] = None) -> tuple[bool, str]:
        self._reset_daily(current_date)

        if self.position is not None:
            return False, "position already open"

        if self.daily.pnl <= -self.cfg.max_daily_loss:
            return False, f"daily loss limit hit ({self.daily.pnl*100:.2f}%)"

        if self.daily.consecutive_losses >= self.cfg.max_consecutive_losses:
            if self.candle_count < self.daily.cooldown_until_candle:
                remaining = self.daily.cooldown_until_candle - self.candle_count
                return False, f"cooldown ({remaining} candles left)"
            self.daily.consecutive_losses = 0

        return True, "ok"

    def calc_position_size(self, balance: float, entry_price: float,
                           stop_loss: float, confidence: float) -> float:
        risk_pct = self.cfg.risk_per_trade * min(confidence + 0.3, 1.0)
        risk_amount = balance * risk_pct
        price_risk = abs(entry_price - stop_loss)

        if price_risk <= 0:
            return 0.0

        size_btc = risk_amount / price_risk
        max_size = (balance * self.cfg.max_position_pct) / entry_price
        size_btc = min(size_btc, max_size)

        return round(size_btc, 6)

    def open_position(self, entry_price: float, amount: float,
                      stop_loss: float, take_profit: float, regime: str):
        self.position = Position(
            entry_price=entry_price,
            amount=amount,
            stop_loss=stop_loss,
            take_profit=take_profit,
            regime=regime,
            entry_time=datetime.now(timezone.utc),
        )
        log.info("OPEN  price=%.2f  amount=%.6f  SL=%.2f  TP=%.2f  regime=%s",
                 entry_price, amount, stop_loss, take_profit, regime)

    def close_position(self, exit_price: float, balance: float) -> float:
        if self.position is None:
            return 0.0

        pnl_pct = (exit_price - self.position.entry_price) / self.position.entry_price
        pnl_abs = pnl_pct * self.position.amount * self.position.entry_price

        self.daily.trades += 1
        self.total_trades += 1

        if pnl_pct > 0:
            self.daily.wins += 1
            self.total_wins += 1
            self.daily.consecutive_losses = 0
        else:
            self.daily.losses += 1
            self.daily.consecutive_losses += 1
            if self.daily.consecutive_losses >= self.cfg.max_consecutive_losses:
                self.daily.cooldown_until_candle = self.candle_count + self.cfg.cooldown_candles
                log.warning("Cooldown activated: %d consecutive losses", self.daily.consecutive_losses)

        self.daily.pnl += pnl_pct
        self.total_pnl += pnl_abs

        new_balance = balance + pnl_abs
        self.peak_balance = max(self.peak_balance, new_balance)
        dd = (self.peak_balance - new_balance) / self.peak_balance if self.peak_balance > 0 else 0
        self.max_drawdown = max(self.max_drawdown, dd)

        log.info("CLOSE price=%.2f  PnL=%.2f%% ($%.2f)  held=%d candles  daily_pnl=%.2f%%",
                 exit_price, pnl_pct * 100, pnl_abs,
                 self.position.hold_candles, self.daily.pnl * 100)

        self.position = None
        return pnl_abs

    def tick_candle(self):
        self.candle_count += 1
        if self.position:
            self.position.hold_candles += 1

    @property
    def win_rate(self) -> float:
        return self.total_wins / self.total_trades if self.total_trades > 0 else 0.0

    def summary(self) -> dict:
        return {
            "total_trades": self.total_trades,
            "wins": self.total_wins,
            "losses": self.total_trades - self.total_wins,
            "win_rate": f"{self.win_rate*100:.1f}%",
            "total_pnl": f"${self.total_pnl:.2f}",
            "max_drawdown": f"{self.max_drawdown*100:.2f}%",
        }
