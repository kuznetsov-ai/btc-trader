# BTC Trading Bot v2 — Deployment Guide

## Strategy Summary
**RSI(14) mean reversion with trend filter + funding rate arbitrage**

| Param | Value |
|-------|-------|
| Timeframe | 1h candles |
| RSI entry | RSI was < 45, now rising + green candle |
| RSI exit | RSI > 80 (if in profit) |
| Trend filter | Only trade when close > EMA(50) |
| Stop loss | 0.8% |
| Take profit | 6% |
| R:R ratio | 7.5:1 |
| Leverage | 3x (configurable) |
| Funding arb | 40% of capital, delta-neutral |

## Backtest Results (Jan 2024 - Jun 2025, after fees)

| Metric | 1x | 2x | 3x |
|--------|-----|-----|-----|
| Annual return | 8.9% | 15.0% | **21.7%** |
| Max drawdown | 1.6% | 3.2% | 4.7% |
| Profit factor | 2.22 | 2.21 | 2.20 |
| Trades | 95 | 95 | 95 |
| Win rate | 32.6% | 32.6% | 32.6% |

**Important**: Uses limit orders only (maker fees 0.04% round trip).

## Quick Start

### 1. Get API keys

**Bybit Testnet:**
1. Go to https://testnet.bybit.com/
2. Register and enable 2FA
3. API Management → Create API key
4. Permissions: Contract Trade, Wallet Read

**Binance Testnet (alternative):**
1. Go to https://testnet.binance.vision/
2. Login with GitHub
3. Generate HMAC key

### 2. Configure
```bash
cd btc-trader
cp .env.example .env
# Edit .env with your keys
```

### 3. Run
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

### 4. Monitor
```bash
tail -f logs/bot.log       # All activity
tail -f logs/trades.log    # Trades only
```

## Docker
```bash
docker compose up -d
docker compose logs -f btc-trader
```

## Projections

| Capital | Monthly (3x) | Annual (3x) |
|---------|-------------|-------------|
| $500 | $8.9 | $109 |
| $1,000 | $17.9 | $218 |
| $5,000 | $89 | $1,087 |
| $10,000 | $179 | $2,175 |

## Switching to Live

1. Update API key permissions: **Contract Trade + Wallet Read**
2. Change `.env`: `MODE=live`
3. Start with minimum capital ($500)
4. Monitor first 2 weeks actively
5. Scale up after confirming live matches backtest
