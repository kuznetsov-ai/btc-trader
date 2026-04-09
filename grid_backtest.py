#!/usr/bin/env python3
"""Realistic grid trading backtester.

Grid trading works by:
1. Place buy limit orders below current price at regular intervals
2. When a buy fills, place a sell limit one grid level above
3. When a sell fills, place a buy limit one grid level below
4. Profit = grid_size * completed round trips

Key risk: in downtrends, buys fill but sells don't → you hold losing inventory.
"""

import asyncio
import sys
from collections import OrderedDict

import pandas as pd

from backtest import fetch_historical
from src.logger_setup import setup_logging
import logging

log = logging.getLogger("bot.grid")

INITIAL_BALANCE = 10_000.0


def simulate_grid(df: pd.DataFrame, grid_pct: float, num_grids: int,
                  invest_pct: float, leverage: float = 1.0) -> dict:
    """Simulate grid trading on OHLCV data.

    Args:
        grid_pct: spacing between grid levels (e.g., 0.003 = 0.3%)
        num_grids: number of grid levels above and below center
        invest_pct: fraction of balance to deploy in grid
        leverage: leverage multiplier (1 = spot, 2-5 = futures)
    """
    usdt_balance = INITIAL_BALANCE
    btc_inventory = 0.0  # BTC held from filled buy orders
    invested = INITIAL_BALANCE * invest_pct
    per_grid = invested / num_grids * leverage

    # Track daily PnL
    daily_profits = []
    daily_pnl = 0.0
    last_day = None

    # Metrics
    total_round_trips = 0
    total_fees = 0.0
    total_gross_profit = 0.0
    peak_equity = INITIAL_BALANCE
    max_dd = 0.0

    # Grid state: dict of price_level -> {"side": "buy"/"sell", "filled": bool, "fill_price": float}
    # We rebuild the grid around each candle's open price

    center_price = df.iloc[0]["close"]
    fee_rate = 0.0007  # 0.07% per side (Bybit/Binance maker+taker avg)

    # Active buy levels and pending sell levels
    active_buys = {}   # price_level -> qty (in USDT)
    pending_sells = {}  # price_level -> {"buy_price": float, "qty_btc": float}

    def rebuild_grid(price):
        """Place buy orders below current price."""
        nonlocal active_buys
        active_buys = {}
        for i in range(1, num_grids + 1):
            level = price * (1 - grid_pct * i)
            active_buys[level] = per_grid

    rebuild_grid(center_price)
    rebalance_every = 96  # Rebuild grid every 96 candles (8h on 5m)

    for idx in range(1, len(df)):
        row = df.iloc[idx]
        low = row["low"]
        high = row["high"]
        close = row["close"]

        day = df.index[idx].date()
        if day != last_day:
            if last_day is not None:
                daily_profits.append(daily_pnl)
            daily_pnl = 0.0
            last_day = day

        # Check buy orders filled (price went down to level)
        filled_buys = []
        for level, usdt_amount in active_buys.items():
            if low <= level:
                # Buy filled
                qty_btc = usdt_amount / level
                fee = usdt_amount * fee_rate
                total_fees += fee
                usdt_balance -= fee
                btc_inventory += qty_btc

                # Place sell order one grid above
                sell_level = level * (1 + grid_pct)
                pending_sells[sell_level] = {
                    "buy_price": level,
                    "qty_btc": qty_btc,
                }
                filled_buys.append(level)

        for level in filled_buys:
            del active_buys[level]

        # Check sell orders filled (price went up to level)
        filled_sells = []
        for level, info in pending_sells.items():
            if high >= level:
                # Sell filled — round trip complete
                qty_btc = info["qty_btc"]
                buy_price = info["buy_price"]
                sell_value = qty_btc * level
                buy_cost = qty_btc * buy_price

                fee = sell_value * fee_rate
                total_fees += fee

                gross = sell_value - buy_cost
                net = gross - fee
                usdt_balance += net
                btc_inventory -= qty_btc
                total_gross_profit += gross
                total_round_trips += 1
                daily_pnl += net / INITIAL_BALANCE

                # Place new buy order at the buy level again
                active_buys[buy_price] = per_grid

                filled_sells.append(level)

        for level in filled_sells:
            del pending_sells[level]

        # Periodically rebalance grid center if price drifted too far
        if idx % rebalance_every == 0:
            if active_buys:
                grid_center = max(active_buys.keys())
            else:
                grid_center = close

            # If price moved more than 3x grid range from center, rebuild
            grid_range = grid_pct * num_grids * close
            if abs(close - grid_center) > grid_range * 2:
                # Close out inventory at market
                if btc_inventory > 0:
                    sell_value = btc_inventory * close
                    avg_buy = sum(s["buy_price"] * s["qty_btc"] for s in pending_sells.values()) / btc_inventory if btc_inventory > 0 else close
                    pnl = (close - avg_buy) * btc_inventory
                    usdt_balance += pnl
                    daily_pnl += pnl / INITIAL_BALANCE
                    btc_inventory = 0
                    pending_sells.clear()

                rebuild_grid(close)

        # Track equity (USDT + BTC value)
        equity = usdt_balance + btc_inventory * close
        peak_equity = max(peak_equity, equity)
        dd = (peak_equity - equity) / peak_equity
        max_dd = max(max_dd, dd)

    # Final: close remaining inventory
    if last_day:
        daily_profits.append(daily_pnl)

    final_close = df.iloc[-1]["close"]
    equity = usdt_balance + btc_inventory * final_close

    days = len(daily_profits)
    avg_daily = sum(daily_profits) / days if days > 0 else 0
    positive_days = sum(1 for d in daily_profits if d > 0)
    total_return = (equity - INITIAL_BALANCE) / INITIAL_BALANCE

    return {
        "grid_pct": f"{grid_pct*100:.2f}%",
        "num_grids": num_grids,
        "invest_pct": f"{invest_pct*100:.0f}%",
        "leverage": f"{leverage:.0f}x",
        "round_trips": total_round_trips,
        "avg_daily": avg_daily * 100,
        "total_return": total_return * 100,
        "max_dd": max_dd * 100,
        "positive_days_pct": positive_days / days * 100 if days > 0 else 0,
        "total_fees": total_fees,
        "gross_profit": total_gross_profit,
        "equity": equity,
        "btc_held": btc_inventory,
    }


