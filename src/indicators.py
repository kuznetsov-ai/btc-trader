"""Technical indicators — pure numpy/pandas, no external TA library needed."""

import numpy as np
import pandas as pd
from config.settings import TradingConfig


def _ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False).mean()


def _sma(series: pd.Series, length: int) -> pd.Series:
    return series.rolling(window=length).mean()


def _rsi(series: pd.Series, length: int) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / length, min_periods=length).mean()
    avg_loss = loss.ewm(alpha=1 / length, min_periods=length).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, length: int) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(span=length, adjust=False).mean()


def _adx(high: pd.Series, low: pd.Series, close: pd.Series, length: int) -> pd.Series:
    plus_dm = high.diff().clip(lower=0)
    minus_dm = (-low.diff()).clip(lower=0)
    # Zero out when the other is larger
    cond = plus_dm > minus_dm
    plus_dm = plus_dm.where(cond, 0)
    minus_dm = minus_dm.where(~cond, 0)

    atr = _atr(high, low, close, length)
    plus_di = 100 * _ema(plus_dm, length) / atr
    minus_di = 100 * _ema(minus_dm, length) / atr

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    adx = _ema(dx, length)
    return adx


def _bbands(close: pd.Series, length: int, std: float):
    mid = _sma(close, length)
    sd = close.rolling(window=length).std()
    upper = mid + std * sd
    lower = mid - std * sd
    return lower, mid, upper


def _macd(close: pd.Series, fast: int, slow: int, signal: int):
    ema_f = _ema(close, fast)
    ema_s = _ema(close, slow)
    macd_line = ema_f - ema_s
    signal_line = _ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def compute_indicators(df: pd.DataFrame, cfg: TradingConfig) -> pd.DataFrame:
    d = df.copy()

    d["ema_fast"] = _ema(d["close"], cfg.ema_fast)
    d["ema_slow"] = _ema(d["close"], cfg.ema_slow)
    d["ema_trend"] = _ema(d["close"], cfg.ema_trend)

    bb_l, bb_m, bb_u = _bbands(d["close"], cfg.bb_length, cfg.bb_std)
    d["bb_lower"] = bb_l
    d["bb_mid"] = bb_m
    d["bb_upper"] = bb_u
    d["bb_width"] = (bb_u - bb_l) / bb_m

    d["rsi"] = _rsi(d["close"], cfg.rsi_length)
    d["rsi_fast"] = _rsi(d["close"], cfg.rsi_fast_length)

    d["adx"] = _adx(d["high"], d["low"], d["close"], cfg.adx_length)
    d["atr"] = _atr(d["high"], d["low"], d["close"], cfg.atr_length)

    macd_line, sig_line, hist = _macd(d["close"], cfg.macd_fast, cfg.macd_slow, cfg.macd_signal)
    d["macd"] = macd_line
    d["macd_signal"] = sig_line
    d["macd_hist"] = hist

    d["vol_sma"] = _sma(d["volume"], cfg.vol_sma_length)
    d["vol_ratio"] = d["volume"] / d["vol_sma"]

    return d
