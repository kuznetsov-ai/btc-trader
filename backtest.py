#!/usr/bin/env python3
"""Backtester v2 — compute indicators once on full dataset, then step through."""

import asyncio
import sys
from datetime import datetime, timezone

import ccxt.async_support as ccxt
import pandas as pd

from config.settings import Config
from src.indicators import compute_indicators
from src.strategy import generate_signal, check_exit, Regime
from src.risk_manager import RiskManager
from src.logger_setup import setup_logging

import logging

log = logging.getLogger("bot.backtest")

INITIAL_BALANCE = 10_000.0


async def fetch_historical(symbol: str, timeframe: str, since: str, until: str) -> pd.DataFrame:
    exchange = ccxt.binance({"enableRateLimit": True})
    all_candles = []
    since_ts = int(datetime.fromisoformat(since).replace(tzinfo=timezone.utc).timestamp() * 1000)
    until_ts = int(datetime.fromisoformat(until).replace(tzinfo=timezone.utc).timestamp() * 1000)

    log.info("Fetching %s %s from %s to %s...", symbol, timeframe, since, until)

    while since_ts < until_ts:
        try:
            candles = await exchange.fetch_ohlcv(symbol, timeframe, since=since_ts, limit=1000)
        except Exception as e:
            log.error("Fetch error: %s, retrying...", e)
            await asyncio.sleep(2)
            continue

        if not candles:
            break

        all_candles.extend(candles)
        since_ts = candles[-1][0] + 1
        log.info("  fetched %d candles (total: %d)", len(candles), len(all_candles))
        if len(candles) < 1000:
            break

    await exchange.close()

    df = pd.DataFrame(all_candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df.set_index("timestamp", inplace=True)
    df = df[~df.index.duplicated(keep="first")]
    end_dt = pd.Timestamp(until, tz="UTC")
    df = df[df.index <= end_dt]
    return df


def run_backtest(df_all: pd.DataFrame, cfg: Config) -> dict:
    """Step through pre-computed indicator data candle by candle."""
    risk = RiskManager(cfg.risk)
    risk.set_initial_balance(INITIAL_BALANCE)
    balance = INITIAL_BALANCE
    trades_log = []

    warmup = max(cfg.trading.ema_trend, cfg.trading.bb_length, cfg.trading.macd_slow) + 10
    total = len(df_all)
    log.info("Backtesting %d candles (warmup=%d)", total, warmup)

    for i in range(warmup, total):
        risk.tick_candle()

        # Pass a slice of the full (pre-computed) dataframe ending at current candle
        df = df_all.iloc[max(0, i - 100):i + 1]
        row = df.iloc[-1]
        price = row["close"]
        ts = df.index[-1]

        # Check exit
        if risk.position:
            reason = check_exit(
                row,
                risk.position.entry_price,
                risk.position.stop_loss,
                risk.position.take_profit,
                risk.position.hold_candles,
                Regime(risk.position.regime),
            )
            if reason:
                # Realistic exit price
                if "stop_loss" in reason:
                    exit_price = risk.position.stop_loss
                elif "take_profit" in reason:
                    exit_price = risk.position.take_profit
                else:
                    exit_price = price

                pnl = risk.close_position(exit_price, balance)
                balance += pnl
                trades_log.append({
                    "time": str(ts),
                    "type": "exit",
                    "price": exit_price,
                    "pnl": pnl,
                    "pnl_pct": (exit_price - trades_log[-1]["price"]) / trades_log[-1]["price"] * 100 if trades_log else 0,
                    "reason": reason,
                    "balance": balance,
                })
            continue

        # Check entry
        sim_date = ts.strftime("%Y-%m-%d") if hasattr(ts, 'strftime') else str(ts)[:10]
        can, msg = risk.can_trade(current_date=sim_date)
        if not can:
            continue

        signal = generate_signal(df, cfg.trading)
        if signal is None or signal.action != "buy":
            continue

        size = risk.calc_position_size(balance, price, signal.stop_loss, signal.confidence)
        if size <= 0 or size * price < 10:
            continue

        risk.open_position(price, size, signal.stop_loss, signal.take_profit, signal.regime.value)
        trades_log.append({
            "time": str(ts),
            "type": "entry",
            "price": price,
            "size": size,
            "regime": signal.regime.value,
            "reason": signal.reason,
        })

    # Close remaining position
    if risk.position:
        last_price = df_all.iloc[-1]["close"]
        pnl = risk.close_position(last_price, balance)
        balance += pnl

    # Results
    total_return = (balance - INITIAL_BALANCE) / INITIAL_BALANCE
    days = max((df_all.index[-1] - df_all.index[0]).days, 1)
    daily_return = total_return / days

    winning_pnl = sum(t.get("pnl", 0) for t in trades_log if t.get("pnl", 0) > 0)
    losing_pnl = abs(sum(t.get("pnl", 0) for t in trades_log if t.get("pnl", 0) < 0))
    pf = f"{winning_pnl / losing_pnl:.2f}" if losing_pnl > 0 else "N/A"

    return {
        "period": f"{df_all.index[0].date()} to {df_all.index[-1].date()}",
        "days": days,
        "initial_balance": INITIAL_BALANCE,
        "final_balance": round(balance, 2),
        "total_return": f"{total_return * 100:.2f}%",
        "daily_avg_return": f"{daily_return * 100:.3f}%",
        "monthly_return": f"{daily_return * 30 * 100:.2f}%",
        "total_trades": risk.total_trades,
        "wins": risk.total_wins,
        "losses": risk.total_trades - risk.total_wins,
        "win_rate": f"{risk.win_rate * 100:.1f}%",
        "max_drawdown": f"{risk.max_drawdown * 100:.2f}%",
        "profit_factor": pf,
        "avg_trades_per_day": f"{risk.total_trades / days:.1f}",
    }


async def main():
    setup_logging("logs")
    cfg = Config()

    since = sys.argv[1] if len(sys.argv) > 1 else "2024-07-01"
    until = sys.argv[2] if len(sys.argv) > 2 else "2025-03-01"

    df = await fetch_historical(cfg.trading.symbol, cfg.trading.timeframe, since, until)
    log.info("Loaded %d candles", len(df))

    if len(df) < 100:
        log.error("Not enough data for backtest")
        return

    # Compute all indicators ONCE on the full dataset
    log.info("Computing indicators on full dataset...")
    df = compute_indicators(df, cfg.trading)

    results = run_backtest(df, cfg)

    print("\n" + "=" * 55)
    print("          BACKTEST RESULTS")
    print("=" * 55)
    for k, v in results.items():
        print(f"  {k:>22}: {v}")
    print("=" * 55)

    win_rate = float(results["win_rate"].rstrip("%"))
    max_dd = float(results["max_drawdown"].rstrip("%"))
    if win_rate < 55:
        print("\n  [!] Win rate below 55%")
    if max_dd > 5:
        print("\n  [!] Max drawdown above 5%")
    if win_rate >= 55 and max_dd <= 5:
        print("\n  [OK] Strategy passes minimum requirements")


if __name__ == "__main__":
    asyncio.run(main())
