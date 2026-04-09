#!/usr/bin/env python3
"""Parameter optimizer — tests multiple strategy configurations to find optimal settings."""

import asyncio
import sys
import itertools
from copy import deepcopy

import pandas as pd

from config.settings import Config
from src.indicators import compute_indicators
from src.logger_setup import setup_logging
from backtest import fetch_historical

import logging

# Suppress strategy logs during optimization
logging.getLogger("bot.strategy").setLevel(logging.WARNING)
logging.getLogger("bot.risk").setLevel(logging.WARNING)
log = logging.getLogger("bot.optimize")

INITIAL_BALANCE = 10_000.0


def fast_backtest(df_full: pd.DataFrame, cfg: Config,
                  rsi_entry: float, rsi_exit: float, sl_pct: float, tp_pct: float,
                  timeframe: str, rsi_period: int = 14) -> dict:
    """Fast backtest with parameterized RSI mean reversion."""
    from src.indicators import _ema, _rsi, _sma, _atr, _bbands

    d = df_full.copy()
    d["rsi"] = _rsi(d["close"], rsi_period)
    d["ema50"] = _ema(d["close"], 50)
    d["atr"] = _atr(d["high"], d["low"], d["close"], 14)
    bb_l, bb_m, bb_u = _bbands(d["close"], 20, 2.0)
    d["bb_lower"] = bb_l
    d["bb_mid"] = bb_m

    balance = INITIAL_BALANCE
    in_position = False
    entry_price = 0
    sl = 0
    tp = 0
    entry_idx = 0
    trades = 0
    wins = 0
    peak = INITIAL_BALANCE
    max_dd = 0
    daily_pnl = 0
    last_day = None
    max_daily_loss = -0.04
    paused = False
    total_pnl = 0
    win_pnl = 0
    loss_pnl = 0

    warmup = 55
    for i in range(warmup, len(d)):
        row = d.iloc[i]
        rsi = row["rsi"]
        close = row["close"]
        low = row["low"]
        high = row["high"]
        prev_rsi = d.iloc[i - 1]["rsi"]

        if pd.isna(rsi) or pd.isna(prev_rsi):
            continue

        # Daily reset
        day = d.index[i].date()
        if day != last_day:
            daily_pnl = 0
            paused = False
            last_day = day

        if paused:
            continue

        if in_position:
            # Check exits
            exit_price = None
            if low <= sl:
                exit_price = sl
            elif high >= tp:
                exit_price = tp
            elif i - entry_idx >= 48:
                exit_price = close
            elif rsi > rsi_exit and (close - entry_price) / entry_price > 0.003:
                exit_price = close

            if exit_price:
                pnl_pct = (exit_price - entry_price) / entry_price
                pnl_abs = pnl_pct * (balance * 0.15)  # 15% position
                balance += pnl_abs
                total_pnl += pnl_abs
                daily_pnl += pnl_pct
                trades += 1
                if pnl_pct > 0:
                    wins += 1
                    win_pnl += pnl_abs
                else:
                    loss_pnl += abs(pnl_abs)
                peak = max(peak, balance)
                dd = (peak - balance) / peak
                max_dd = max(max_dd, dd)
                in_position = False

                if daily_pnl < max_daily_loss:
                    paused = True

        else:
            # Check entries: RSI was below threshold, now recovering + green candle
            green = close > row["open"]
            if prev_rsi < rsi_entry and rsi > prev_rsi and green:
                entry_price = close
                sl = close * (1 - sl_pct)
                tp = close * (1 + tp_pct)
                entry_idx = i
                in_position = True

    # Close remaining position
    if in_position:
        last_close = d.iloc[-1]["close"]
        pnl_pct = (last_close - entry_price) / entry_price
        pnl_abs = pnl_pct * (balance * 0.15)
        balance += pnl_abs
        trades += 1
        if pnl_pct > 0:
            wins += 1

    days = (d.index[-1] - d.index[0]).days or 1
    total_ret = (balance - INITIAL_BALANCE) / INITIAL_BALANCE

    return {
        "rsi_entry": rsi_entry,
        "rsi_exit": rsi_exit,
        "sl_pct": sl_pct,
        "tp_pct": tp_pct,
        "rsi_period": rsi_period,
        "trades": trades,
        "wins": wins,
        "win_rate": wins / trades * 100 if trades > 0 else 0,
        "total_return": total_ret * 100,
        "daily_return": total_ret / days * 100,
        "max_dd": max_dd * 100,
        "profit_factor": win_pnl / loss_pnl if loss_pnl > 0 else 999,
        "balance": balance,
    }


