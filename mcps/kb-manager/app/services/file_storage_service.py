# kb-manager/app/services/file_storage_service.py

from pathlib import Path
from typing import List, Dict
from datetime import datetime
import os

from app.utils.utillites import hash_file
from app.utils.preprocessors.document_loader import DocumentLoader
from app.utils.logger import setup_logger


class FileStorageService:
    """
    Source of Truth = FileSystem
    
    Отвечает за:
    - сканирование файловой структуры
    - вычисление hash
    - синхронизацию с Qdrant
    - построение дерева папок
    """

    def __init__(
        self,
        root_path: Path,
        qdrant_service,
        chunk_size: int,
        chunk_overlap: int,
        service_dir: Path,
    ):
        self.root = root_path
        self.qdrant = qdrant_service
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.service_dir = service_dir
        self.logger = setup_logger("file_storage", service_dir)
        self._sync_lock=False

    # -------------------------------------------------
    # SCAN FILESYSTEM
    # -------------------------------------------------

    def scan_files(self) -> List[Dict]:
        """
        Возвращает список всех файлов на диске.
        """
        files = []

        for path in self.root.rglob("*"):
            if not path.is_file():
                continue

            files.append({
                "absolute_path": path,
                "relative_path": str(path.relative_to(self.root)),
                "filename": path.name,
                "hash": hash_file(path),
                "updated_at": datetime.fromtimestamp(
                    path.stat().st_mtime
                ).isoformat()
            })

        return files

    # -------------------------------------------------
    # BUILD FOLDER TREE (для UI / MCP)
    # -------------------------------------------------

    def build_tree(self) -> Dict:
        """
        Строит древо папок вида:
        {
            "01_Маркетинг": {
                "01_ДСЖ": ["file1.pdf"]
            }
        }
        """

        tree = {}

        for path in self.root.rglob("*"):
            rel = path.relative_to(self.root)
            parts = rel.parts

            current = tree
            for part in parts[:-1]:
                current = current.setdefault(part, {})

            if path.is_file():
                current.setdefault("files", []).append(parts[-1])

        return tree

    # -------------------------------------------------
    # SYNC LOGIC (DIFF BASED)
    # -------------------------------------------------

    def sync(self, kb_id: str, collection_type: str):
        """
        Дифф-синхронизация:
        - новые файлы → индексируем
        - изменённые → переиндексируем
        - удалённые → удаляем из Qdrant
        """
        if self._sync_lock:
            self.logger.info("Sync already running")
            return
        self._sync_lock = True
        self.logger.info("Starting filesystem sync")

        disk_files = self.scan_files()
        indexed_docs = self.qdrant.list_documents()
        indexed_map = {}

        for doc in indexed_docs:
            if doc.get("kb_id") != kb_id:
                continue

            filename = doc.get("source_name")

            if filename not in indexed_map:
                indexed_map[filename] = []

            indexed_map[filename].append(doc) 

        disk_filenames = set()

        for file in disk_files:
            filename = file["filename"]
            disk_filenames.add(filename)

            if filename not in indexed_map:
                self.logger.info(f"NEW FILE: {filename}")
                self._index_file(file, kb_id, collection_type)

            elif indexed_map[filename][0]["doc_hash"] != file["hash"]:
                self.logger.info(f"UPDATED FILE: {filename}")
                for doc in indexed_map[filename]:
                    self.qdrant.delete_document(doc["document_id"])
                self._index_file(file, kb_id, collection_type)

        # удалённые файлы
        for filename, docs in indexed_map.items():
            if filename not in disk_filenames:
                self.logger.info(f"DELETED FILE: {filename}")
                for doc in docs:
                    self.qdrant.delete_document(doc["document_id"])

        self.logger.info("Filesystem sync completed")
        self._sync_lock = False

    # -------------------------------------------------
    # INDEX FILE
    # -------------------------------------------------

    def _index_file(self, file_info: Dict, kb_id: str, collection_type: str):

        single_file_path = file_info["absolute_path"]

        loader = DocumentLoader(
            documents_dir=single_file_path.parent,
            service_dir=self.service_dir,
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap
        )

        documents, _, docs_count, points_count = loader.prepare_docs_texts(
            kb_id=kb_id,
            map_true=(collection_type == "faq"),
            index_answers=False,
            user_id="filesystem",
            filepath=str(single_file_path)
        )

        if not documents:
            return

        self.qdrant.upload_points_qdrant(
            documents,
            docs_count,
            points_count
        )