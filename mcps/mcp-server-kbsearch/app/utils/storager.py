import shutil, os
import asyncio
from pathlib import Path
from typing import Optional
from enum import Enum
from utils.logger import setup_logger

#  Класс для вычисления режима обновления
class UpdateMode(str, Enum):
    """Режимы обновления данных FAQ и базы знаний KB"""
    APPEND = "append"          # Добавить к существующему
    REPLACE = "replace"        # Очистить и заменить
    MERGE = "merge"            # Умное слияние (удалить старые, добавить новые)

# класс для вычисления источника данных
class SourceType(str, Enum):
    """Типы источников для FAQ и базы знаний KB"""
    LOCAL_FOLDER = "local_folder" # папка куда попадают файлы после загрузки через upload
    S3 = "s3"
    DEFAULT = "default"
#  класс отвечающий за работу хранилища
class LocalStorage:
    """
    Адаптер для работы с источниками и localvolume.
    """

    def __init__(self,documents_dir: Path, default_path:str, service_dir: Path,
                 local_mount: Optional[Path]=None, in_docker: bool=True, supported_ext: list=['.json', '.txt', '.md']):
        # настройка логера
        self.logger = setup_logger("loader", service_dir=service_dir)
        self.local_mount = Path(local_mount) if local_mount else Path('/faq_local')
        self.in_docker = in_docker
        self.docs_dir = documents_dir
        self.faq_s3_bucket = os.getenv("S3_BUCKET")
        self.faq_s3_prefix = os.getenv("S3_PREFIX", "mcp_inputs/faq")
        self.faq_s3_endpoint = os.getenv("S3_ENDPOINT", "https://storage.yandexcloud.net")
        self.faq_s3_access_key = os.getenv("S3_ACCESS_KEY", "REDACTED_EXAMPLE")
        self.faq_s3_secret_key = os.getenv("S3_SECRET_KEY", "REDACTED_EXAMPLE")
        self.default_path = default_path
        self.supported_extensions = supported_ext 

    async def save_uploaded_file(self, file_obj, filename: str) -> str:
        """Сохранить загруженный через api файл."""
        dest = self.local_mount/filename
        dest.parent.mkdir(parents=True, exist_ok=True)
        self.logger.info(f"Сохранение загруженного файла: {dest}")
        # читаем и пишем файл чанками чтобы не забивать оперативную память
        try:
            with open(dest, "wb") as f:
                # Читаем по 1мб
                while chunk := await file_obj.read(1024*1024):
                    f.write(chunk)
            return str(dest)
        except Exception as e:
            self.logger.error(f"Ошибка при сохранении файла {filename}: {e}")
            raise e
        
    # Асинхронное копирование из локальной папки
    async def copy_from_local(self, source_path: Path, dest_path: Path, mode: UpdateMode) -> bool:
        loop=asyncio.get_running_loop()
        # Вызываем синхронный метод в отдельном потоке, чтобы не блокировать event loop
        return await loop.run_in_executor(None, self._copy_sync, source_path, dest_path, mode)
    
    # Синхронное копирование из локальной папки
    def _copy_sync(self, source_path: Path, dest_path: Path, mode: UpdateMode) -> bool:
        """
        Скопировать документы из локальной папки.    
        Args:
            source_path: Исходная папка
            dest_path: Целевая папка
            mode: Режим обновления (append/replace)
        Returns:
            True если успешно, False если ошибка
        """
        try:
            source_path = Path(source_path)
            if not source_path.exists():
                self.logger.error(f"Исходная папка не существует: {source_path}")
                return False
            self.logger.info(f"Копируем FAQ из: {source_path} - > {dest_path}")
            # если у нас режим REPLACE - очищаем целевую папку
            if mode == UpdateMode.REPLACE:
                if dest_path.exists():
                    for item in dest_path.iterdir():
                        if item.is_dir():
                            shutil.rmtree(item)
                        else:
                            item.unlink()
                    self.logger.debug(f"Очищена папка: {dest_path}")
                dest_path.mkdir(parents=True, exist_ok=True)
            copied = 0
            for file in source_path.rglob("*"):
                if file.is_file() and file.suffix.lower() in self.supported_extensions:
                    rel_path = file.relative_to(source_path)
                    dest_file = dest_path / rel_path
                    dest_file.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(file, dest_file)
                    copied += 1
            self.logger.info(f"Скопировано {copied} файлов")
            return True
        
        except Exception as e:
            self.logger.error(f"Ошибка при копировании из локальной папки: {e}")
            return False

    # асинхронная загрузка из S3 
    async def download_from_s3(self, bucket: str, prefix: str, local_path: Path, mode: UpdateMode,
                                s3_endpoint: Optional[str]=None, s3_access_key: Optional[str]=None,
                                s3_secret_key: Optional[str]=None) -> bool:
        loop = asyncio.get_running_loop()
        # Вызываем синхронный метод в отдельном потоке, чтобы не блокировать event loop
        return await loop.run_in_executor(None, self._download_s3_sync, bucket, prefix, local_path, mode, s3_endpoint, s3_access_key, s3_secret_key)
    
    # синхронная загрузка из S3
    def _download_s3_sync(self, bucket: str, prefix: str, local_path: Path, mode: UpdateMode,
                                s3_endpoint: Optional[str]=None, s3_access_key: Optional[str]=None,
                                s3_secret_key: Optional[str]=None) -> bool:
        """
        Загрузить документы из S3.
        
        Args:
            bucket: Имя S3 бакета
            prefix: Префикс пути в S3
            local_path: Локальный путь для сохранения
            mode: Режим обновления (append/replace)
            s3_endpoint: URL эндпоинта S3
            s3_access_key: Ключ доступа S3
            s3_secret_key: Секретный ключ S3
        
        Returns:
            True если успешно, False если ошибка
        """
        try:
            import boto3
            # если у нас режим REPLACE - очищаем локальную папку
            if mode == UpdateMode.REPLACE:
                # Полная очистка локальной папки целевой
                if local_path.exists():
                    for item in local_path.iterdir():
                        if item.is_dir():
                            shutil.rmtree(item)
                        else:
                            item.unlink()
                    self.logger.debug(f"Очищена папка: {local_path}")
            local_path.mkdir(parents=True, exist_ok=True)
            # Настраиваем клиент S3
            s3_client_config = {}
            if s3_endpoint:
                s3_client_config['endpoint_url'] = s3_endpoint
            if s3_access_key:
                s3_client_config['aws_access_key_id'] = s3_access_key
            if s3_secret_key:
                s3_client_config['aws_secret_access_key'] = s3_secret_key
            # подключаемся к S3 
            s3_client = boto3.client('s3', **s3_client_config)
            paginator = s3_client.get_paginator('list_objects_v2')
            pages = paginator.paginate(Bucket=bucket, Prefix=prefix)
            
            downloaded = 0
            # сохраняем только поддерживаемые файлы в хранилище
            for page in pages:
                if 'Contents' not in page:
                    continue
                for obj in page['Contents']:
                    key = obj['Key']
                    # пропускаем папки и неподдерживаемые файлы
                    if key.endswith('/'):
                        continue
                    # Проверяем расширение файла
                    file_ext = Path(key).suffix.lower()
                    if file_ext not in self.supported_extensions:
                        self.logger.debug(f"Пропущен файл (неподдерживаемое расширение): {key}")
                        continue
                    # вычисляем локальный путь
                    relative_path = key.replace(prefix, "").lstrip("/")
                    local_file = local_path / relative_path
                    local_file.parent.mkdir(parents=True, exist_ok=True)
                    # загружаем файл
                    try:
                        s3_client.download_file(bucket, key, str(local_file))
                        downloaded += 1
                        self.logger.debug(f"Загружен файл из S3: {key}")
                    except Exception as e:
                        self.logger.warning(f"Ошибка при загрузке {key}: {e}")
                        continue
            if downloaded == 0:
                self.logger.warning(f"Не найдено файлов для загрузки из S3: s3://{bucket}/{prefix}")
                return False
            self.logger.info(f"Загружено {downloaded} файлов из S3")
            return True
        
        except ImportError:
            self.logger.error("boto3 не установлен. Установите: pip install boto3")
            return False
        except Exception as e:
            self.logger.error(f"Ошибка при загрузке из S3: {e}", exc_info=True)
            return False

    def resolve_source_path(self, raw_path: str):
        """
        Приводит входной путь к корректному внутри-Docker пути.
        Если пользователь указал относительный путь — считаем что
        он лежит внутри /faq_local (volume).
        """
        p = Path(raw_path)

        # абсолютный путь, не меняем
        if p.is_absolute():
            return p
        # относительный путь -> внутри volume /faq_local
        return self.local_mount/p
    
    async def load_documents_from_source(self,
            source_type: SourceType,
            params: dict,
            mode: UpdateMode):
        """Загрузить документы FAQ из указанного источника."""
        # логика загрузки в зависимости от типа источника
        if source_type ==SourceType.LOCAL_FOLDER:
            # локальная папка
            raw = params.get("source_path")
            if not raw:
                return False
            # резолвим путь
            if self.in_docker:
                resolved = self.resolve_source_path(raw)
            else: 
                resolved = raw
            return await self.copy_from_local(
                source_path=resolved, 
                dest_path=self.docs_dir,
                mode=mode)
        # S3 источник
        elif source_type == SourceType.S3:
            # параметры S3
            s3_bucket = params.get("s3_bucket", self.faq_s3_bucket)
            s3_prefix = params.get("s3_prefix", self.faq_s3_prefix)
            s3_endpoint = params.get("s3_endpoint", self.faq_s3_endpoint)
            s3_access_key = params.get("s3_access_key", self.faq_s3_access_key)
            s3_secret_key = params.get("s3_secret_key", self.faq_s3_secret_key)
            if not s3_bucket:
                raise ValueError("Для 's3' требуется 's3_bucket' в запросе или переменная окружения KB_S3_BUCKET.")
            source_location = f"s3://{s3_bucket}/{s3_prefix}"
            self.logger.info(f"Загрузка FAQ из S3: {source_location}")
            return await self.download_from_s3(
                bucket=s3_bucket,
                prefix=s3_prefix,
                local_path=self.docs_dir,
                mode=mode,
                s3_endpoint=s3_endpoint,
                s3_access_key=s3_access_key,
                s3_secret_key=s3_secret_key)
        # Источник по умолчанию
        elif source_type == SourceType.DEFAULT:
            default_path = Path(self.default_path)
            return await self.copy_from_local(
                source_path=default_path,
                dest_path=self.docs_dir,
                mode=mode
            )
        return False