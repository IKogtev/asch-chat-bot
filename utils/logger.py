import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

def setup_logger(name: str, log_file: str, level=None) -> logging.Logger:
    """
    Настройка логгера с ротацией файлов
    
    Args:
        name: имя логгера
        log_file: путь к файлу логов
        level: уровень логирования (если None, берётся из LOG_LEVEL в .env)
    """
    # Получаем уровень логирования из переменной окружения
    if level is None:
        log_level_str = os.getenv('LOG_LEVEL', 'INFO').upper()
        # Добавляем отладочный вывод
        print(f"[SETUP_LOGGER] LOG_LEVEL from env: '{log_level_str}'")
        level = getattr(logging, log_level_str, logging.INFO)
        print(f"[SETUP_LOGGER] Resolved level: {logging.getLevelName(level)}")
    
    # Создаём директорию для логов
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    
    # Полный путь к файлу
    log_path = log_dir / log_file
    
    # Создаём логгер
    logger = logging.getLogger(name)
    logger.setLevel(level)
    
    # Очищаем существующие обработчики
    logger.handlers.clear()
    
    # Формат логов
    formatter = logging.Formatter(
        '%(asctime)s | %(name)s | %(levelname)s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Ротация: макс 10 МБ, хранить 5 файлов
    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)
    logger.addHandler(file_handler)
    
    # Вывод в консоль
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(level)
    logger.addHandler(console_handler)
    
    # Логируем уровень логирования при инициализации
    logger.info(f"Logger '{name}' initialized with level: {logging.getLevelName(level)}")
    logger.debug(f"Log file: {log_path.absolute()}")
    
    return logger