# tests/test_safety.py
import pytest
from tinkoff_client import TinkoffClient, SandboxRequiredError


def test_sandbox_required():
    with pytest.raises(SandboxRequiredError):
        TinkoffClient("fake", use_sandbox=False)


def test_sandbox_default_ok():
    # Не подключаемся, только создаём объект.
    client = TinkoffClient("fake", use_sandbox=True)
    assert client.use_sandbox is True


def test_telegram_auth_rejects_unknown():
    """Проверка _authorized без реального Telegram."""
    import telegram_bot

    class FakeUser:
        def __init__(self, uid):
            self.id = uid

    class FakeUpdate:
        def __init__(self, uid):
            self.effective_user = FakeUser(uid)

    bot = telegram_bot.TelegramBot.__new__(telegram_bot.TelegramBot)
    bot.allowed_user_id = 12345
    assert bot._authorized(FakeUpdate(12345)) is True
    assert bot._authorized(FakeUpdate(99999)) is False


def test_telegram_auth_rejects_when_not_set():
    import telegram_bot

    class FakeUser:
        id = 12345

    class FakeUpdate:
        effective_user = FakeUser()

    bot = telegram_bot.TelegramBot.__new__(telegram_bot.TelegramBot)
    bot.allowed_user_id = None
    assert bot._authorized(FakeUpdate()) is False