# screener.py
# Tsclp_v2.1_volatile — скрининг по ВСЕМУ РЫНКУ (Read-Only токен).
#
# ИСПРАВЛЕНИЕ: фильтр по real_exchange == 1 (REAL_EXCHANGE_MOEX).
# В Python SDK real_exchange — числовое значение:
#   0 = UNSPECIFIED, 1 = MOEX, 2 = RTS, 4 = OTC, 5 = DEALER.

import asyncio
import logging
from datetime import timedelta
from typing import Optional

import pandas as pd
import pandas_ta_classic as ta
from t_tech.invest import AsyncClient, CandleInterval, InstrumentStatus
from t_tech.invest.utils import now

import config

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Числовое значение REAL_EXCHANGE_MOEX в Python SDK.
REAL_EXCHANGE_MOEX = 1


class Screener:
    def __init__(self):
        self.cached_instruments = None
        self.cached_at = None

    def _cache_valid(self) -> bool:
        if not self.cached_instruments or not self.cached_at:
            return False
        age_hours = (now() - self.cached_at).total_seconds() / 3600
        return age_hours < config.VOLATILITY_REFRESH_HOURS

    async def select_top_volatile(self, force: bool = False) -> Optional[list]:
        if not force and self._cache_valid():
            logging.info(f"[screener] Кэш ({len(self.cached_instruments)} инстр.)")
            return self.cached_instruments

        for attempt in range(1, 3):
            try:
                result = await self._do_scan()
                if result:
                    self.cached_instruments = result
                    self.cached_at = now()
                    logging.info(f"[screener] Скрининг: {len(result)} инструментов.")
                    return result
                logging.warning(f"[screener] Попытка {attempt}: пусто.")
            except Exception as e:
                logging.error(f"[screener] Попытка {attempt} ошибка: {e}")
            if attempt < 2:
                await asyncio.sleep(config.API_RETRY_DELAY_SECONDS)
        logging.error("[screener] Скрининг не удался.")
        return None

    async def _do_scan(self) -> Optional[list]:
        async with AsyncClient(config.TINKOFF_READONLY_TOKEN) as client:
            logging.info("[screener] Боевой Read-Only клиент подключён (только чтение).")

            # 1. Получаем ВСЕ акции
            instruments = await client.instruments.shares(
                instrument_status=InstrumentStatus.INSTRUMENT_STATUS_ALL
            )
            all_shares = instruments.instruments
            logging.info(f"[screener] Всего акций: {len(all_shares)}")

            # 2. Фильтруем: только MOEX, RUB, доступны для покупки/продажи
            shares = []
            for s in all_shares:
                real_ex = getattr(s, 'real_exchange', 0)
                if real_ex != REAL_EXCHANGE_MOEX:
                    continue
                if (s.currency or "").lower() != "rub":
                    continue
                if not s.buy_available_flag or not s.sell_available_flag:
                    continue
                shares.append(s)

            logging.info(f"[screener] Акций MOEX (real_exchange=1, RUB, tradable): {len(shares)}")

            if not shares:
                logging.error("[screener] Пустой список акций после фильтра MOEX.")
                return None

            # 3. Для каждой — дневные свечи, ATR, фильтр ликвидности
            volatility_data = []
            for share in shares:
                try:
                    await asyncio.sleep(config.API_CALL_DELAY_SECONDS)
                    candles = []
                    async for c in client.get_all_candles(
                        figi=share.figi,
                        from_=now() - timedelta(days=config.VOLATILITY_PERIOD_DAYS),
                        interval=CandleInterval.CANDLE_INTERVAL_DAY,
                    ):
                        candles.append(c)

                    # API отдаёт 29 свечей за 30 дней (сегодняшняя не закрыта).
                    if len(candles) < config.VOLATILITY_PERIOD_DAYS - 1:
                        continue

                    df = pd.DataFrame([{
                        'high': float(c.high.units + c.high.nano / 1e9),
                        'low': float(c.low.units + c.low.nano / 1e9),
                        'close': float(c.close.units + c.close.nano / 1e9),
                        'volume': int(c.volume),
                    } for c in candles])

                    atr_series = ta.atr(
                        high=df['high'], low=df['low'], close=df['close'],
                        length=config.ATR_PERIOD,
                    )
                    if atr_series is None or atr_series.dropna().empty:
                        continue

                    atr = float(atr_series.dropna().iloc[-1])
                    last_close = float(df['close'].iloc[-1])
                    if last_close <= 0 or atr <= 0:
                        continue

                    avg_volume = float(df['volume'].tail(20).mean())
                    avg_price = float(df['close'].tail(20).mean())
                    avg_turnover = avg_volume * avg_price
                    if avg_turnover < config.MIN_DAILY_TURNOVER_RUB:
                        continue

                    normalized_atr = (atr / last_close) * 100
                    volatility_data.append({
                        'figi': share.figi,
                        'ticker': share.ticker,
                        'name': share.name,
                        'volatility': normalized_atr,
                        'avg_turnover_rub': avg_turnover,
                    })
                except Exception as e:
                    logging.warning(f"[screener] {share.ticker}: {e}")
                    continue

            if not volatility_data:
                logging.error("[screener] Нет инструментов после фильтров.")
                return None

            volatility_data.sort(key=lambda x: x['volatility'], reverse=True)
            top = volatility_data[:config.TOP_VOLATILE_INSTRUMENTS_COUNT]

            logging.info("[screener] Топ волатильных (весь рынок MOEX):")
            for i, item in enumerate(top, 1):
                logging.info(
                    f"  {i}. {item['ticker']} ({item['name']}) "
                    f"vol={item['volatility']:.2f}%"
                )
            return top