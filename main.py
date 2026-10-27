# main.py
# Tsclp_v2.1_volatile — основной цикл (ТОЛЬКО ПЕСОЧНИЦА для торговли).
# Скрининг — через боевой Read-Only токен (только чтение).
#
# ИЗМЕНЕНИЯ:
# 1. Логируется, когда свечей мало (чтобы понимать, почему нет сигналов).
# 2. Приглушены сетевые ошибки Telegram (httpx, networkloop) — только WARNING.

import asyncio
import logging

import pandas as pd
from t_tech.invest import (
    CandleInterval,
    OrderDirection,
    OrderExecutionReportStatus,
)

import config
from risk_manager import RiskManager
from strategy import StrategyManager
from tinkoff_client import TinkoffClient, UncertainStateError
from execution import ExecutionMonitor
from screener import Screener

if config.ENABLE_TELEGRAM:
    from telegram_bot import TelegramBot
else:
    TelegramBot = None

# Основной лог
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
)
# Приглушаем сетевые ошибки Telegram (кратковременные обрывы — не критичны)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram.ext._utils.networkloop").setLevel(logging.WARNING)

trading_enabled = False
top_instruments = []
last_prices = {}
telegram_bot = None
main_client = None


def start_trading():
    global trading_enabled
    trading_enabled = True
    logging.info("Торговля АКТИВИРОВАНА (новые входы разрешены).")


def stop_trading():
    global trading_enabled
    trading_enabled = False
    logging.info(
        "Торговля ОСТАНОВЛЕНА (новые входы запрещены). "
        "Позиции и SL/TP НЕ закрыты — мониторинг продолжается."
    )


def get_status():
    instruments = ", ".join(i['ticker'] for i in top_instruments) or "—"
    uncertain = "ДА" if (main_client and main_client.is_uncertain()) else "нет"
    return (
        f"🤖 Статус\n"
        f"Торговля: {'АКТИВНА' if trading_enabled else 'СТОП'}\n"
        f"Режим торговли: SANDBOX\n"
        f"Скрининг: READ-ONLY (боевой)\n"
        f"Неопределённость: {uncertain}\n"
        f"Инструменты ({len(top_instruments)}): {instruments}"
    )


async def reconcile(client: TinkoffClient, monitor: ExecutionMonitor):
    positions = await client.get_positions()
    if not positions:
        logging.info("[reconcile] Позиций нет.")
        return
    trading_figis = {i['figi'] for i in top_instruments}
    unknown = []
    for figi, qty in positions.items():
        if figi not in trading_figis:
            continue
        if not monitor.has_position(figi):
            unknown.append((figi, qty))
    if unknown:
        client.uncertain_state = True
        msg = (
            "⚠️ Позиции, о которых робот не знает: "
            + ", ".join(f"{f}({q})" for f, q in unknown)
            + ". Новые входы ЗАПРЕЩЕНЫ."
        )
        logging.error(msg)
        if telegram_bot:
            try:
                await telegram_bot.send_message(msg)
            except Exception as e:
                logging.warning(f"Telegram send error: {e}")
    else:
        logging.info("[reconcile] Все торговые позиции учтены.")


