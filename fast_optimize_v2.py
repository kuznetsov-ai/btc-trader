#!/usr/bin/env python3
"""Show what IS achievable — no threshold filters."""
import asyncio
import itertools
import numpy as np
import pandas as pd
from backtest import fetch_historical
from src.indicators import _rsi
import logging
logging.getLogger("bot").setLevel(logging.WARNING)

INIT = 10000.0

def fast_scalp(closes, opens, lows, highs, days, rsi_arr,
               rsi_entry, rsi_exit, sl_pct, tp_pct, pos_pct, leverage):
    n = len(closes)
    balance = INIT
    in_pos = False
    ep = sp = tpp = 0.0
    ei = trades = wins = 0
    peak = balance
    max_dd = 0.0
    dpnl = 0.0
    ld = -1
    wp = lp = 0.0
    daily_pnls = []
    cl = 0
    for i in range(1, n):
        r = rsi_arr[i]
        pr = rsi_arr[i - 1]
        if np.isnan(r) or np.isnan(pr): continue
        c, lo, hi, op = closes[i], lows[i], highs[i], opens[i]
        d = days[i]
        if d != ld:
            if ld >= 0: daily_pnls.append(dpnl)
            dpnl = 0.0; cl = 0; ld = d
        if dpnl <= -0.03 or cl >= 4: continue
        if in_pos:
            ex = 0.0
            if lo <= sp: ex = sp
            elif hi >= tpp: ex = tpp
            elif r > rsi_exit and (c - ep) / ep > 0.002: ex = c
            elif i - ei >= 24: ex = c
            if ex > 0:
                pnl = (ex - ep) / ep * leverage
                pabs = pnl * (balance * pos_pct) - balance * pos_pct * leverage * 0.0011
                balance += pabs; dpnl += pabs / INIT; trades += 1
                if pabs > 0: wins += 1; wp += pabs; cl = 0
                else: lp += abs(pabs); cl += 1
                peak = max(peak, balance)
                dd = (peak - balance) / peak
                if dd > max_dd: max_dd = dd
                in_pos = False
        else:
            if pr < rsi_entry and r > pr and c > op:
                ep = c; sp = c * (1 - sl_pct); tpp = c * (1 + tp_pct); ei = i; in_pos = True
    if ld >= 0: daily_pnls.append(dpnl)
    ndays = len(daily_pnls)
    if trades < 10 or ndays == 0: return None
    ad = sum(daily_pnls) / ndays
    tr = (balance - INIT) / INIT
    pf = wp / lp if lp > 0 else 99
    return (rsi_entry, rsi_exit, sl_pct*100, tp_pct*100, pos_pct*100, leverage,
            trades, wins/trades*100 if trades else 0, ad*100, tr*100, max_dd*100, pf)

async def main():
    logging.basicConfig(level=logging.WARNING)
    df = await fetch_historical("BTC/USDT", "5m", "2025-01-01", "2025-04-01")
    print(f"Candles: {len(df)}")

    closes = df["close"].values.astype(np.float64)
    opens = df["open"].values.astype(np.float64)
    lows = df["low"].values.astype(np.float64)
    highs = df["high"].values.astype(np.float64)
    day_dates = df.index.date
    unique_days = {d: i for i, d in enumerate(sorted(set(day_dates)))}
    days = np.array([unique_days[d] for d in day_dates], dtype=np.int32)

    rsi_cache = {}
    for rp in [5, 7, 10]:
        rsi_cache[rp] = _rsi(df["close"], rp).values.astype(np.float64)

    all_results = []
    valid = [(rp, re, rx, sl, tp, pp, lev)
             for rp, re, rx, sl, tp, pp, lev in itertools.product(
                 [5, 7, 10], [35, 40, 45], [60, 65, 70],
                 [0.003, 0.005, 0.007], [0.008, 0.01, 0.015, 0.02],
                 [0.3, 0.5, 0.8], [1, 2, 3, 5])
             if tp > sl]

    for rp, re, rx, sl, tp, pp, lev in valid:
        r = fast_scalp(closes, opens, lows, highs, days, rsi_cache[rp],
                       re, rx, sl, tp, pp, lev)
        if r: all_results.append((rp, *r))

    all_results.sort(key=lambda x: x[9], reverse=True)

    print(f"\nTotal configs with 10+ trades: {len(all_results)}")
    hdr = f'{"RSI":>3} {"RE":>4} {"RX":>4} {"SL":>5} {"TP":>5} {"Pos":>4} {"Lev":>4} {"#":>5} {"WR":>5} {"Day%":>7} {"Tot%":>8} {"DD%":>6} {"PF":>5}'
    print(f"\nALL TOP 30 (best daily return, no filters):\n{hdr}\n{'-'*78}")
    for r in all_results[:30]:
        print(f"{r[0]:>3} {r[1]:>4} {r[2]:>4} {r[3]:>5.1f} {r[4]:>5.1f} {r[5]:>4.0f} {r[6]:>4}x {r[7]:>5} {r[8]:>5.1f} {r[9]:>7.3f} {r[10]:>8.2f} {r[11]:>6.1f} {r[12]:>5.2f}")

if __name__ == "__main__":
    asyncio.run(main())
