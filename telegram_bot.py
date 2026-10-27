# telegram_bot.py
# Tsclp_v2.1_volatile — Telegram-бот с авторизацией.
#
# ИСПРАВЛЕНИЕ: убран run() с run_polling() в потоке.
# Вместо этого main.py вызывает await application.initialize()
# и await application.updater.start_polling() в главном asyncio loop.
# Это официально рекомендованный способ для PTB v20+ на Python 3.14.

import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes


class TelegramBot:
    def __init__(self, token, chat_id, allowed_user_id, app_controls):
        self.application = Application.builder().token(token).build()
        self.chat_id = chat_id
        try:
            self.allowed_user_id = int(allowed_user_id) if allowed_user_id else None
        except (TypeError, ValueError):
            self.allowed_user_id = None
        if self.allowed_user_id is None:
            logging.warning(
                "TELEGRAM_ALLOWED_USER_ID не задан — команды будут отклонены."
            )
        self.app_controls = app_controls
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("stop", self.stop_command))
        self.application.add_handler(CommandHandler("status", self.status_command))

    def _authorized(self, update: Update) -> bool:
        user = update.effective_user
        if user is None:
            return False
        if self.allowed_user_id is None:
            return False
        return user.id == self.allowed_user_id

    async def _deny(self, update: Update):
        try:
            await update.message.reply_text("⛔ Нет прав.")
        except Exception:
            pass

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update):
            await self._deny(update)
            return
        self.app_controls['start_trading']()
        await update.message.reply_text(
            "✅ Новые входы разрешены.\n"
            "Позиции и SL/TP не закрываются автоматически."
        )

    async def stop_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update):
            await self._deny(update)
            return
        self.app_controls['stop_trading']()
        await update.message.reply_text(
            "🛑 Новые входы запрещены.\n"
            "⚠️ Открытые позиции и SL/TP НЕ закрыты. Мониторинг продолжается."
        )

    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._authorized(update):
            await self._deny(update)
            return
        status = self.app_controls['get_status']()
        await update.message.reply_text(status)

    async def send_message(self, text: str):
        try:
            await self.application.bot.send_message(
                chat_id=self.chat_id, text=text, parse_mode='HTML'
            )
        except Exception as e:
            logging.error(f"Telegram send error: {e}")