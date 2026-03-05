import logging
import os
import sys
from datetime import datetime
# -------------------------
# ЛОГИРОВАНИЕ
# -------------------------

def setup_logger(name: str, service_dir, log_level: str ="INFO") -> logging.Logger:
    """Настройка структурированного логирования"""
    logger = logging.getLogger(name)
    log_level_dict = {"INFO": logging.INFO,
                      "WARNING": logging.WARNING,
                      "DEBUG": logging.DEBUG} 
    logger.setLevel(log_level_dict[log_level])
    if logger.handlers:
        return logger
    formatter = logging.Formatter(
        fmt='%(asctime)s | %(name)s | %(levelname)-8s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S')
    # STDOUT handler
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(log_level_dict[log_level])
    stdout_handler.setFormatter(formatter)
    logger.addHandler(stdout_handler)
    # File handler (DEV)
    if os.getenv("LOG_TO_FILE", "false").lower() == "true":
        if service_dir is None:
            raise ValueError("service_dir required when LOG_TO_FILE=true")
        log_dir = service_dir/ "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir/f"{name}_{datetime.now().strftime('%Y%m%d')}.log"
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    return logger