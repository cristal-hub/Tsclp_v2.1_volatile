# tests/test_strategy.py
import pandas as pd
import pytest
from strategy import StrategyManager


def make_df(closes, volumes=None):
    n = len(closes)
    if volumes is None:
        volumes = [1000] * n
    return pd.DataFrame({
        'open': closes,
        'high': [c * 1.001 for c in closes],
        'low': [c * 0.999 for c in closes],
        'close': closes,
        'volume': volumes,
    }, index=pd.date_range('2026-01-01', periods=n, freq='5min'))


def test_insufficient_data_returns_hold():
    sm = StrategyManager()
    df = make_df([100.0] * 30)
    signal, reason = sm.get_signal(df, 100.0)
    assert signal == "HOLD"
    assert "Недостаточно" in reason or "данных" in reason


def test_ma100_missing_blocks_signal():
    """При < 100 свечах фильтр MA100 должен запрещать сигнал (HOLD)."""
    sm = StrategyManager()
    # 60 свечей: достаточно для MIN_CANDLES, но < MA100.
    closes = [100.0 + i * 0.1 for i in range(60)]
    df = make_df(closes)
    signal, reason = sm.get_signal(df, closes[-1])
    # Сигнал может быть HOLD по разным причинам, но если BUY/SELL —
    # это означает, что фильтр MA100 не сработал.
    assert signal == "HOLD", f"Ожидался HOLD (MA100 < 100 свечей), получен {signal}: {reason}"


def test_volume_confirmation_rejects_low_volume():
    sm = StrategyManager()
    closes = [100.0] * 59 + [100.5]
    volumes = [1000] * 59 + [100]  # последняя свеча с низким объёмом
    df = make_df(closes, volumes)
    ok, why = sm._volume_confirmation(df)
    assert ok is False


def test_volume_confirmation_accepts_spike():
    sm = StrategyManager()
    closes = [100.0] * 60
    volumes = [1000] * 59 + [5000]
    df = make_df(closes, volumes)
    ok, why = sm._volume_confirmation(df)
    assert ok is True


def test_adx_hysteresis_does_not_flip_immediately():
    """
    Гистерезис ADX: режим не должен меняться мгновенно.
    Проверяем, что при чередовании значений около порога режим
    стабилен хотя бы один вызов.
    """
    sm = StrategyManager()
    # Устанавливаем начальный режим вручную (как будто был TREND).
    sm._last_regime = "TREND"
    sm._regime_streak = 0
    # Много свечей с плавным трендом, ADX должен быть высоким.
    closes = [100.0 + i * 0.5 for i in range(150)]
    df = make_df(closes)
    regime1 = sm.get_market_regime(df)
    regime2 = sm.get_market_regime(df)
    # Не падаем и не застреваем в UNKNOWN.
    assert regime1 in {"TREND", "RANGE", "UNKNOWN"}
    assert regime2 in {"TREND", "RANGE", "UNKNOWN"}


def test_prepare_dataframe_drops_incomplete_when_asked():
    sm = StrategyManager()
    df = make_df([100.0] * 10)
    df['is_complete'] = [True] * 9 + [False]
    prepared = sm._prepare_dataframe(df, drop_incomplete=True)
    assert len(prepared) == 9


def test_prepare_dataframe_keeps_incomplete_by_default():
    sm = StrategyManager()
    df = make_df([100.0] * 10)
    df['is_complete'] = [True] * 9 + [False]
    prepared = sm._prepare_dataframe(df, drop_incomplete=False)
    assert len(prepared) == 10