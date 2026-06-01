import logging
import os
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional, Union

LogLevel = Union[str, int, None]
LogTarget = Union[str, Path, None]

DEFAULT_LOG_DIR = Path("logs")
LOG_FORMAT = "%(asctime)s | %(name)s | %(levelname)-8s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
ROTATE_MAX_BYTES = 10 * 1024 * 1024
ROTATE_BACKUP_COUNT = 5


def _parse_log_level(value: LogLevel = None, default: int = logging.INFO) -> int:
    if isinstance(value, int):
        return value
    if value is None:
        value = os.getenv("LOG_LEVEL", "INFO")
    level = getattr(logging, str(value).upper(), None)
    return level if isinstance(level, int) else default


def _resolve_log_target(
    log_target: LogTarget,
    *,
    log_file: Optional[str],
    service_dir: LogTarget,
) -> tuple[Optional[str], Optional[Path]]:
    if log_file:
        return log_file, None

    if service_dir is not None:
        return None, Path(service_dir)

    if log_target is None:
        return None, None

    if isinstance(log_target, Path):
        return None, log_target

    target = str(log_target)
    if target.endswith(".log") or "/" not in target and "\\" not in target:
        return target, None

    return None, Path(target)


def setup_logger(
    name: str,
    log_target: LogTarget = None,
    *,
    service_dir: LogTarget = None,
    log_file: Optional[str] = None,
    level: LogLevel = None,
    log_level: LogLevel = None,
) -> logging.Logger:
    """
    Создаёт или возвращает настроенный логгер.

    Args:
        name: имя логгера (отображается в каждой строке лога)
        log_target: имя файла в logs/ или каталог сервиса (обратная совместимость)
        service_dir: каталог сервиса для локальных логов при LOG_TO_FILE=true
        log_file: имя файла в logs/ с ротацией
        level: уровень логирования (по умолчанию из LOG_LEVEL)
        log_level: алиас для level
    """
    resolved_level = _parse_log_level(level if level is not None else log_level)
    resolved_log_file, resolved_service_dir = _resolve_log_target(
        log_target,
        log_file=log_file,
        service_dir=service_dir,
    )

    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(resolved_level)
    logger.propagate = False

    formatter = logging.Formatter(fmt=LOG_FORMAT, datefmt=DATE_FORMAT)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(resolved_level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    if resolved_log_file:
        log_dir = DEFAULT_LOG_DIR
        log_dir.mkdir(exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / resolved_log_file,
            maxBytes=ROTATE_MAX_BYTES,
            backupCount=ROTATE_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setLevel(resolved_level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    elif resolved_service_dir is not None and os.getenv("LOG_TO_FILE", "false").lower() == "true":
        log_dir = resolved_service_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{name}_{datetime.now().strftime('%Y%m%d')}.log"
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setLevel(resolved_level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger
