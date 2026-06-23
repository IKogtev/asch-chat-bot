import os
import re
from pathlib import Path
from dotenv import load_dotenv
from collections import OrderedDict
# Загружаем переменные окружения ДО импорта setup_logger
load_dotenv(override=True)

from utils.doc_search_format import DOWNLOAD_RE as _DOC_DOWNLOAD_RE

class Settings():
    # версия платформы
    PLATFORM_VERSION: str = os.getenv("PLATFORM_VERSION", "0.5.1")
    # URL для доступа к API KB Manager Admin
    KB_MANAGER_URL: str = os.getenv("KB_MANAGER_URL", "http://kb-manager:5000")
    # стартовое сообщение бота
    BOT_START_MESSAGE_FILE: Path = Path(
        os.getenv("BOT_START_MESSAGE_FILE", "/app/data/settings/bot_start_message.md")
    )
    # help сообщение бота
    BOT_HELP_MESSAGE_FILE: Path = Path(
        os.getenv("BOT_HELP_MESSAGE_FILE", "/app/data/settings/bot_help_message.md")
    )
    # Пауза между попытками подключиться а API telegram
    RECONNECT_DELAY_SEC: int = int(os.getenv("RECONNECT_DELAY_SEC", 60))
    SHOW_MAX: int = int(os.getenv("SHOW_LIST_SIZE",5))
    SHOW_BY_PAGE: bool = os.getenv("SHOW_BY_PAGE", "False").lower() == "true"

    # path для сохранения загруженных пользователями файлов новостей
    UPLOAD_NEWS: Path = Path(
        os.getenv("UPLOAD_NEWS", "/app/data/upload")
    )
    PRODUCT_KITS_ROOT: Path = Path(
        os.getenv("PRODUCT_KITS_ROOT", r"kb_storage\manager\kb\1 Продукты")
    )
    PRODUCT_KITS_MAX_FILES: int = int(os.getenv("PRODUCT_KITS_MAX_FILES", 10))
    PRODUCT_KITS_MAX_FILE_SIZE_MB: int = int(os.getenv("PRODUCT_KITS_MAX_FILE_SIZE_MB", 50))
    
    # Регулярные выражения для распознавания команд
    DOWNLOAD_RE = _DOC_DOWNLOAD_RE
    SHOW_MORE_RE = re.compile(
        r'^\s*(?:ещ[её]|покажи\s+ещ[её]|дальше|ещ[её]\s+файлы)\s*$',
        re.IGNORECASE
    )
    _SHOW_ALL_BASE_RE = re.compile(
        r'^\s*(?:покажи\s+все|все\s+файлы|вс[её]|(дай )*все( файлы)*|полный\s+список|полный|весь|да|ага|угу|ок|окей|хорошо|хочу|конечно|да,*\s*давай|давай|покажи|показывай)\s*$',
        re.IGNORECASE
    )
    SHOW_ALL_RE = re.compile('|'.join(x.pattern for x in [ _SHOW_ALL_BASE_RE, SHOW_MORE_RE]), re.IGNORECASE)
    # сохраняем пути папок в сортированном по времени словаре
    CALLBACK_MAP = OrderedDict()
    MAX_CALLBACK_ENTRIES = 5000
    
    TIME_SET_WAIT = 120
    AVAILABLE_GROUPS = ("all", "manager_group", "coach_group")

    def create_directories(self):
        """Создает необходимые директории при старте, если их нет"""
        self.UPLOAD_NEWS.mkdir(parents=True, exist_ok=True)
        self.BOT_START_MESSAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
        self.BOT_HELP_MESSAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
settings = Settings()
settings.create_directories()