async def main():
    setup_logging("logs")

    since = sys.argv[1] if len(sys.argv) > 1 else "2025-01-01"
    until = sys.argv[2] if len(sys.argv) > 2 else "2025-04-01"

    df = await fetch_historical("BTC/USDT", "5m", since, until)
    log.info("Loaded %d candles", len(df))

    print(f"\nBTC range: ${df['close'].min():.0f} - ${df['close'].max():.0f}")
    print(f"Period: {df.index[0].date()} to {df.index[-1].date()}")

    # Test combinations
    import itertools
    configs = []
    for gp, ng, ip, lev in itertools.product(
        [0.002, 0.003, 0.005, 0.007, 0.01],  # 0.2% - 1% grid
        [5, 10, 15, 20, 30],                   # grid levels
        [0.5, 0.7, 0.9],                        # invest %
        [1, 2, 3, 5],                            # leverage
    ):
        r = simulate_grid(df, gp, ng, ip, lev)
        if r["round_trips"] >= 10:
            configs.append(r)

    # Sort by avg daily return
    configs.sort(key=lambda x: x["avg_daily"], reverse=True)

    print(f"\n{'='*85}")
    print(f"TOP 20 GRID CONFIGS (sorted by daily %)")
    print(f"{'='*85}")
    print(f"{'Grid':>6} {'Lvls':>5} {'Inv%':>5} {'Lev':>4} {'Trips':>6} {'Daily%':>7} {'Total%':>8} {'DD%':>7} {'Win%':>6}")
    print("-" * 85)
    for r in configs[:20]:
        print(f"{r['grid_pct']:>6} {r['num_grids']:>5} {r['invest_pct']:>5} {r['leverage']:>4} "
              f"{r['round_trips']:>6} {r['avg_daily']:>7.3f} {r['total_return']:>8.2f} "
              f"{r['max_dd']:>7.2f} {r['positive_days_pct']:>6.1f}")

    # Filter: daily > 0.8%, DD < 30%
    safe = [r for r in configs if r["avg_daily"] > 0.8 and r["max_dd"] < 30]
    if safe:
        safe.sort(key=lambda x: (x["avg_daily"], -x["max_dd"]), reverse=True)
        print(f"\n{'='*85}")
        print(f"VIABLE CONFIGS (Daily>0.8%, DD<30%)")
        print(f"{'='*85}")
        for r in safe[:10]:
            print(f"{r['grid_pct']:>6} {r['num_grids']:>5} {r['invest_pct']:>5} {r['leverage']:>4} "
                  f"{r['round_trips']:>6} {r['avg_daily']:>7.3f} {r['total_return']:>8.2f} "
                  f"{r['max_dd']:>7.2f} {r['positive_days_pct']:>6.1f}")
    else:
        # Show best we have
        best = [r for r in configs if r["max_dd"] < 30]
        best.sort(key=lambda x: x["avg_daily"], reverse=True)
        print(f"\nNo configs hit 0.8%/day with DD<30%. Best:")
        for r in best[:5]:
            print(f"{r['grid_pct']:>6} {r['num_grids']:>5} {r['invest_pct']:>5} {r['leverage']:>4} "
                  f"{r['round_trips']:>6} {r['avg_daily']:>7.3f} {r['total_return']:>8.2f} "
                  f"{r['max_dd']:>7.2f} {r['positive_days_pct']:>6.1f}")


if __name__ == "__main__":
    asyncio.run(main())
