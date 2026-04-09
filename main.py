#!/usr/bin/env python3
"""BTC/USDT Trading Bot — RSI Futures Scalping + Funding Rate Arbitrage."""

import asyncio
import signal
import sys

from config.settings import Config
from src.logger_setup import setup_logging
from src.trader import Trader


async def main():
    setup_logging()
    cfg = Config()

    if not cfg.exchange.api_key or not cfg.exchange.api_secret:
        print("ERROR: Set API_KEY and API_SECRET in .env file")
        print("For Binance testnet: https://testnet.binance.vision/")
        print("For Bybit testnet: https://testnet.bybit.com/")
        sys.exit(1)

    trader = Trader(cfg)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(trader.stop()))

    try:
        await trader.run()
    except asyncio.CancelledError:
        pass
    finally:
        await trader.stop()


if __name__ == "__main__":
    asyncio.run(main())
