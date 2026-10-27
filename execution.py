# execution.py
# Tsclp_v2.0 — программный мониторинг SL/TP.
#
# ИЗМЕНЕНИЯ ПО СРАВНЕНИЮ С v1.0:
# Новый модуль. В v1.0 защита позиции возлагалась на две стоп-заявки брокера
# и watchdog с окном 20 сек ("OCO"). В v2.0:
# 1. После входа регистрируется "позиция под наблюдением" с entry_price, sl, tp.
# 2. В каждом цикле проверяется текущая цена. Если достигнут SL/TP —
#    закрытие рыночной заявкой.
# 3. Стоп-заявки брокера остаются как дополнительный слой (опционально),
#    но НЕ считаются гарантированным OCO.
# 4. При невозможности получить цену → uncertain_state=True,
#    новые входы запрещены (проверяется в main.py).
#
# ОГРАНИЧЕНИЯ (документированы):
# - Программный SL/TP не сработает, если процесс остановлен, связь потеряна
#   или цена "проскочила" между циклами (30 сек). Это неизбежно.
# - В таких случаях робот должен перейти в безопасное состояние:
#   uncertain_state=True, уведомление в Telegram, запрет новых входов.
# - Реальный контур не реализован. Модуль работает только в песочнице.

import logging
from dataclasses import dataclass, field
from typing import Dict, Optional

import config


@dataclass
class TrackedPosition:
    figi: str
    ticker: str
    direction: str          # "BUY" или "SELL"
    entry_price: float
    quantity: int           # лоты
    sl_price: float
    tp_price: float
    sl_order_id: Optional[str] = None
    tp_order_id: Optional[str] = None
    closed: bool = False
    close_reason: Optional[str] = None


class ExecutionMonitor:
    """
    Отслеживает открытые позиции и закрывает их рыночной заявкой
    при достижении SL/TP.

    Не является заменой стоп-заявкам брокера, а дополняет их.
    """

    def __init__(self, client):
        self.client = client
        self.positions: Dict[str, TrackedPosition] = {}

    def register(
        self,
        figi: str,
        ticker: str,
        direction: str,
        entry_price: float,
        quantity: int,
        sl_price: float,
        tp_price: float,
        sl_order_id: Optional[str] = None,
        tp_order_id: Optional[str] = None,
    ):
        self.positions[figi] = TrackedPosition(
            figi=figi,
            ticker=ticker,
            direction=direction,
            entry_price=entry_price,
            quantity=quantity,
            sl_price=sl_price,
            tp_price=tp_price,
            sl_order_id=sl_order_id,
            tp_order_id=tp_order_id,
        )
        logging.info(
            f"[execution] Зарегистрирована позиция {ticker} {direction} "
            f"entry={entry_price:.4f} sl={sl_price:.4f} tp={tp_price:.4f} "
            f"qty={quantity}"
        )

    def has_position(self, figi: str) -> bool:
        p = self.positions.get(figi)
        return p is not None and not p.closed

    def get_position(self, figi: str) -> Optional[TrackedPosition]:
        return self.positions.get(figi)

    def _close_position(self, pos: TrackedPosition, reason: str, current_price: float):
        """
        Закрывает позицию рыночной заявкой. Синхронный вызов async
        делается из main.py (await monitor.check_all(...)).
        Здесь — только подготовка решения.
        """
        logging.warning(
            f"[execution] {pos.ticker}: закрытие по {reason} "
            f"(цена {current_price:.4f}, sl={pos.sl_price:.4f}, "
            f"tp={pos.tp_price:.4f})"
        )
        pos.closed = True
        pos.close_reason = reason

    async def check_all(self, prices: Dict[str, float]) -> list:
        """
        Проверяет все открытые позиции по текущим ценам.
        Возвращает список (figi, ticker, reason, current_price) для закрытия.
        Реальное закрытие выполняет main.py, чтобы контролировать порядок.
        """
        to_close = []
        if not prices:
            # Нет цен — неопределённость.
            self.client.uncertain_state = True
            logging.error("[execution] Нет цен для проверки позиций — uncertain_state=True")
            return to_close

        for figi, pos in list(self.positions.items()):
            if pos.closed:
                continue
            price = prices.get(figi)
            if price is None or price <= 0:
                logging.warning(
                    f"[execution] {pos.ticker}: нет цены — пропуск проверки"
                )
                continue

            if pos.direction == "BUY":
                if price <= pos.sl_price:
                    self._close_position(pos, "SL", price)
                    to_close.append((figi, pos.ticker, "SL", price, pos.quantity))
                elif price >= pos.tp_price:
                    self._close_position(pos, "TP", price)
                    to_close.append((figi, pos.ticker, "TP", price, pos.quantity))
            elif pos.direction == "SELL":
                if price >= pos.sl_price:
                    self._close_position(pos, "SL", price)
                    to_close.append((figi, pos.ticker, "SL", price, pos.quantity))
                elif price <= pos.tp_price:
                    self._close_position(pos, "TP", price)
                    to_close.append((figi, pos.ticker, "TP", price, pos.quantity))

        return to_close

    def mark_closed_externally(self, figi: str, reason: str = "EXTERNAL"):
        """
        Если позиция закрыта не нами (например, стоп-заявкой брокера),
        помечаем её закрытой, чтобы не дублировать.
        """
        pos = self.positions.get(figi)
        if pos and not pos.closed:
            pos.closed = True
            pos.close_reason = reason
            logging.info(f"[execution] {pos.ticker}: закрыта внешне ({reason})")

    def active_count(self) -> int:
        return sum(1 for p in self.positions.values() if not p.closed)