async def main():
    setup_logging("logs")

    since = sys.argv[1] if len(sys.argv) > 1 else "2024-07-01"
    until = sys.argv[2] if len(sys.argv) > 2 else "2025-03-01"

    cfg = Config()

    # Test both timeframes
    for tf in ["15m", "1h"]:
        log.info("=" * 60)
        log.info("Optimizing for %s timeframe (%s to %s)", tf, since, until)
        log.info("=" * 60)

        cfg.trading.timeframe = tf
        df = await fetch_historical(cfg.trading.symbol, tf, since, until)
        log.info("Loaded %d candles", len(df))

        # Parameter grid
        rsi_entries = [30, 35, 40, 45]
        rsi_exits = [60, 65, 70]
        sl_pcts = [0.01, 0.015, 0.02, 0.025]
        tp_pcts = [0.015, 0.02, 0.025, 0.03, 0.04]
        rsi_periods = [7, 10, 14]

        results = []
        total = len(rsi_entries) * len(rsi_exits) * len(sl_pcts) * len(tp_pcts) * len(rsi_periods)
        log.info("Testing %d combinations...", total)

        for re, rx, sl, tp, rp in itertools.product(
            rsi_entries, rsi_exits, sl_pcts, tp_pcts, rsi_periods
        ):
            if tp <= sl:
                continue
            r = fast_backtest(df, cfg, re, rx, sl, tp, tf, rp)
            if r["trades"] >= 10:  # Min 10 trades for statistical relevance
                results.append(r)

        if not results:
            log.info("No combinations with >= 10 trades")
            continue

        # Sort by profit factor, then return
        results.sort(key=lambda x: (x["profit_factor"], x["total_return"]), reverse=True)

        print(f"\n{'=' * 80}")
        print(f"  TOP 10 CONFIGURATIONS — {tf}")
        print(f"{'=' * 80}")
        print(f"{'RSI_P':>5} {'RSI_E':>5} {'RSI_X':>5} {'SL%':>5} {'TP%':>5} "
              f"{'Trades':>6} {'WinR%':>6} {'Return%':>8} {'Daily%':>7} {'MaxDD%':>6} {'PF':>6}")
        print("-" * 80)

        for r in results[:10]:
            print(f"{r['rsi_period']:>5} {r['rsi_entry']:>5} {r['rsi_exit']:>5} "
                  f"{r['sl_pct']*100:>5.1f} {r['tp_pct']*100:>5.1f} "
                  f"{r['trades']:>6} {r['win_rate']:>6.1f} {r['total_return']:>8.2f} "
                  f"{r['daily_return']:>7.3f} {r['max_dd']:>6.2f} {r['profit_factor']:>6.2f}")

        print(f"\nTotal configs tested: {len(results)} (with >= 10 trades)")

        # Also show highest return configs
        results.sort(key=lambda x: x["total_return"], reverse=True)
        print(f"\n{'=' * 80}")
        print(f"  TOP 10 BY RETURN — {tf}")
        print(f"{'=' * 80}")
        print(f"{'RSI_P':>5} {'RSI_E':>5} {'RSI_X':>5} {'SL%':>5} {'TP%':>5} "
              f"{'Trades':>6} {'WinR%':>6} {'Return%':>8} {'Daily%':>7} {'MaxDD%':>6} {'PF':>6}")
        print("-" * 80)
        for r in results[:10]:
            print(f"{r['rsi_period']:>5} {r['rsi_entry']:>5} {r['rsi_exit']:>5} "
                  f"{r['sl_pct']*100:>5.1f} {r['tp_pct']*100:>5.1f} "
                  f"{r['trades']:>6} {r['win_rate']:>6.1f} {r['total_return']:>8.2f} "
                  f"{r['daily_return']:>7.3f} {r['max_dd']:>6.2f} {r['profit_factor']:>6.2f}")


if __name__ == "__main__":
    asyncio.run(main())
