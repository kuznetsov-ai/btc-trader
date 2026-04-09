#!/usr/bin/env python3
"""Combined backtest: 3x leveraged RSI trading + funding rate income simulation."""

import asyncio
import sys
import numpy as np
import pandas as pd
from backtest import fetch_historical
from src.indicators import _rsi
from src.logger_setup import setup_logging
import logging

log = logging.getLogger("bot.combined")

INIT = 10000.0


async def main():
    setup_logging("logs")

    since = sys.argv[1] if len(sys.argv) > 1 else "2024-01-01"
    until = sys.argv[2] if len(sys.argv) > 2 else "2025-06-01"
    leverage = int(sys.argv[3]) if len(sys.argv) > 3 else 3

    df = await fetch_historical("BTC/USDT", "1h", since, until)
    log.info("Loaded %d 1h candles", len(df))

    # ===== COMPONENT 1: Leveraged RSI Trading =====
    closes = df["close"].values
    opens = df["open"].values
    lows = df["low"].values
    highs = df["high"].values
    rsi = _rsi(df["close"], 10).values

    trade_balance = INIT * 0.55  # 55% for trading
    arb_capital = INIT * 0.40     # 40% for funding arb
    reserve = INIT * 0.05         # 5% reserve

    # Trading params (optimized)
    rsi_entry, rsi_exit = 45, 75
    sl_pct, tp_pct = 0.01, 0.04
    pos_pct = 0.35  # 35% of trading capital per trade

    in_pos = False
    ep = sp = tpp = 0.0
    ei = 0
    trades = wins = 0
    peak_trade = trade_balance
    max_dd_trade = 0.0
    dpnl = 0.0
    ld = None
    wp = lp = 0.0
    daily_trade_pnls = []
    cl = 0

    for i in range(20, len(df)):
        r, pr = rsi[i], rsi[i - 1]
        c, lo, hi, op = closes[i], lows[i], highs[i], opens[i]
        if np.isnan(r) or np.isnan(pr):
            continue
        day = df.index[i].date()
        if day != ld:
            if ld:
                daily_trade_pnls.append(dpnl)
            dpnl = 0.0
            cl = 0
            ld = day
        if dpnl <= -0.04 or cl >= 4:
            continue

        if in_pos:
            ex = 0.0
            if lo <= sp: ex = sp
            elif hi >= tpp: ex = tpp
            elif r > rsi_exit and (c - ep) / ep > 0.005: ex = c
            elif i - ei >= 48: ex = c
            if ex > 0:
                pnl_pct = (ex - ep) / ep * leverage
                pabs = pnl_pct * (trade_balance * pos_pct)
                fee = trade_balance * pos_pct * leverage * 0.0011
                pabs -= fee
                trade_balance += pabs
                dpnl += pabs / INIT
                trades += 1
                if pabs > 0: wins += 1; wp += pabs; cl = 0
                else: lp += abs(pabs); cl += 1
                peak_trade = max(peak_trade, trade_balance)
                dd = (peak_trade - trade_balance) / peak_trade
                max_dd_trade = max(max_dd_trade, dd)
                in_pos = False
        else:
            if pr < rsi_entry and r > pr and c > op:
                ep = c; sp = c * (1 - sl_pct); tpp = c * (1 + tp_pct)
                ei = i; in_pos = True

    if ld:
        daily_trade_pnls.append(dpnl)

    # ===== COMPONENT 2: Funding Rate Income =====
    # BTC funding rate averages ~0.01% per 8h (3x daily)
    # Conservative estimate: 0.008% avg (some periods are negative)
    avg_funding_rate = 0.00008  # per 8h collection
    collections_per_day = 3
    days = max((df.index[-1] - df.index[0]).days, 1)
    total_funding = arb_capital * avg_funding_rate * collections_per_day * days
    daily_funding = arb_capital * avg_funding_rate * collections_per_day

    # ===== COMBINED RESULTS =====
    total_equity = trade_balance + arb_capital + total_funding + reserve
    total_return = (total_equity - INIT) / INIT
    trade_return = (trade_balance - INIT * 0.55) / (INIT * 0.55)
    funding_return = total_funding / arb_capital

    trade_days = len(daily_trade_pnls)
    avg_daily_trade = sum(daily_trade_pnls) / trade_days if trade_days > 0 else 0
    avg_daily_funding = daily_funding / INIT
    avg_daily_combined = avg_daily_trade + avg_daily_funding
    pos_trade_days = sum(1 for d in daily_trade_pnls if d > 0)
    trade_pf = wp / lp if lp > 0 else 99

    print(f"\n{'='*60}")
    print(f"  COMBINED BACKTEST: RSI {leverage}x + Funding Arb")
    print(f"{'='*60}")
    print(f"  Period: {df.index[0].date()} to {df.index[-1].date()} ({days} days)")
    print(f"  Initial: ${INIT:,.0f} (55% trade, 40% arb, 5% reserve)")
    print()
    print(f"  --- RSI TRADING ({leverage}x leverage) ---")
    print(f"  Trades: {trades} ({trades/days:.1f}/day)")
    print(f"  Win rate: {wins/trades*100:.1f}%" if trades > 0 else "  No trades")
    print(f"  Profit factor: {trade_pf:.2f}")
    print(f"  Trade capital: ${INIT*0.55:,.0f} -> ${trade_balance:,.0f} ({trade_return*100:+.1f}%)")
    print(f"  Max drawdown: {max_dd_trade*100:.1f}%")
    print(f"  Avg daily: {avg_daily_trade*100:.3f}%")
    print(f"  Positive days: {pos_trade_days}/{trade_days} ({pos_trade_days/trade_days*100:.0f}%)" if trade_days > 0 else "")
    print()
    print(f"  --- FUNDING RATE ARBITRAGE ---")
    print(f"  Capital deployed: ${arb_capital:,.0f}")
    print(f"  Avg rate: {avg_funding_rate*100:.4f}% per 8h")
    print(f"  Total earned: ${total_funding:,.2f} ({funding_return*100:.1f}%)")
    print(f"  Daily income: ${daily_funding:.2f}/day")
    print(f"  Annualized: {funding_return/days*365*100:.1f}%")
    print()
    print(f"  --- COMBINED ---")
    print(f"  Final equity: ${total_equity:,.2f}")
    print(f"  Total return: {total_return*100:+.2f}%")
    print(f"  Monthly: {total_return/days*30*100:+.2f}%")
    print(f"  Annualized: {total_return/days*365*100:+.1f}%")
    print(f"  Avg daily: {avg_daily_combined*100:.3f}%")
    print(f"{'='*60}")

    # Projections
    print(f"\n  Projections at different capital levels:")
    for cap in [500, 1000, 5000, 10000]:
        monthly = cap * total_return / days * 30
        yearly = cap * total_return / days * 365
        print(f"  ${cap:>6,}: ${monthly:>8.2f}/month  ${yearly:>10.2f}/year")


if __name__ == "__main__":
    asyncio.run(main())
