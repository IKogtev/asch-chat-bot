from pathlib import Path
from typing import List, Dict
from datetime import datetime

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
        ext_allowed: set
    ):
        self.root = root_path
        self.qdrant = qdrant_service
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.service_dir = service_dir
        self.logger = setup_logger("file_storage", service_dir)
        self._sync_lock=False
        self.ignore_folders = {".git", "__pycache__", "_prepared"}
        self.allowed_ext = ext_allowed
        

    # -------------------------------------------------
    # SCAN FILESYSTEM
    # -------------------------------------------------

    def scan_files(self, kb_id:str) -> List[Dict]:
        """
        Сканирование только конкретного kb.
        """
        # вычисляем папку kb 
        kb_root = self.root/kb_id
        if not kb_root.exists():
            self.logger.warning(f"KB folder not found: {kb_root}")
            return []

        files = []
        #  собираем файлы по вычисленной kb папке
        for path in kb_root.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(kb_root)

            # если в пути есть игнорируемая папка
            if any(part in self.ignore_folders for part in rel.parts):
                self.logger.info(f"Ignored path: {rel}")
                continue

            if path.stat().st_size < 1000:
                continue
            if path.suffix.lower() not in self.allowed_ext:
                self.logger.debug(f"Skipping unsupported file: {path.name} (suffix: {path.suffix})")
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
                # current.setdefault("files", []).append({
                #     "name": parts[-1],
                #     "path": str(rel)
                # })

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
            self.logger.info(f"[SYNC BLOCKED], kb_id={kb_id}")
            return
        self._sync_lock = True
        self.logger.info("Starting filesystem sync")  
        try:
            disk_files = self.scan_files(kb_id)
            indexed_docs = self.qdrant.list_documents()
            indexed_map = {}
            # строим index_map для сопоставления
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
                #  добавление файла если нет в qdrant
                if filename not in indexed_map:
                    self.logger.info(f"NEW FILE: {filename}")
                    self._index_file(file, kb_id, collection_type)
                    continue
                # обновление файла в qdrant если есть
                stored_docs = indexed_map[filename]
                stored_doc_hash = stored_docs[0].get("doc_hash")
                current_doc_hash = file["hash"]
            
                reindex_needed = False
                # --- Проверка doc_hash ---
                if stored_doc_hash != current_doc_hash:
                    self.logger.info(f"UPDATED (doc_hash): {filename}")
                    reindex_needed = True

                else:
                    # --- ДОБАВЛЕНА проверка content_hash ---
                    # собираем существующие content_hash
                    stored_content_hashes = {
                        doc.get("content_hash")
                        for doc in stored_docs
                        if doc.get("content_hash") is not None
                    }

                    # генерируем новые чанки временно
                    loader = DocumentLoader(
                        documents_dir=file["absolute_path"].parent,
                        service_dir=self.service_dir,
                        chunk_size=self.chunk_size,
                        chunk_overlap=self.chunk_overlap
                    )
                    docs, _, _, _ = loader.prepare_docs_texts(
                        kb_id=kb_id,
                        filepath=file["absolute_path"],
                        map_true=(collection_type=="faq"),
                        user_id="filesystem",
                    )

                    new_content_hashes = {
                        d["meta"]["content_hash"]
                        for d in docs
                        if d["meta"].get("content_hash") is not None
                    }
                    if new_content_hashes - stored_content_hashes:
                        self.logger.info(f"UPDATED (content_hash): {filename}")
                        reindex_needed = True
                
                # --- Переиндексация если нужно ---
                if reindex_needed:
                    for doc in stored_docs:
                        self.qdrant.delete_document(doc["document_id"])

                    self._index_file(file, kb_id, collection_type)
        

            # удалённые файлы, удаление из qdrant если локально удалили
            for filename, docs in indexed_map.items():
                if filename not in disk_filenames:
                    self.logger.info(f"DELETED FILE: {filename}")
                    for doc in docs:
                        self.qdrant.delete_document(doc["document_id"])

            self.logger.info("Filesystem sync completed")
        except Exception as e:
            self.logger.error(f"[SYNC SERVICE] Critical sync error: {e}")
        finally:
            self._sync_lock = False
             

    # -------------------------------------------------
    # INDEX FILE
    # -------------------------------------------------

    def _index_file(self, file_info: Dict, kb_id: str, collection_type: str):
        try:
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
        except Exception as e:
            self.logger.error(f"[SYNC SERVICE] Failed to index {file_info.get('filename')}: {e}")
