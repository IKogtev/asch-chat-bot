import json
import uuid
from pathlib import Path
from typing import List, Tuple, Dict, Optional
from llama_index.core.node_parser import SentenceSplitter
from datetime import datetime
from utils.utillites import hash_file
from utils.logger import setup_logger 
import pandas as pd
# Импорт препроцессора теперь здесь, где ему и место
from utils.preprocessors.preprocessors import FAQPreprocessor

# Изображения и прочие бинарные визуальные форматы без OCR: один чанк с заглушкой,
# чтобы sparse мог матчить по имени файла и папкам (см. bm25_document_text).
IMAGE_NO_OCR_SUFFIXES = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".svg", ".ico",
})

class DocumentLoader:
    """
    Класс отвечает ТОЛЬКО за превращение файлов/JSON в список словарей для Qdrant.
    Никакой работы с БД здесь нет.
    """
    def __init__(self, documents_dir: Path, service_dir: Path, 
                 chunk_size: int = 512, chunk_overlap: int = 50):
        self.documents_dir = documents_dir
        self.service_dir = service_dir
        self.prepared_faq_path = self.documents_dir / "_prepared/faq.normalized.json"
        self.logger = setup_logger("document_loader", service_dir)
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        # Сплиттер живет здесь, так как это часть подготовки текста
        self.splitter = SentenceSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)

    def prepare_docs_texts(self, kb_id: Optional[str]=None, map_true: bool=True,
                            index_answers: bool=False, user_id: str="robot") -> Tuple[List[Dict], Dict, int, int]:
        """
        Функция для подготовки документов.
        """
        processor = FAQPreprocessor(self.prepared_faq_path, log_dir=self.service_dir) 
        if map_true:
            # Логика препроцессора
            processor.process_directory(self.documents_dir)
            processor.save()
            
            # Вызов внутренней логики сбора (см. ниже)
            return self._gather_documents(index_answers, kb_id, user_id)
        
        # Логика для сырых файлов (else ветка из вашего кода)
        docs_texts = []
        unique_documents = set()
        splitter = SentenceSplitter(chunk_size=self.chunk_size, chunk_overlap=self.chunk_overlap)
        for path in self.documents_dir.rglob("*"):
            if not path.is_file():
                self.logger.info(f"{path} оказался не файлом")
                continue
            if "_prepared" in path.parts:
                continue
            source_type = path.suffix.lower()
            if source_type in [".csv", ".xls", ".xlsx"]:
                text = self.extract_raw_text_from_tabular(path)          
            elif source_type==".pdf":
                text = processor._extract_pdf(file_path=path)
            elif source_type==".docx":
                text = processor._extract_docx(file_path=path)
            elif source_type in IMAGE_NO_OCR_SUFFIXES:
                text = "пусто"
            elif source_type not in ['.txt', '.md']:
                self.logger.info(f"suffix {path.suffix} not in list of ")
                continue
            else:
                text = path.read_text(encoding='utf-8', errors="ignore")
            if not text.strip():
                text = "пусто"    
                    
            document_id = str(uuid.uuid4())
            doc_hash = hash_file(path)
            # логика section_path из папок
            section_path = [p.name for p in path.relative_to(self.documents_dir).parents if p.name][::-1]
            chunks = splitter.split_text(text)
            for i, chunk in enumerate(chunks):
                kb_id = kb_id if kb_id is not None else path.stem.lower()
                docs_texts.append({
                    "text": chunk,
                    "meta": {
                        "kb_id": kb_id,
                        "document_id": document_id,
                        "chunk_id": f"{document_id}#{i}",
                        "doc_hash": doc_hash,
                        "source": path.name,
                        "source_type": source_type,
                        "created_at": datetime.now().isoformat(),
                        "section_path": section_path,
                        "user_id": user_id,
                        "version": 1,
                    }
                })
        points_count = len(docs_texts)
        document_count = len(unique_documents)
        
        return docs_texts, {}, document_count, points_count

    def extract_raw_text_from_tabular(self, path: Path) -> str:
        suffix = path.suffix.lower()
        texts = []
        if suffix == ".csv":
            df = pd.read_csv(path, encoding="utf-8", dtype=str)
            texts.extend(df.fillna("").astype(str).values.flatten())
        elif suffix in [".xls", ".xlsx"]:
            xls = pd.ExcelFile(path)
            for sheet_name in xls.sheet_names:
                df = xls.parse(sheet_name, dtype=str)
                texts.append(f"Sheet: {sheet_name}")
                texts.extend(df.fillna("").astype(str).values.flatten())

        return "\n".join(t for t in texts if t.strip())

    def _gather_documents(self, index_answers: bool, kb_id, user_id) -> Tuple[List[Dict], Dict, int, int]:
        """
        Читает документы из self.documents_dir, парсит JSON/md/txt в простой список текстов
        и формирует index_map metadata.

        Возвращает tuple (documents_texts_list, index_map, doc_counter)
        """
        documents = []
        map_data = {}
        doc_counter = 0
        
        if not self.prepared_faq_path.exists():
            self.logger.error(f"Prepared FAQ file not found: {self.prepared_faq_path}")
            return [], {}, 0, 0

        payload = json.loads(self.prepared_faq_path.read_text(encoding="utf-8"))
        items = payload.get("faqs", [])
        # вычисляем новые поля для payload qdrant метаданных 
        kb_id = kb_id if kb_id is not None else "default_faq"
        user_id = user_id
        version = payload.get("version", 1)
        for item in items:
            question = item.get('question') 
            answer = item.get('answer')
            if not question or not answer:
                continue
            # формируем текст документа
            doc_id = item.get("document_id", f"doc_{str(uuid.uuid4())}")
            chunk_id = f"{doc_id}#0"
            doc_hash = item.get("hash")
            source_name = item.get("source_file") 
            category = item.get('category', "-")
            section_path = " / ".join(item.get('section_path', []))
            # индексируем ответы или нет doc_text используется для индексации итоговой 
            if index_answers:
                doc_text = (
                    f"Question:\n {question}\n\n"
                    f"Answer:\n {answer}\n\n"
                    f"context\n"
                    f"category: {category}\n"
                    f"topics: {section_path}\n"
                    f"source:{source_name}"
                )
            else:
                doc_text = (
                    f"Question:\n {question}\n"
                    f"context:\n"
                    f"category: {category}\n"
                    f"topics: {section_path}\n"
                    f"source:{source_name}"
                )
            payload = {
                "chunk_id": chunk_id,
                # "question": question,
                "answer": answer,
                "category": category,
                "section_path": section_path,
                "source": source_name,
                "created_at": datetime.now().isoformat(),
                "source_type": Path(source_name).suffix.lower(),
                "doc_hash": doc_hash,
                "kb_id": kb_id,
                "user_id": user_id,
                "version": version,
                "document_id": doc_id,
            }
            # добавляем в список документов
            documents.append({"text": doc_text,
                              "meta": payload})
            doc_counter += 1
                    
        self.logger.info(f"Prepared FAQ Loaded")
        points_count = len(documents)
        return documents, map_data, doc_counter, points_count
