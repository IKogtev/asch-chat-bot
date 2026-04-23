import asyncio
import aiohttp
from utils import setup_logger
from maxapi import Bot, Dispatcher
from maxapi.types import MessageCreated, Command
import os
from bot.bootstrap import init_services
from bot.adapters.handlers_max_wrapper import register_handlers_max


# Настройка логирования
logger = setup_logger('bot', 'bot.log')

# Инициализация бота и диспетчера
# Токен можно задать через переменную окружения MAX_BOT_TOKEN
# или передать напрямую: Bot(token='ваш_токен')
max_token = os.getenv("MAX_BOT_TOKEN", "REDACTED_EXAMPLE").strip()
bot = Bot(token=max_token)
dp = Dispatcher()
user_states = {} 
def build_menu(tree: dict, path: list[str]) -> str:
    node = tree

    for p in path:
        node = node.get(p, {})

    lines = []

    # папки
    for key in node.keys():
        if key == "files":
            continue
        lines.append(f"📁 {key}")

    # файлы
    for f in node.get("files", []):
        lines.append(f"📄 {f}")

    # навигация
    if path:
        lines.append("⬅ Назад")
        lines.append("🏠 Главная")

    return "\n".join(lines)

@dp.message_created()
async def handle_message(event: MessageCreated):
    text = event.message.body.text
    user_id = event.user_id

    if not text:
        return

    # текущее состояние
    path = user_states.get(user_id, [])

    # получаем дерево
    tree = await get_kb_tree()

    # =========================
    # LOGIC
    # =========================

    # старт / домой
    if text == "/start" or text == "🏠 Главная":
        path = []

    # назад
    elif text == "⬅ Назад":
        path = path[:-1]

    # папка
    elif text.startswith("📁 "):
        folder = text.replace("📁 ", "")
        path = path + [folder]

    # файл
    elif text.startswith("📄 "):
        filename = text.replace("📄 ", "")
        await event.message.answer(f"📄 Вы выбрали файл: {filename}")
        return

    # если просто текст (не команда)
    else:
        await event.message.answer("Напиши /start или выбери пункт меню")
        return

    # сохраняем состояние
    user_states[user_id] = path

    # строим меню
    menu_text = build_menu(tree, path)

    # отправляем
    await event.message.answer(menu_text)


# =========================
# RUN
# =========================
async def main():
    await dp.start_polling(bot)
# async def main():
#     store, subscriber_store, news_store, adk, doc_handler = await init_services()

#     register_handlers_max(
#         dp=dp,
#         store=store,
#         subscriber_store=subscriber_store,
#         adk=adk,
#         doc_handler=doc_handler,
#         get_start_message=lambda: "MAX старт"
#     )    
    
#     await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())