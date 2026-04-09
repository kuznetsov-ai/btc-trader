"""Telegram alerts — sends important events to Alice bot. No spam."""

import logging
import aiohttp

log = logging.getLogger("bot.alerts")

BOT_TOKEN = "8490431456:AAFb4hY072QysITXFHp01T8v4lXc_OVkBWo"
CHAT_ID = "431603030"
TG_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"


async def send_alert(text: str):
    """Send alert to Telegram. Fire and forget."""
    try:
        async with aiohttp.ClientSession() as session:
            await session.post(TG_URL, json={
                "chat_id": CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_notification": False,
            })
    except Exception as e:
        log.error("Telegram alert failed: %s", e)


async def alert_trade_opened(symbol: str, price: float, size: float,
                              sl: float, tp: float, reason: str):
    await send_alert(
        f"<b>BTC Bot: LONG</b>\n"
        f"{symbol} @ ${price:,.2f}\n"
        f"Size: {size:.4f} | SL: ${sl:,.2f} | TP: ${tp:,.2f}\n"
        f"{reason}"
    )


async def alert_trade_closed(symbol: str, entry: float, exit_price: float,
                              pnl_pct: float, pnl_abs: float, reason: str):
    emoji = "+" if pnl_abs >= 0 else ""
    await send_alert(
        f"<b>BTC Bot: CLOSED</b>\n"
        f"{symbol} @ ${exit_price:,.2f}\n"
        f"PnL: {emoji}{pnl_pct:.2f}% ({emoji}${pnl_abs:.2f})\n"
        f"Reason: {reason}"
    )


async def alert_daily_summary(trades: int, wins: int, pnl_pct: float, balance: float):
    if trades == 0:
        return  # No spam on quiet days
    await send_alert(
        f"<b>BTC Bot: Daily Summary</b>\n"
        f"Trades: {trades} | Wins: {wins}\n"
        f"PnL: {pnl_pct:+.2f}% | Balance: ${balance:,.2f}"
    )


async def alert_error(error: str):
    await send_alert(f"<b>BTC Bot: ERROR</b>\n{error}")


async def alert_started(exchange: str, balance: float, sandbox: bool):
    mode = "TESTNET" if sandbox else "LIVE"
    await send_alert(
        f"<b>BTC Bot: Started ({mode})</b>\n"
        f"Exchange: {exchange}\n"
        f"Balance: ${balance:,.2f}"
    )


async def alert_stopped(summary: dict):
    await send_alert(
        f"<b>BTC Bot: Stopped</b>\n"
        f"Trades: {summary.get('total_trades', 0)} | "
        f"Win rate: {summary.get('win_rate', '0%')}\n"
        f"PnL: {summary.get('total_pnl', '$0')}"
    )
