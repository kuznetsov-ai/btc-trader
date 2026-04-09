"""Main trading engine — RSI scalping on futures + funding rate arbitrage."""

import asyncio
import logging
from datetime import datetime, timezone

from config.settings import Config
from src.exchange import Exchange
from src.indicators import compute_indicators
from src.strategy import generate_signal, check_exit, Regime
from src.risk_manager import RiskManager
from src.funding_arb import FundingArbitrage

log = logging.getLogger("bot.trader")

TF_SECONDS = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900,
    "30m": 1800, "1h": 3600, "4h": 14400, "1d": 86400,
}


class Trader:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.exchange = Exchange(cfg.exchange)
        self.risk = RiskManager(cfg.risk)
        self.funding = FundingArbitrage(self.exchange, cfg.funding)
        self._running = False
        self._funding_check_counter = 0

    async def run(self):
        self._running = True
        tf_sec = TF_SECONDS.get(self.cfg.trading.timeframe, 3600)
        symbol = self.cfg.trading.symbol

        log.info("=" * 60)
        log.info("BTC Trader v2 — Futures + Funding Arb")
        log.info("Exchange: %s | Sandbox: %s", self.cfg.exchange.name, self.cfg.exchange.sandbox)
        log.info("Symbol: %s | TF: %s | Leverage: %dx",
                 symbol, self.cfg.trading.timeframe, self.cfg.trading.leverage)
        log.info("Funding Arb: %s", "ON" if self.cfg.funding.enabled else "OFF")
        log.info("=" * 60)

        # Initialize exchange
        await self.exchange.load_markets()
        if not self.cfg.exchange.sandbox:
            await self.exchange.set_leverage(symbol, self.cfg.trading.leverage)
            await self.exchange.set_position_mode(hedge=False)

        balance = await self._get_balance()
        self.risk.set_initial_balance(balance)
        log.info("Initial balance: $%.2f USDT", balance)

        while self._running:
            try:
                await self._tick()
            except Exception as e:
                log.error("Tick error: %s", e, exc_info=True)

            # Wait for next candle
            now = datetime.now(timezone.utc).timestamp()
            next_candle = (int(now / tf_sec) + 1) * tf_sec
            wait = max(next_candle - now + 3, 5)
            log.debug("Next tick in %.0fs", wait)
            await asyncio.sleep(wait)

    async def stop(self):
        self._running = False
        symbol = self.cfg.trading.symbol

        # Close trading position
        if self.risk.position:
            try:
                await self.exchange.close_long(symbol, self.risk.position.amount)
                balance = await self._get_balance()
                ticker = await self.exchange.fetch_ticker(symbol)
                self.risk.close_position(ticker["last"], balance)
            except Exception as e:
                log.error("Emergency close failed: %s", e)

        # Close arb
        await self.funding.close_all(symbol)

        log.info("=" * 60)
        log.info("Bot stopped")
        log.info("Trading: %s", self.risk.summary())
        log.info("Funding Arb: %s", self.funding.summary())
        log.info("=" * 60)
        await self.exchange.close()

    async def _tick(self):
        self.risk.tick_candle()
        symbol = self.cfg.trading.symbol

        # Funding rate check every hour (futures only, skip on testnet spot)
        if not self.cfg.exchange.sandbox and self.cfg.funding.enabled:
            self._funding_check_counter += 1
            candles_per_hour = 3600 // TF_SECONDS.get(self.cfg.trading.timeframe, 3600)
            if self._funding_check_counter >= max(candles_per_hour, 1):
                self._funding_check_counter = 0
                balance = await self._get_balance()
                await self.funding.check_and_manage(symbol, balance)

        # Fetch candles and compute indicators
        df = await self.exchange.fetch_ohlcv(symbol, self.cfg.trading.timeframe,
                                              self.cfg.trading.candle_limit)
        df = compute_indicators(df, self.cfg.trading)
        balance = await self._get_balance()
        price = df.iloc[-1]["close"]

        # Check exit for open position
        if self.risk.position:
            reason = check_exit(
                df.iloc[-1],
                self.risk.position.entry_price,
                self.risk.position.stop_loss,
                self.risk.position.take_profit,
                self.risk.position.hold_candles,
                Regime(self.risk.position.regime),
            )
            if reason:
                log.info("Exit: %s", reason)
                try:
                    if self.cfg.exchange.sandbox:
                        await self.exchange.api.create_market_sell_order(symbol, self.risk.position.amount)
                    else:
                        await self.exchange.close_long(symbol, self.risk.position.amount)
                except Exception as e:
                    log.error("Close failed: %s", e)
                    return
                self.risk.close_position(price, balance)
            return

        # Check entry
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        can, msg = self.risk.can_trade(current_date=today)
        if not can:
            log.debug("Skip: %s", msg)
            return

        signal = generate_signal(df, self.cfg.trading)
        if signal is None or signal.action != "buy":
            return

        # Calculate leveraged position size
        size = self.risk.calc_position_size(balance, price, signal.stop_loss, signal.confidence)
        if size <= 0:
            return

        # Apply leverage to notional check
        notional = size * price
        if notional < 5:  # Bybit min notional
            log.debug("Notional $%.2f below minimum", notional)
            return

        # Round to Bybit step (0.001 BTC)
        size = max(round(size, 3), 0.001)

        # Execute long
        log.info("Signal: %s conf=%.0f%%", signal.reason, signal.confidence * 100)
        try:
            if self.cfg.exchange.sandbox:
                order = await self.exchange.api.create_market_buy_order(symbol, size)
            else:
                order = await self.exchange.open_long(symbol, size)
            fill_price = float(order.get("average", price))
        except Exception as e:
            log.error("Buy order failed: %s", e)
            return

        self.risk.open_position(fill_price, size, signal.stop_loss,
                                signal.take_profit, signal.regime.value)

    async def _get_balance(self) -> float:
        try:
            bal = await self.exchange.fetch_balance()
            usdt = bal["USDT"]
            # On spot, include BTC value
            if self.cfg.exchange.sandbox:
                btc_bal = float((await self.exchange.api.fetch_balance()).get("BTC", {}).get("free", 0))
                if btc_bal > 0:
                    ticker = await self.exchange.fetch_ticker(self.cfg.trading.symbol)
                    usdt += btc_bal * ticker["last"]
            return usdt
        except Exception as e:
            log.error("Balance fetch failed: %s", e)
            return self.risk._initial_balance