async def trading_cycle(
    client: TinkoffClient,
    rm: RiskManager,
    sm: StrategyManager,
    monitor: ExecutionMonitor,
):
    global trading_enabled

    if not top_instruments:
        return

    figi_list = [i['figi'] for i in top_instruments]
    prices = await client.get_last_prices(figi_list)
    if not prices:
        logging.warning("Нет цен — пропуск итерации")
        client.uncertain_state = True
        return
    last_prices.update(prices)

    # 1. Программный мониторинг SL/TP
    to_close = await monitor.check_all(prices)
    for figi, ticker, reason, price, qty in to_close:
        pos = monitor.get_position(figi)
        if pos is None:
            continue
        direction = (
            OrderDirection.ORDER_DIRECTION_SELL
            if pos.direction == "BUY"
            else OrderDirection.ORDER_DIRECTION_BUY
        )
        logging.warning(f"[close] {ticker} {reason} @ {price:.4f} qty={qty}")
        resp = await client.post_market_order(figi, qty, direction)
        if resp is None:
            logging.error(f"[close] Не удалось закрыть {ticker} — uncertain_state=True")
            client.uncertain_state = True
        else:
            if telegram_bot:
                try:
                    await telegram_bot.send_message(
                        f"🔒 {ticker}: закрыто по {reason} @ {price:.4f}"
                    )
                except Exception as e:
                    logging.warning(f"Telegram send error: {e}")

    # 2. Новые входы
    if not trading_enabled:
        return
    if client.is_uncertain():
        logging.warning("Неопределённое состояние — новые входы запрещены.")
        return

    positions = await client.get_positions()

    for inst in top_instruments:
        figi = inst['figi']
        price = prices.get(figi)
        if not price:
            continue

        if positions.get(figi, 0) != 0:
            continue

        candles = await client.get_historical_candles(
            figi, days=config.CANDLES_LOOKBACK_DAYS,
            interval=CandleInterval.CANDLE_INTERVAL_5_MIN,
        )
        if len(candles) < config.MIN_CANDLES_FOR_SIGNAL:
            # Логируем, чтобы было видно, что робот не "завис",
            # а отбрасывает инструмент из-за недостатка свечей.
            logging.info(
                f"{inst['ticker']}: мало свечей ({len(candles)} < "
                f"{config.MIN_CANDLES_FOR_SIGNAL}), пропуск."
            )
            continue

        df = pd.DataFrame([{
            'time': c.time,
            'open': float(c.open.units + c.open.nano / 1e9),
            'high': float(c.high.units + c.high.nano / 1e9),
            'low': float(c.low.units + c.low.nano / 1e9),
            'close': float(c.close.units + c.close.nano / 1e9),
            'volume': int(c.volume),
            'is_complete': getattr(c, 'is_complete', None),
        } for c in candles])
        df = df.set_index('time')
        df = df.sort_index()

        signal, reason = sm.get_signal(df, float(price), drop_incomplete=True)
        if signal == "HOLD":
            logging.info(f"{inst['ticker']}: HOLD ({reason})")
            continue

        if signal == "SELL" and positions.get(figi, 0) == 0:
            logging.info(f"{inst['ticker']}: SELL без позиции — шорт запрещён")
            continue

        lots = await rm.calculate_position_size(figi, float(price), df)
        if lots <= 0:
            logging.info(f"{inst['ticker']}: размер позиции 0, пропуск")
            continue

        direction = (
            OrderDirection.ORDER_DIRECTION_BUY
            if signal == "BUY"
            else OrderDirection.ORDER_DIRECTION_SELL
        )
        order = await client.post_market_order(figi, lots, direction)
        if order is None:
            logging.error(f"{inst['ticker']}: заявка не отправлена — uncertain_state=True")
            client.uncertain_state = True
            continue

        status = getattr(order, 'execution_report_status', None)
        if status == OrderExecutionReportStatus.EXECUTION_REPORT_STATUS_FILL:
            logging.info(f"{inst['ticker']}: исполнено полностью")
        elif status == OrderExecutionReportStatus.EXECUTION_REPORT_STATUS_PARTIALLYFILL:
            logging.error(f"{inst['ticker']}: ЧАСТИЧНОЕ исполнение — uncertain_state=True")
            client.uncertain_state = True
            continue
        else:
            logging.warning(f"{inst['ticker']}: ордер не исполнен ({status})")
            continue

        rm.record_trade(figi)

        sl_price, tp_price = rm.calculate_sl_tp(float(price), signal, df)
        if sl_price is None or tp_price is None:
            logging.error(f"{inst['ticker']}: SL/TP не рассчитаны — uncertain_state=True")
            client.uncertain_state = True
            continue

        monitor.register(
            figi=figi,
            ticker=inst['ticker'],
            direction=signal,
            entry_price=float(price),
            quantity=lots,
            sl_price=sl_price,
            tp_price=tp_price,
        )

        if telegram_bot:
            try:
                await telegram_bot.send_message(
                    f"🚀 <b>{inst['ticker']}</b> {signal}\n"
                    f"Цена: {price:.4f}\nЛотов: {lots}\n"
                    f"SL: {sl_price:.4f}\nTP: {tp_price:.4f}\n"
                    f"<i>{reason}</i>"
                )
            except Exception as e:
                logging.warning(f"Telegram send error: {e}")


async def main():
    global telegram_bot, main_client, top_instruments

    config.validate()

    if config.ENABLE_TELEGRAM and TelegramBot:
        telegram_bot = TelegramBot(
            config.TELEGRAM_BOT_TOKEN,
            config.TELEGRAM_CHAT_ID,
            config.TELEGRAM_ALLOWED_USER_ID,
            {
                'start_trading': start_trading,
                'stop_trading': stop_trading,
                'get_status': get_status,
            },
        )
        await telegram_bot.application.initialize()
        await telegram_bot.application.updater.start_polling()
        logging.info("Telegram-бот запущен в главном event loop.")

        try:
            await telegram_bot.send_message(
                "🤖 Tsclp_v2.1_volatile запущен (SANDBOX для торговли).\n"
                "Скрининг — через боевой Read-Only токен.\n"
                "Торговля ВЫКЛЮЧЕНА. /start — разрешить, /stop — запретить."
            )
        except Exception as e:
            logging.warning(f"Первое сообщение не ушло: {e}")

    async with TinkoffClient(config.TINKOFF_API_TOKEN, config.USE_SANDBOX) as client:
        main_client = client
        rm = RiskManager(client)
        sm = StrategyManager()
        monitor = ExecutionMonitor(client)
        screener = Screener()

        instruments = await screener.select_top_volatile()
        if not instruments:
            client.uncertain_state = True
            msg = (
                "⚠️ Скрининг не удался. Новые входы ЗАПРЕЩЕНЫ. "
                "Перезапустите робота."
            )
            logging.error(msg)
            if telegram_bot:
                try:
                    await telegram_bot.send_message(msg)
                except Exception as e:
                    logging.warning(f"Telegram send error: {e}")
            while True:
                await asyncio.sleep(60)

        top_instruments = instruments
        if telegram_bot:
            try:
                tickers = ", ".join(i['ticker'] for i in instruments)
                await telegram_bot.send_message(
                    f"📊 Скрининг: {len(instruments)} инструментов.\n{tickers}"
                )
            except Exception as e:
                logging.warning(f"Telegram send error: {e}")

        rm.reset_daily_counts()
        await reconcile(client, monitor)

        while True:
            try:
                await trading_cycle(client, rm, sm, monitor)
            except UncertainStateError as e:
                logging.error(f"Неопределённое состояние: {e}")
                client.uncertain_state = True
            except Exception as e:
                logging.exception(f"Ошибка цикла: {e}")
            await asyncio.sleep(config.TRADING_CYCLE_SECONDS)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Остановлено пользователем.")
    except Exception as e:
        logging.critical(f"Фатальная ошибка: {e}")