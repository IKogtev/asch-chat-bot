import asyncio
import aiohttp
from utils import setup_logger
from bot.services.utils import get_kb_tree, register_callback_path
from maxapi import Bot, Dispatcher
from maxapi.types import MessageCreated, Command, MessageCallback
import os
from maxapi.types.attachments.attachment import ButtonsPayload
from maxapi.types.attachments.buttons import (
    ClipboardButton,
    LinkButton,
    CallbackButton,
)
# from bot.bootstrap import init_services
# from bot.adapters.handlers_max_wrapper import register_handlers_max
from bot.services.config import Settings
from maxapi import F
import json
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder

# Настройка логирования
logger = setup_logger('bot', 'bot.log')
TITLE_START = """
👋 Привет! Я интерактивный чат-бот базы знаний компании.

📁 Выбери интересующий тебя раздел или напиши что тебя интересует сообщением.
"""

# Инициализация бота и диспетчера
# Токен можно задать через переменную окружения MAX_BOT_TOKEN
# или передать напрямую: Bot(token='ваш_токен')
max_token = os.getenv("MAX_BOT_TOKEN", "REDACTED_EXAMPLE").strip()
bot = Bot(token=max_token)
dp = Dispatcher()
user_states = {} 
def load_bot_start_message():
    """Load start message from file"""
    global TITLE_START
    try:
        if Settings.BOT_START_MESSAGE_FILE.exists():
            TITLE_START = Settings.BOT_START_MESSAGE_FILE.read_text(encoding="utf-8")
            logger.info(f"Start message loaded from file: {len(TITLE_START)} symbols")
        else:
            logger.warning(f"Start file not found using standard")
    except Exception as e:
        logger.error(f"Error loading starting message: {e}")

def get_start_message():
    """Получение стартового сообщения"""
    return TITLE_START

def build_menu(tree: dict, path: list[str]):
    node = tree

    for p in path:
        node = node.get(p, {})
    builder = InlineKeyboardBuilder()
    # папки
    for key in node.keys():
        if key == "files":
            continue
        full_path = "/".join(path + [key])
        pid = register_callback_path(full_path)
        builder.row(
            CallbackButton(
                text=f"📁 {key}",
                payload=f"d:{pid}"
            )
        )

    # файлы
    for f in node.get("files", []):
        full_path = "/".join(path + [f])
        pid = register_callback_path(full_path)
        builder.row(
            CallbackButton(
                text=f"📄 {f}",
                payload="f:{pid}"
            )
        )

    # навигация
    if path:
        parent = "/".join(path[:-1])
        pid = register_callback_path(parent)
        builder.row(
            CallbackButton(
                text=f"⬅ Назад",
                payload=f"d:{pid}"
            ),
            CallbackButton(
                text="🏠 на главную",
                payload="home"
            )
        )

    return builder.as_markup()

@dp.message_created(Command("start"))
async def start_handler(event: MessageCreated):
    """Обработчик /start"""
    user_id = event.from_user.user_id
    logger.info(f"Команда /start от user_id={user_id}")
    
    tree = await get_kb_tree()
    menu = build_menu(tree, [])
    
    await event.message.answer(
        text=get_start_message(),
        attachments=[menu]
    )

@dp.message_created(Command("help"))
async def help_cmd(event: MessageCreated) -> None:
    user_id = event.from_user.user_id
    logger.info(f"Команда /help от user_id={user_id}")
    await event.message.answer(
        "ℹ️ Я помогу найти информацию в базе знаний.\n\n"
        "Просто напиши свой вопрос, и я постараюсь найти ответ!\n\n"
        "Команды:\n"
        "/start — начать работу\n"
        "/reset — сбросить историю\n"
        "/help — эта справка"
    )

# обработчик открытия папки в меню бота
@dp.message_callback(F.callback.payload.startswith("d:"))
async def open_dir(event: MessageCallback):
    """Команда обработчик открытия папки"""
    logger.info("Мы попали в папку")
    # await event.answer()
    payload = event.callback.payload
    if not payload:
        await event.answer("Ошибка данных")
        return
    
    pid = payload.split(":", 1)[1]
    path = Settings.CALLBACK_MAP.get(pid)

    if path is None:
        await event.answer("Кнопка устарела")
        return

    Settings.CALLBACK_MAP.move_to_end(pid)
    path_list = path.split("/") if path else []
    tree = await get_kb_tree()

    menu = build_menu(tree, path_list)
    title = "📁 /".join(path_list) or get_start_message()
    await event.message.edit(
        text=title,
        attachments=[menu]
    )

# обработчик открытия файла в меню бота
# @dp.callback_query(F.data.startswith("f:"))
# async def send_file(callback: CallbackQuery):
#     """Обработчик отправки файлов через меню бота"""
#     await callback.answer()
    
#     pid = callback.data.split(":")[1]
#     path = Settings.CALLBACK_MAP.get(pid)
#     if not path:
#         await callback.answer("Файл не найден", show_alert=True)
#         return

#     Settings.CALLBACK_MAP.move_to_end(pid)
#     doc_id = await get_document_id(path)
#     if not doc_id:
#         url = f"{Settings.KB_MANAGER_URL}/api/filesystem/download/?path={quote(path)}"
#     else:
#         url = f"{Settings.KB_MANAGER_URL}/api/documents/download/{doc_id}"
#     filename = path.split("/")[-1]
#     user_id = callback.from_user.id
#     logger.info(f"Запрос на скачивание файла через меню: {filename} (doc_id={doc_id}) от user_id={user_id}")
#     await eventlogger.log_event(
#         event_type="document_download_menu",
#         user_id=str(user_id),
#         session_id=str(user_id),
#         user_name=callback.from_user.username,
#         channel="telegram",
#         payload={
#             "filename": filename,
#             "file_path": path,
#             "doc_id": doc_id,
#             "source": "menu"
#         }
#     )
#     tmp_name = None
#     async with aiohttp.ClientSession() as session:
#         async with session.get(url) as resp:
#             if resp.status != 200:
#                 await callback.answer("Ошибка загрузки файла", show_alert=True)
#                 return
#             try:
#                 tmp = tempfile.NamedTemporaryFile(delete=False)
#                 tmp_name = tmp.name
#                 tmp.write(await resp.read())
#                 tmp.close()

#                 await callback.message.answer_document(
#                     document=FSInputFile(tmp_name, filename=filename),
#                 )
#             finally:
#                 if tmp_name and os.path.exists(tmp_name):
#                     os.remove(tmp_name)


@dp.message_created()
async def handle_message(event: MessageCreated):
    text = event.message.body.text
    logger.info(f" Что храниться внутри {event}")
    user_id = event.from_user.user_id

    if not text:
        return

    # текущее состояние
    path = user_states.get(user_id, [])

    # получаем дерево
    tree = await get_kb_tree()
    # сохраняем состояние
    user_states[user_id] = path
    # строим меню
    menu_text = build_menu(tree, path)
    # отправляем
    await event.message.answer(text=get_start_message(), attachments=[menu_text])


# =========================
# RUN
# =========================
async def main():
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())