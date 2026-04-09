"""Strategy v7 — RSI mean reversion with trend filter + asymmetric R:R.

Optimized on Jan 2024 - Jun 2025 with maker fees included:
  RSI(14), entry<45, exit>80, SL 0.8%, TP 6%, trend filter (close>EMA50)
  → PF 2.22, +14% return, 1.6% max DD, 95 trades
"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import pandas as pd

from config.settings import TradingConfig

log = logging.getLogger("bot.strategy")


class Regime(Enum):
    TRENDING_UP = "trending_up"
    TRENDING_DOWN = "trending_down"
    RANGING = "ranging"
    TRANSITIONAL = "transitional"


@dataclass
class Signal:
    action: str
    regime: Regime
    reason: str
    stop_loss: float
    take_profit: float
    confidence: float


def generate_signal(df: pd.DataFrame, cfg: TradingConfig) -> Optional[Signal]:
    if len(df) < 3:
        return None

    row = df.iloc[-1]
    prev = df.iloc[-2]

    rsi = row.get("rsi", 50)
    rsi_prev = prev.get("rsi", 50)
    close = row["close"]
    ema_trend = row.get("ema_trend", 0)

    if pd.isna(rsi) or pd.isna(rsi_prev):
        return None

    # Trend filter: only trade in uptrend (close > EMA50)
    if cfg.trend_filter and not pd.isna(ema_trend) and close < ema_trend:
        return None

    # Entry: RSI was below threshold, now recovering + green candle
    if rsi_prev >= cfg.rsi_entry:
        return None
    if rsi <= rsi_prev:
        return None
    if close <= row["open"]:
        return None

    sl = close * (1 - cfg.sl_pct)
    tp = close * (1 + cfg.tp_pct)
    regime = Regime.TRENDING_UP

    reason = f"RSI({cfg.rsi_length}) {rsi_prev:.0f}->{rsi:.0f} uptrend"
    log.info("SIGNAL: %s | price=%.2f SL=%.2f(%.1f%%) TP=%.2f(%.1f%%)",
             reason, close, sl, cfg.sl_pct * 100, tp, cfg.tp_pct * 100)

    return Signal("buy", regime, reason, sl, tp, 0.65)


def check_exit(row: pd.Series, entry_price: float, stop_loss: float,
               take_profit: float, hold_candles: int, regime: Regime) -> Optional[str]:
    close = row["close"]
    low = row["low"]
    high = row["high"]
    rsi = row.get("rsi", 50)

    if low <= stop_loss:
        return "stop_loss"
    if high >= take_profit:
        return "take_profit"
    if hold_candles >= 72:  # 3 days max hold
        return "time_stop"

    # RSI overbought exit (only if in profit > 0.5%)
    if not pd.isna(rsi) and rsi > 80:
        pnl_pct = (close - entry_price) / entry_price
        if pnl_pct > 0.005:
            return f"rsi_exit ({rsi:.0f})"

    return None
