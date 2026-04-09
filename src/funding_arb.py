"""Funding Rate Arbitrage — delta-neutral strategy.

How it works:
1. When funding rate is positive (longs pay shorts):
   - Buy BTC on spot (long exposure)
   - Short BTC perpetual futures (short exposure)
   - Net delta = 0 (market-neutral)
   - Collect funding payment every 8 hours

2. When funding rate is negative (shorts pay longs):
   - Reverse: sell spot, go long futures
   - Or just close the arb position

Typical yield: 0.01-0.05% per 8h = 0.03-0.15%/day = 11-55%/year
Risk: very low (spread risk between spot and futures price)
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from config.settings import FundingConfig
from src.exchange import Exchange

log = logging.getLogger("bot.funding")


class FundingArbitrage:
    def __init__(self, exchange: Exchange, cfg: FundingConfig):
        self.exchange = exchange
        self.cfg = cfg
        self.arb_active = False
        self.arb_size_btc = 0.0
        self.arb_entry_rate = 0.0
        self.total_funding_earned = 0.0
        self.funding_collections = 0

    async def check_and_manage(self, symbol: str, balance: float) -> float:
        """Check funding rate and manage arb position. Returns funding earned."""
        if not self.cfg.enabled:
            return 0.0

        rate_info = await self.exchange.fetch_funding_rate(symbol)
        rate = rate_info["rate"]

        if rate == 0:
            return 0.0

        log.info("Funding rate: %.4f%% (%.2f%% annualized)",
                 rate * 100, rate * 3 * 365 * 100)

        funding_earned = 0.0

        if self.arb_active:
            # Collect funding from existing position
            funding_earned = self.arb_size_btc * rate
            self.total_funding_earned += funding_earned
            self.funding_collections += 1
            log.info("Funding collected: $%.4f (total: $%.4f from %d collections)",
                     funding_earned, self.total_funding_earned, self.funding_collections)

            # Close arb if rate turned negative (we'd be paying)
            if rate < -0.0001:
                await self._close_arb(symbol)
                log.info("Arb closed — funding rate turned negative")

        elif rate >= self.cfg.min_rate_threshold:
            # Open new arb position
            await self._open_arb(symbol, balance, rate)

        return funding_earned

    async def _open_arb(self, symbol: str, balance: float, rate: float):
        """Open delta-neutral arb: long spot + short futures."""
        arb_capital = balance * self.cfg.position_pct
        ticker = await self.exchange.fetch_ticker(symbol)
        price = ticker["last"]

        # Size in BTC
        size_btc = arb_capital / price
        size_btc = round(size_btc, 4)  # Round to Bybit minimum step

        if size_btc < 0.001:
            log.info("Arb size too small (%.6f BTC), skipping", size_btc)
            return

        try:
            # 1. Buy spot BTC
            await self.exchange.spot_buy(symbol, size_btc)
            # 2. Short perpetual futures
            await self.exchange.open_short(symbol, size_btc)

            self.arb_active = True
            self.arb_size_btc = size_btc
            self.arb_entry_rate = rate

            log.info("ARB OPENED: long %.4f BTC spot + short %.4f BTC perp @ rate %.4f%%",
                     size_btc, size_btc, rate * 100)
        except Exception as e:
            log.error("Failed to open arb: %s", e)
            # Try to close any partial position
            await self._close_arb(symbol)

    async def _close_arb(self, symbol: str):
        """Close arb: sell spot + close short."""
        if self.arb_size_btc <= 0:
            self.arb_active = False
            return

        try:
            await self.exchange.spot_sell(symbol, self.arb_size_btc)
            await self.exchange.close_short(symbol, self.arb_size_btc)
            log.info("ARB CLOSED: %.4f BTC (earned $%.4f total)",
                     self.arb_size_btc, self.total_funding_earned)
        except Exception as e:
            log.error("Failed to close arb: %s", e)

        self.arb_active = False
        self.arb_size_btc = 0.0

    async def close_all(self, symbol: str):
        """Emergency close all arb positions."""
        if self.arb_active:
            await self._close_arb(symbol)

    def summary(self) -> dict:
        return {
            "active": self.arb_active,
            "size_btc": self.arb_size_btc,
            "total_funding_earned": f"${self.total_funding_earned:.4f}",
            "collections": self.funding_collections,
            "avg_per_collection": f"${self.total_funding_earned / self.funding_collections:.4f}" if self.funding_collections > 0 else "$0",
        }
