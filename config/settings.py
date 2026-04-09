import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass
class ExchangeConfig:
    name: str = os.getenv("EXCHANGE", "bybit")
    api_key: str = os.getenv("API_KEY", "")
    api_secret: str = os.getenv("API_SECRET", "")
    sandbox: bool = os.getenv("MODE", "paper") == "paper"


@dataclass
class TradingConfig:
    symbol: str = os.getenv("SYMBOL", "BTC/USDT")
    timeframe: str = os.getenv("TIMEFRAME", "1h")
    candle_limit: int = 200
    leverage: int = int(os.getenv("LEVERAGE", "3"))

    # Optimized via grid search Jan 2024 - Jun 2025 (with maker fees + trend filter)
    ema_fast: int = 9
    ema_slow: int = 21
    ema_trend: int = 50
    bb_length: int = 20
    bb_std: float = 2.0
    rsi_length: int = 14        # RSI(14) — best PF with trend filter
    rsi_fast_length: int = 7
    adx_length: int = 14
    atr_length: int = 14
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    vol_sma_length: int = 20

    rsi_entry: float = 45.0     # Buy when RSI recovers from below 45
    rsi_exit: float = 80.0      # Let winners run until RSI 80
    sl_pct: float = 0.008       # 0.8% SL (tight, cut losers)
    tp_pct: float = 0.06        # 6% TP (wide, let winners run)
    trend_filter: bool = True   # Only trade when close > EMA50

    adx_trending: float = 25.0
    adx_ranging: float = 20.0
    ema_divergence_pct: float = 0.001


@dataclass
class FundingConfig:
    enabled: bool = os.getenv("FUNDING_ARB", "true").lower() == "true"
    check_interval_hours: float = 1.0
    min_rate_threshold: float = 0.0005
    position_pct: float = 0.4


@dataclass
class RiskConfig:
    risk_per_trade: float = float(os.getenv("RISK_PER_TRADE", "0.02"))
    max_daily_loss: float = float(os.getenv("MAX_DAILY_LOSS", "0.04"))
    max_position_pct: float = float(os.getenv("MAX_POSITION_PCT", "0.20"))
    max_consecutive_losses: int = 3
    cooldown_candles: int = 4
    max_open_positions: int = 1


@dataclass
class Config:
    exchange: ExchangeConfig = field(default_factory=ExchangeConfig)
    trading: TradingConfig = field(default_factory=TradingConfig)
    funding: FundingConfig = field(default_factory=FundingConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
