#!/usr/bin/env python3
"""Leveraged scalping optimizer on 5m candles."""
import asyncio
import itertools
import pandas as pd
from backtest import fetch_historical
from src.indicators import _rsi
from src.logger_setup import setup_logging
import logging

logging.getLogger("bot.backtest").setLevel(logging.WARNING)

INIT = 10000.0

def scalp(df, rsi_p, rsi_e, rsi_x, sl, tp, pp, lev):
    d = df.copy()
    d["rsi"] = _rsi(d["close"], rsi_p)
    balance = INIT
    in_pos = False
    ep = sp = tpp = 0.0
    ei = trades = wins = 0
    peak = balance
    max_dd = 0.0
    dpnl = 0.0
    ld = None
    wp = lp = 0.0
    dps = []
    cl = 0

    for i in range(20, len(d)):
        row = d.iloc[i]
        prev = d.iloc[i - 1]
        r = row["rsi"]
        pr = prev["rsi"]
        c, lo, hi, op = row["close"], row["low"], row["high"], row["open"]
        if pd.isna(r) or pd.isna(pr):
            continue
        day = d.index[i].date()
        if day != ld:
            if ld:
                dps.append(dpnl)
            dpnl = 0.0
            cl = 0
            ld = day
        if dpnl <= -0.03 or cl >= 4:
            continue

        if in_pos:
            ex = None
            if lo <= sp:
                ex = sp
            elif hi >= tpp:
                ex = tpp
            elif r > rsi_x and (c - ep) / ep > 0.002:
                ex = c
            elif i - ei >= 24:
                ex = c
            if ex is not None:
                pnl = (ex - ep) / ep * lev
                pabs = pnl * (balance * pp)
                fee = balance * pp * lev * 0.0011
                pabs -= fee
                balance += pabs
                dpnl += pabs / INIT
                trades += 1
                if pabs > 0:
                    wins += 1
                    wp += pabs
                    cl = 0
                else:
                    lp += abs(pabs)
                    cl += 1
                peak = max(peak, balance)
                dd = (peak - balance) / peak
                max_dd = max(max_dd, dd)
                in_pos = False
        else:
            if pr < rsi_e and r > pr and c > op:
                ep = c
                sp = c * (1 - sl)
                tpp = c * (1 + tp)
                ei = i
                in_pos = True

    if ld:
        dps.append(dpnl)
    days = len(dps)
    if trades < 20 or days == 0:
        return None
    ad = sum(dps) / days
    tr = (balance - INIT) / INIT
    pf = wp / lp if lp > 0 else 99
    pd_pct = sum(1 for x in dps if x > 0) / days * 100
    return (rsi_p, rsi_e, rsi_x, sl * 100, tp * 100, pp * 100, lev, trades,
            wins / trades * 100, ad * 100, tr * 100, max_dd * 100, pf, pd_pct)


async def main():
    setup_logging("logs")
    df = await fetch_historical("BTC/USDT", "5m", "2025-01-01", "2025-04-01")
    print(f"Candles: {len(df)}")

    results = []
    combos = list(itertools.product(
        [5, 7, 10], [35, 40, 45], [60, 65, 70],
        [0.003, 0.005, 0.007], [0.008, 0.01, 0.015],
        [0.3, 0.5], [1, 2, 3, 5],
    ))
    print(f"Testing {len(combos)} combinations...")

    for idx, (rp, re, rx, sl, tp, pp, lev) in enumerate(combos):
        if tp <= sl:
            continue
        r = scalp(df, rp, re, rx, sl, tp, pp, lev)
        if r and r[9] > 0.2:
            results.append(r)
        if idx % 200 == 0:
            print(f"  {idx}/{len(combos)}...")

    results.sort(key=lambda x: x[9], reverse=True)

    hdr = f'{"RSI":>3} {"RE":>4} {"RX":>4} {"SL":>5} {"TP":>5} {"Pos":>4} {"Lev":>4} {"#":>5} {"WR":>5} {"Day%":>6} {"Tot%":>7} {"DD%":>6} {"PF":>5} {"W/D":>5}'
    print(f"\nTOP 20:\n{hdr}\n{'-'*80}")
    for r in results[:20]:
        print(f"{r[0]:>3} {r[1]:>4} {r[2]:>4} {r[3]:>5.1f} {r[4]:>5.1f} {r[5]:>4.0f} {r[6]:>4}x {r[7]:>5} {r[8]:>5.1f} {r[9]:>6.2f} {r[10]:>7.1f} {r[11]:>6.1f} {r[12]:>5.2f} {r[13]:>5.1f}")

    safe = [r for r in results if r[11] < 20]
    if safe:
        safe.sort(key=lambda x: x[9], reverse=True)
        print(f"\nBEST with DD<20%:")
        for r in safe[:10]:
            print(f"{r[0]:>3} {r[1]:>4} {r[2]:>4} {r[3]:>5.1f} {r[4]:>5.1f} {r[5]:>4.0f} {r[6]:>4}x {r[7]:>5} {r[8]:>5.1f} {r[9]:>6.2f} {r[10]:>7.1f} {r[11]:>6.1f} {r[12]:>5.2f} {r[13]:>5.1f}")


if __name__ == "__main__":
    asyncio.run(main())
