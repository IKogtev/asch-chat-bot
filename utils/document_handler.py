import re
import aiohttp
from pathlib import Path
from typing import List, Optional
from .logger import setup_logger
import cgi
from urllib.parse import unquote

logger = setup_logger("document_handler", "document_handler.log")

class DocumentHandler:
    """Обработчик документов для телеграм-бота"""
    
    def __init__(self, kb_manager_url: str, kb_manager_token: Optional[str], downloads_dir: str):
        self.kb_manager_url = kb_manager_url.rstrip('/')
        self.kb_manager_token = kb_manager_token
        self.downloads_dir = Path(downloads_dir)
        self.downloads_dir.mkdir(exist_ok=True, parents=True)
        
    def extract_document_ids(self, text: str) -> List[str]:
        """Извлечь document_id из текста"""
        pattern = r'\[document_id:\s*(doc_[a-f0-9\-]+)\]'
        matches = re.findall(pattern, text)
        logger.debug(f"Найдено {len(matches)} document_id в тексте")
        return matches
    
    def remove_document_ids(self, text: str) -> str:
        """Удалить все [document_id:...] из текста"""
        pattern = r'\s*\[document_id:\s*doc_[a-f0-9\-]+\]'
        cleaned_text = re.sub(pattern, '', text).strip()
        logger.debug("Удалены document_id из текста")
        return cleaned_text
    
    async def download_document(self, document_id: str) -> Optional[Path]:
        """Скачать документ через KB Manager API"""
        url = f"{self.kb_manager_url}/api/documents/download/{document_id}"
        headers = {}
        if self.kb_manager_token:
            headers["Authorization"] = f"Bearer {self.kb_manager_token}"
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers) as response:
                    if response.status != 200:
                        logger.error(f"Ошибка загрузки документа {document_id}: HTTP {response.status}")
                        return None
                    
                    # Парсинг Content-Disposition
                    content_disposition = response.headers.get('Content-Disposition', '')
                    filename = None
                    
                    if content_disposition:
                        _, params = cgi.parse_header(content_disposition)
                        # Поддержка filename* (RFC 5987) и обычного filename
                        filename = params.get('filename*') or params.get('filename')
                        
                        # Декодирование RFC 5987: utf-8''encoded_name
                        if filename and "''" in filename:
                            filename = unquote(filename.split("''", 1)[-1])
                    
                    if not filename:
                        logger.warning(f"Не удалось извлечь имя файла для {document_id}")
                        filename = f"{document_id}.file"
                    
                    # Сохранение файла
                    file_path = self.downloads_dir / filename
                    
                    # Добавление суффикса если файл существует
                    if file_path.exists():
                        stem, suffix = file_path.stem, file_path.suffix
                        counter = 1
                        while file_path.exists():
                            file_path = self.downloads_dir / f"{stem}_{counter}{suffix}"
                            counter += 1
                    
                    with open(file_path, 'wb') as f:
                        f.write(await response.read())
                    
                    logger.info(f"Документ {document_id} скачан как: {filename}")
                    return file_path
                    
        except Exception as e:
            logger.error(f"Ошибка при скачивании документа {document_id}: {e}", exc_info=True)
            return None
            
    async def download_documents(self, document_ids: List[str]) -> List[Path]:
        """
        Скачать несколько документов
        
        Returns:
            Список путей к успешно скачанным файлам
        """
        downloaded = []
        for doc_id in document_ids:
            file_path = await self.download_document(doc_id)
            if file_path:
                downloaded.append(file_path)
        
        logger.info(f"Скачано {len(downloaded)} из {len(document_ids)} документов")
        return downloaded
    
    def cleanup_file(self, file_path: Path) -> None:
        """Удалить файл после отправки"""
        try:
            if file_path.exists():
                file_path.unlink()
                logger.debug(f"Удален файл: {file_path}")
        except Exception as e:
            logger.error(f"Ошибка при удалении файла {file_path}: {e}")
    
    def cleanup_files(self, file_paths: List[Path]) -> None:
        """Удалить несколько файлов"""
        for file_path in file_paths:
            self.cleanup_file(file_path)
        logger.info(f"Очищено {len(file_paths)} файлов")