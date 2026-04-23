from bot.adapters.max_messages import MaxMessageAdapter
from bot.services.handlers import register_handlers


def register_handlers_max(dp, **kwargs):
    # создаём "фейковый" dp для регистрации
    fake_dp = type("FakeDP", (), {})()

    message_handlers = []
    callback_handlers = []

    def fake_message(*args, **kwargs_inner):
        def decorator(func):
            message_handlers.append(func)
            return func
        return decorator

    def fake_callback_query(*args, **kwargs_inner):
        def decorator(func):
            callback_handlers.append(func)
            return func
        return decorator

    fake_dp.message = fake_message
    fake_dp.callback_query = fake_callback_query  # 🔥 ВОТ ЭТО ДОБАВИТЬ

    # регистрация
    register_handlers(fake_dp, **kwargs)

    @dp.message_created()
    async def handle_all(event):
        msg = MaxMessageAdapter(event)

        # сначала обычные сообщения
        for handler in message_handlers:
            try:
                await handler(msg)
            except Exception as e:
                print(f"Message handler error: {e}")

        # callback пока игнорим (или лог)
        if callback_handlers:
            print("Callback handlers registered but not supported in MAX yet")