"""Exchange wrapper — supports both spot and futures via CCXT."""

import asyncio
import logging
from typing import Optional

import ccxt.async_support as ccxt
import pandas as pd

from config.settings import ExchangeConfig

log = logging.getLogger("bot.exchange")


class Exchange:
    def __init__(self, cfg: ExchangeConfig):
        self.cfg = cfg
        cls = getattr(ccxt, cfg.name)
        default_type = "spot" if cfg.sandbox else "swap"
        self.api: ccxt.Exchange = cls({
            "apiKey": cfg.api_key,
            "secret": cfg.api_secret,
            "enableRateLimit": True,
            "options": {"defaultType": default_type},
        })
        if cfg.sandbox:
            self.api.set_sandbox_mode(True)
            log.info("Sandbox mode ON — %s testnet", cfg.name)

    async def close(self):
        await self.api.close()

    async def load_markets(self):
        await self.api.load_markets()

    async def set_leverage(self, symbol: str, leverage: int):
        """Set leverage for futures trading."""
        try:
            await self.api.set_leverage(leverage, symbol)
            log.info("Leverage set to %dx for %s", leverage, symbol)
        except Exception as e:
            log.warning("Set leverage failed (may already be set): %s", e)

    async def set_position_mode(self, hedge: bool = False):
        """Set one-way (False) or hedge (True) position mode."""
        try:
            await self.api.set_position_mode(hedge)
            log.info("Position mode: %s", "hedge" if hedge else "one-way")
        except Exception as e:
            log.warning("Set position mode failed: %s", e)

    # -- Market data --

    async def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 200) -> pd.DataFrame:
        raw = await self.api.fetch_ohlcv(symbol, timeframe, limit=limit)
        df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df.set_index("timestamp", inplace=True)
        return df

    async def fetch_ticker(self, symbol: str) -> dict:
        return await self.api.fetch_ticker(symbol)

    async def fetch_funding_rate(self, symbol: str) -> dict:
        """Fetch current funding rate for perpetual futures."""
        try:
            rate_info = await self.api.fetch_funding_rate(symbol)
            return {
                "rate": float(rate_info.get("fundingRate", 0)),
                "timestamp": rate_info.get("fundingTimestamp"),
                "next_timestamp": rate_info.get("nextFundingTimestamp"),
            }
        except Exception as e:
            log.error("Fetch funding rate failed: %s", e)
            return {"rate": 0, "timestamp": None, "next_timestamp": None}

    # -- Balance --

    async def fetch_balance(self) -> dict:
        bal = await self.api.fetch_balance()
        usdt_free = float(bal.get("USDT", {}).get("free", 0))
        usdt_total = float(bal.get("USDT", {}).get("total", 0))
        return {"USDT": usdt_free, "total_USDT": usdt_total}

    # -- Futures orders --

    async def open_long(self, symbol: str, amount: float) -> dict:
        """Open long position (market buy)."""
        log.info("LONG  %s  size=%.4f", symbol, amount)
        return await self.api.create_market_order(symbol, "buy", amount)

    async def close_long(self, symbol: str, amount: float) -> dict:
        """Close long position (market sell)."""
        log.info("CLOSE LONG  %s  size=%.4f", symbol, amount)
        return await self.api.create_market_order(symbol, "sell", amount, params={"reduceOnly": True})

    async def open_short(self, symbol: str, amount: float) -> dict:
        """Open short position (market sell)."""
        log.info("SHORT %s  size=%.4f", symbol, amount)
        return await self.api.create_market_order(symbol, "sell", amount)

    async def close_short(self, symbol: str, amount: float) -> dict:
        """Close short position (market buy)."""
        log.info("CLOSE SHORT %s  size=%.4f", symbol, amount)
        return await self.api.create_market_order(symbol, "buy", amount, params={"reduceOnly": True})

    # -- Spot orders (for funding arb) --

    async def spot_buy(self, symbol: str, amount: float) -> dict:
        """Spot market buy (for funding rate arb hedge)."""
        spot = self._spot_exchange()
        log.info("SPOT BUY  %s  amount=%.6f", symbol, amount)
        return await spot.create_market_buy_order(symbol, amount)

    async def spot_sell(self, symbol: str, amount: float) -> dict:
        """Spot market sell."""
        spot = self._spot_exchange()
        log.info("SPOT SELL %s  amount=%.6f", symbol, amount)
        return await spot.create_market_sell_order(symbol, amount)

    def _spot_exchange(self) -> ccxt.Exchange:
        """Get spot exchange instance (reuse connection with spot type)."""
        cls = getattr(ccxt, self.cfg.name)
        spot = cls({
            "apiKey": self.cfg.api_key,
            "secret": self.cfg.api_secret,
            "enableRateLimit": True,
            "options": {"defaultType": "spot"},
        })
        if self.cfg.sandbox:
            spot.set_sandbox_mode(True)
        return spot

    # -- Positions --

    async def fetch_positions(self, symbol: Optional[str] = None) -> list:
        """Fetch open futures positions."""
        try:
            positions = await self.api.fetch_positions([symbol] if symbol else None)
            return [p for p in positions if float(p.get("contracts", 0)) > 0]
        except Exception as e:
            log.error("Fetch positions failed: %s", e)
            return []
