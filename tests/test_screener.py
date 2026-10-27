# tests/test_screener.py
import pytest
from datetime import datetime, timedelta

from screener import Screener
import config


class FakeCandle:
    def __init__(self, high, low, close, volume):
        self.high = type("Q", (), {"units": int(high), "nano": int((high % 1) * 1e9)})()
        self.low = type("Q", (), {"units": int(low), "nano": int((low % 1) * 1e9)})()
        self.close = type("Q", (), {"units": int(close), "nano": int((close % 1) * 1e9)})()
        self.volume = volume
        self.time = datetime.now()


class FakeShare:
    def __init__(self, figi, ticker, name):
        self.figi = figi
        self.ticker = ticker
        self.name = name


class FakeClient:
    def __init__(self, shares, candles_by_figi):
        self.shares = shares
        self.candles_by_figi = candles_by_figi
        self.calls = 0

    async def get_all_tradable_shares(self):
        return self.shares

    async def get_historical_candles(self, figi, days, interval):
        self.calls += 1
        return self.candles_by_figi.get(figi, [])


@pytest.mark.asyncio
async def test_screener_returns_top_n(monkeypatch):
    monkeypatch.setattr(config, "API_CALL_DELAY_SECONDS", 0)
    shares = [
        FakeShare("F1", "AAA", "A"),
        FakeShare("F2", "BBB", "B"),
        FakeShare("F3", "CCC", "C"),
    ]
    # AAA — самая волатильная, CCC — наименее.
    candles = {
        "F1": [FakeCandle(110, 90, 100, 1_000_000) for _ in range(30)],
        "F2": [FakeCandle(105, 95, 100, 1_000_000) for _ in range(30)],
        "F3": [FakeCandle(101, 99, 100, 1_000_000) for _ in range(30)],
    }
    client = FakeClient(shares, candles)
    screener = Screener(client)
    result = await screener.select_top_volatile()
    assert result is not None
    assert len(result) == 3
    # Первая — AAA (самая волатильная).
    assert result[0]['ticker'] == "AAA"


@pytest.mark.asyncio
async def test_screener_filters_low_turnover(monkeypatch):
    monkeypatch.setattr(config, "API_CALL_DELAY_SECONDS", 0)
    monkeypatch.setattr(config, "MIN_DAILY_TURNOVER_RUB", 50_000_000)
    shares = [FakeShare("F1", "AAA", "A")]
    # Оборот: 1_000_000 * 100 = 100_000_000 > 50M — проходит.
    candles = {"F1": [FakeCandle(110, 90, 100, 1_000_000) for _ in range(30)]}
    screener = Screener(FakeClient(shares, candles))
    result = await screener.select_top_volatile()
    assert result is not None
    assert len(result) == 1


@pytest.mark.asyncio
async def test_screener_returns_none_on_empty(monkeypatch):
    monkeypatch.setattr(config, "API_CALL_DELAY_SECONDS", 0)
    monkeypatch.setattr(config, "API_RETRY_DELAY_SECONDS", 0)
    screener = Screener(FakeClient([], {}))
    result = await screener.select_top_volatile()
    assert result is None


@pytest.mark.asyncio
async def test_screener_cache(monkeypatch):
    monkeypatch.setattr(config, "API_CALL_DELAY_SECONDS", 0)
    shares = [FakeShare("F1", "AAA", "A")]
    candles = {"F1": [FakeCandle(110, 90, 100, 1_000_000) for _ in range(30)]}
    client = FakeClient(shares, candles)
    screener = Screener(client)
    r1 = await screener.select_top_volatile()
    calls_after_first = client.calls
    r2 = await screener.select_top_volatile()
    # Второй вызов должен взять из кэша (без новых запросов).
    assert client.calls == calls_after_first
    assert r1 == r2