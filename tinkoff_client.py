# tinkoff_client.py
# Tsclp_v2.1_volatile — клиент T-Invest API (ТОЛЬКО ПЕСОЧНИЦА).
#
# ИСПРАВЛЕНИЯ ПО СРАВНЕНИЮ С Tsclp_v2.0:
# 1. Добавлен get_all_tradable_shares() — нужен для скрининга.
# 2. __aenter__: self.client = результат __aenter__() (объект с сервисами),
#    self._sandbox_client = сам AsyncSandboxClient (для закрытия).
# 3. __aexit__: закрываем self._sandbox_client, а не self.client.
# 4. get_active_stop_orders: безопасная проверка hasattr.

import asyncio
import logging
import uuid
from datetime import timedelta
from typing import Optional

from t_tech.invest import (
    CandleInterval,
    InstrumentIdType,
    OrderDirection,
    OrderType,
    StopOrderDirection,
    StopOrderExpirationType,
    StopOrderType,
)
from t_tech.invest.sandbox.async_client import AsyncSandboxClient
from t_tech.invest.utils import now, quotation_to_decimal, decimal_to_quotation

import config

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


class SandboxRequiredError(RuntimeError):
    pass


class UncertainStateError(RuntimeError):
    pass


class TinkoffClient:
    def __init__(self, token: str, use_sandbox: bool = True):
        if not use_sandbox:
            raise SandboxRequiredError(
                "Tsclp_v2.1_volatile работает ТОЛЬКО в песочнице. "
                "USE_SANDBOX=False запрещён."
            )
        self.token = token
        self.use_sandbox = True
        self._sandbox_client = None  # сам AsyncSandboxClient (для закрытия)
        self.client = None           # результат __aenter__ (объект с сервисами)
        self.account_id: Optional[str] = None
        self.instrument_info_cache = {}
        self.uncertain_state = False

    async def __aenter__(self):
        last_exc = None
        for attempt in range(config.API_RETRY_COUNT):
            try:
                self._sandbox_client = AsyncSandboxClient(self.token)
                self.client = await self._sandbox_client.__aenter__()
                logging.info("Подключение к T-Invest SANDBOX API успешно.")
                await self._ensure_account()
                return self
            except Exception as e:
                last_exc = e
                logging.error(
                    f"Ошибка подключения к API (попытка {attempt + 1}): {e}"
                )
                if attempt < config.API_RETRY_COUNT - 1:
                    await asyncio.sleep(config.API_RETRY_DELAY_SECONDS)
        raise RuntimeError(f"Не удалось подключиться к API: {last_exc}")

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._sandbox_client:
            try:
                await self._sandbox_client.__aexit__(exc_type, exc_val, exc_tb)
            except Exception as e:
                logging.warning(f"Ошибка при закрытии клиента: {e}")
            logging.info("Соединение с T-Invest API закрыто.")

    async def _ensure_account(self):
        if config.TINKOFF_ACCOUNT_ID:
            self.account_id = config.TINKOFF_ACCOUNT_ID
            logging.info(f"Используем account_id из .env: {self.account_id}")
            return

        accounts = await self.client.users.get_accounts()
        if accounts.accounts:
            self.account_id = accounts.accounts[0].id
            logging.info(f"Найден существующий песочный счёт: {self.account_id}")
        else:
            resp = await self.client.sandbox.open_sandbox_account()
            self.account_id = resp.account_id
            logging.info(f"Создан новый песочный счёт: {self.account_id}")
            logging.info(">>> Скопируйте этот ID в .env как TINKOFF_ACCOUNT_ID <<<")

    # ---------- Чтение ----------

    async def get_all_tradable_shares(self):
        """
        Возвращает список акций MOEX, доступных для покупки и продажи.
        Используется в screener.py.
        """
        try:
            instruments = await self.client.instruments.shares()
            result = []
            for s in instruments.instruments:
                if (s.exchange or "").upper() != "MOEX":
                    continue
                if (s.currency or "").lower() != "rub":
                    continue
                if not s.buy_available_flag or not s.sell_available_flag:
                    continue
                result.append(s)
            logging.info(f"[client] get_all_tradable_shares: {len(result)} акций")
            return result
        except Exception as e:
            logging.error(f"Ошибка при получении списка акций: {e}")
            return []

    async def find_figi_by_ticker(
        self, ticker: str, class_code: str = "TQBR"
    ) -> Optional[str]:
        try:
            resp = await self.client.instruments.get_instrument_by(
                id_type=InstrumentIdType.INSTRUMENT_ID_TYPE_TICKER,
                id=ticker,
                class_code=class_code,
            )
            return resp.instrument.figi
        except Exception as e:
            logging.error(f"Не найден тикер {ticker}: {e}")
            return None

    async def get_historical_candles(
        self,
        figi: str,
        days: int,
        interval: CandleInterval = CandleInterval.CANDLE_INTERVAL_5_MIN,
    ):
        try:
            await asyncio.sleep(config.API_CALL_DELAY_SECONDS)
            candles = []
            async for candle in self.client.get_all_candles(
                figi=figi,
                from_=now() - timedelta(days=days),
                interval=interval,
            ):
                candles.append(candle)
            return candles
        except Exception as e:
            logging.error(f"Ошибка при получении свечей для {figi}: {e}")
            return []

    async def get_last_prices(self, figi_list: list):
        try:
            await asyncio.sleep(config.API_CALL_DELAY_SECONDS)
            response = await self.client.market_data.get_last_prices(figi=figi_list)
            result = {}
            for p in response.last_prices:
                if p.price is not None:
                    result[p.figi] = float(quotation_to_decimal(p.price))
            return result
        except Exception as e:
            logging.error(f"Ошибка при получении последних цен: {e}")
            return {}

    async def get_instrument_info(self, figi: str):
        if figi in self.instrument_info_cache:
            return self.instrument_info_cache[figi]
        try:
            response = await self.client.instruments.get_instrument_by(
                id_type=InstrumentIdType.INSTRUMENT_ID_TYPE_FIGI, id=figi,
            )
            instrument = response.instrument
            inc = quotation_to_decimal(instrument.min_price_increment)
            info = {
                'lot': int(instrument.lot or 1),
                'min_price_increment': float(inc),
                'ticker': instrument.ticker,
                'name': instrument.name,
            }
            if info['min_price_increment'] <= 0:
                return None
            self.instrument_info_cache[figi] = info
            return info
        except Exception as e:
            logging.error(f"Ошибка при получении информации об инструменте {figi}: {e}")
            return None

    async def get_account_balance(self) -> float:
        try:
            portfolio = await self.client.sandbox.get_sandbox_portfolio(
                account_id=self.account_id
            )
            return float(quotation_to_decimal(portfolio.total_amount_portfolio))
        except Exception as e:
            logging.error(f"Ошибка при получении баланса счета: {e}")
            return 0.0

    async def get_positions(self):
        try:
            portfolio = await self.client.sandbox.get_sandbox_portfolio(
                account_id=self.account_id
            )
            result = {}
            for pos in portfolio.positions:
                qty = float(quotation_to_decimal(pos.quantity))
                if qty != 0:
                    result[pos.figi] = qty
            return result
        except Exception as e:
            logging.error(f"Ошибка получения позиций: {e}")
            self.uncertain_state = True
            return {}

    # ---------- Запись ----------

    async def post_market_order(
        self, figi: str, quantity: int, direction: OrderDirection
    ):
        order_id = uuid.uuid4().hex[:32]
        try:
            response = await self.client.sandbox.post_sandbox_order(
                account_id=self.account_id,
                figi=figi,
                quantity=quantity,
                direction=direction,
                order_type=OrderType.ORDER_TYPE_MARKET,
                order_id=order_id,
            )
            logging.info(
                f"Рыночная заявка: {response.order_id}, "
                f"статус={response.execution_report_status}"
            )
            return response
        except Exception as e:
            logging.error(
                f"Ошибка размещения рыночной заявки для {figi} "
                f"(order_id={order_id}): {e}"
            )
            try:
                state = await self.client.sandbox.get_sandbox_order_state(
                    account_id=self.account_id, order_id=order_id
                )
                return state
            except Exception as e2:
                logging.error(f"Не удалось выяснить состояние заявки {order_id}: {e2}")
                self.uncertain_state = True
                return None

    async def post_stop_order(
        self,
        figi: str,
        quantity: int,
        stop_price: float,
        direction: StopOrderDirection,
        stop_order_type: StopOrderType,
    ):
        try:
            info = await self.get_instrument_info(figi)
            if not info:
                return None
            inc = info['min_price_increment']
            if stop_price <= 0:
                return None

            stop_price = round(stop_price / inc) * inc
            stop_price = round(stop_price, 6)

            if stop_order_type == StopOrderType.STOP_ORDER_TYPE_TAKE_PROFIT:
                if direction == StopOrderDirection.STOP_ORDER_DIRECTION_SELL:
                    exec_price = stop_price + inc
                else:
                    exec_price = stop_price - inc
            else:
                exec_price = stop_price

            if exec_price <= 0:
                exec_price = stop_price

            price_q = decimal_to_quotation(exec_price)
            stop_q = decimal_to_quotation(stop_price)

            response = await self.client.sandbox.post_sandbox_stop_order(
                account_id=self.account_id,
                figi=figi,
                quantity=quantity,
                price=price_q,
                stop_price=stop_q,
                direction=direction,
                stop_order_type=stop_order_type,
                expiration_type=StopOrderExpirationType.STOP_ORDER_EXPIRATION_TYPE_GTC,
            )
            logging.info(
                f"Стоп-заявка {stop_order_type.name}: id={response.stop_order_id}"
            )
            return response
        except Exception as e:
            logging.error(f"Ошибка стоп-заявки для {figi}: {e}")
            return None

    async def cancel_stop_order(self, stop_order_id: str):
        if not hasattr(self.client.sandbox, "cancel_sandbox_stop_order"):
            return False
        try:
            await self.client.sandbox.cancel_sandbox_stop_order(
                account_id=self.account_id, stop_order_id=stop_order_id,
            )
            return True
        except Exception as e:
            logging.warning(f"Не удалось отменить стоп {stop_order_id}: {e}")
            return False

    async def get_active_stop_orders(self, figi: str):
        if not hasattr(self.client.sandbox, "get_sandbox_stop_orders"):
            return []
        try:
            resp = await self.client.sandbox.get_sandbox_stop_orders(
                account_id=self.account_id
            )
            active = []
            for o in resp.stop_orders:
                if o.figi != figi:
                    continue
                status = getattr(o, "status", None)
                if status is not None and "ACTIVE" not in str(status).upper():
                    continue
                active.append(o)
            return active
        except Exception as e:
            logging.error(f"Ошибка получения стоп-заявок: {e}")
            return []

    def is_uncertain(self) -> bool:
        return self.uncertain_state

    def clear_uncertain(self):
        self.uncertain_state = False