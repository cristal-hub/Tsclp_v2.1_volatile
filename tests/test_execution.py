# tests/test_execution.py
import pytest
from execution import ExecutionMonitor


class FakeClient:
    def __init__(self):
        self.uncertain_state = False


@pytest.mark.asyncio
async def test_sl_trigger_buy():
    client = FakeClient()
    mon = ExecutionMonitor(client)
    mon.register("FIGI", "SBER", "BUY", 100.0, 1, 99.0, 102.0)
    to_close = await mon.check_all({"FIGI": 98.5})
    assert len(to_close) == 1
    figi, ticker, reason, price, qty = to_close[0]
    assert reason == "SL"


@pytest.mark.asyncio
async def test_tp_trigger_buy():
    client = FakeClient()
    mon = ExecutionMonitor(client)
    mon.register("FIGI", "SBER", "BUY", 100.0, 1, 99.0, 102.0)
    to_close = await mon.check_all({"FIGI": 102.5})
    assert len(to_close) == 1
    assert to_close[0][2] == "TP"


@pytest.mark.asyncio
async def test_no_trigger_inside_range():
    client = FakeClient()
    mon = ExecutionMonitor(client)
    mon.register("FIGI", "SBER", "BUY", 100.0, 1, 99.0, 102.0)
    to_close = await mon.check_all({"FIGI": 100.5})
    assert len(to_close) == 0


@pytest.mark.asyncio
async def test_no_prices_sets_uncertain():
    client = FakeClient()
    mon = ExecutionMonitor(client)
    mon.register("FIGI", "SBER", "BUY", 100.0, 1, 99.0, 102.0)
    to_close = await mon.check_all({})
    assert client.uncertain_state is True
    assert len(to_close) == 0


@pytest.mark.asyncio
async def test_double_trigger_only_once():
    client = FakeClient()
    mon = ExecutionMonitor(client)
    mon.register("FIGI", "SBER", "BUY", 100.0, 1, 99.0, 102.0)
    first = await mon.check_all({"FIGI": 98.0})
    second = await mon.check_all({"FIGI": 98.0})
    assert len(first) == 1
    assert len(second) == 0  # уже закрыта