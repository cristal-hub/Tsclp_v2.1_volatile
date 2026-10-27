# config.py
# Tsclp_v2.1_volatile — конфигурация.
# ВАЖНО: проект предназначен ТОЛЬКО для песочницы (sandbox).
# Торговля — только песочница. Скрининг — через боевой Read-Only токен.

import os
from dotenv import load_dotenv

load_dotenv()

# --- API и Telegram ---
TINKOFF_API_TOKEN = os.getenv("TINKOFF_API_TOKEN")
# Токен ТОЛЬКО ДЛЯ ЧТЕНИЯ (боевой контур) — для скрининга.
TINKOFF_READONLY_TOKEN = os.getenv("TINKOFF_READONLY_TOKEN")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TINKOFF_ACCOUNT_ID = os.getenv("TINKOFF_ACCOUNT_ID")
TELEGRAM_ALLOWED_USER_ID = os.getenv("TELEGRAM_ALLOWED_USER_ID")

# --- Режим ---
USE_SANDBOX = True
ENABLE_TELEGRAM = True

# --- Скрининг инструментов ---
TOP_VOLATILE_INSTRUMENTS_COUNT = 10
VOLATILITY_PERIOD_DAYS = 30
VOLATILITY_REFRESH_HOURS = 24
MIN_DAILY_TURNOVER_RUB = 10_000_000  # 10 млн руб/день

# --- Риск ---
RISK_PER_TRADE = 0.01
MIN_STOP_LOSS_PERCENT = 0.005
STOP_LOSS_PERCENT = 0.012
TAKE_PROFIT_RATIO = 2.0
ATR_STOP_MULTIPLIER = 1.5
ATR_PERIOD_INTRADAY = 20
ATR_PERIOD = 14
MAX_ATR_STOP_PERCENT = 0.03
MAX_POSITION_SHARE_PER_INSTRUMENT = 0.2
MAX_TOTAL_EXPOSURE = 0.6
MAX_TRADES_PER_INSTRUMENT_PER_DAY = 5

# --- Комиссия и проскальзывание ---
COMMISSION_MODE = "TRADER"
COMMISSION_PER_SIDE = 0.003 if COMMISSION_MODE == "INVESTOR" else 0.0005
SLIPPAGE_PERCENT = 0.0002

# --- Стратегия ---
MA_FAST_PERIOD = 9
MA_SLOW_PERIOD = 21
TREND_FILTER_MA_PERIOD = 100
BB_PERIOD = 20
BB_STD_DEV = 2
RSI_PERIOD = 14
RSI_OVERBOUGHT = 70
RSI_OVERSOLD = 30
MIN_CANDLES_FOR_SIGNAL = 60
ADX_PERIOD = 14

# --- Профиль фильтров ---
PROFILE = "TECH_CHECK"

if PROFILE == "TECH_CHECK":
    ADX_TREND_THRESHOLD = 20
    ADX_HYSTERESIS_CANDLES = 1
    VOLUME_SPIKE_FACTOR = 1.0
elif PROFILE == "CONSERVATIVE":
    ADX_TREND_THRESHOLD = 25
    ADX_HYSTERESIS_CANDLES = 2
    VOLUME_SPIKE_FACTOR = 1.5
else:
    raise RuntimeError(f"Неизвестный PROFILE: {PROFILE}")

VOLUME_CONFIRMATION_WINDOW = 20

# --- API ---
API_RETRY_COUNT = 3
API_RETRY_DELAY_SECONDS = 5
API_REQUEST_TIMEOUT_SECONDS = 10
API_CALL_DELAY_SECONDS = 0.5

# --- Тайминги ---
TRADING_CYCLE_SECONDS = 30
CANDLES_LOOKBACK_DAYS = 2

# --- Отчётность ---
STARTING_BALANCE = os.getenv("STARTING_BALANCE")


def validate():
    missing = []
    if not TINKOFF_API_TOKEN:
        missing.append("TINKOFF_API_TOKEN")
    if not TINKOFF_READONLY_TOKEN:
        missing.append("TINKOFF_READONLY_TOKEN")
    if ENABLE_TELEGRAM:
        if not TELEGRAM_BOT_TOKEN:
            missing.append("TELEGRAM_BOT_TOKEN")
        if not TELEGRAM_CHAT_ID:
            missing.append("TELEGRAM_CHAT_ID")
        if not TELEGRAM_ALLOWED_USER_ID:
            missing.append("TELEGRAM_ALLOWED_USER_ID")
    if missing:
        raise RuntimeError(
            "Не заданы обязательные переменные окружения: " + ", ".join(missing) +
            ". Скопируйте .env.example в .env и заполните."
        )
    if not USE_SANDBOX:
        raise RuntimeError(
            "USE_SANDBOX=False. Реальный торговый контур в Tsclp_v2.1_volatile "
            "не реализован. Проект предназначен только для песочницы."
        )