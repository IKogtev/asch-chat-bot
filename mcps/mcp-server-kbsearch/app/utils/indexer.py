import os
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional, List

#  LlamaIndex
from llama_index.core import VectorStoreIndex, Settings
from llama_index.core.node_parser import SentenceSplitter
from llama_index.vector_stores.qdrant import QdrantVectorStore

# QDRANT: 
import qdrant_client
from qdrant_client import models 
from qdrant_client.http.models import (
    Filter, FieldCondition, MatchValue, # фильтры
    VectorParams, Distance, OptimizersConfigDiff, 
    PointStruct
    )

# утилиты
from utils.utillites import RemoteEmbedding, chunk_id_to_uuid, meta_id_for_collection
from utils.logger import setup_logger
from dataclasses import dataclass, field

@dataclass
class IndexerConfig:
    """Единый источник правды для конфигурации Indexer"""
    # Пути
    service_dir: Path
    documents_dir: Path
    
    # Embedding API
    embed_api_url: str = field(default_factory=lambda: os.getenv("OPENROUTER_API_URL", "YOUR-SECRET-API"))
    embed_api_key: str = field(default_factory=lambda: os.getenv("OPENROUTER_API_KEY", "YOUR-SECRET-KEY"))
    embed_model_name: str = field(default_factory=lambda: os.getenv("EMBEDDING_MODEL", "YOUR-SECRET-MODEL"))
    
    # Chunking
    chunk_size: int = 512
    chunk_overlap: int = 50
    similarity_top_k: int = field(default_factory=lambda: int(os.getenv("FAQ_SIMILARITY_TOP_K", 5)))
    similarity_cutoff: float = field(default_factory=lambda: float(os.getenv("FAQ_SIMILARITY_CUTOFF", 0.0)))
    
    # Qdrant
    use_qdrant: bool = True
    qdrant_host: str = "localhost"
    qdrant_port: int = 6333
    qdrant_collection: str = "faq_collection"
    qdrant_alias: Optional[str] = None
    qdrant_timeout: int = 10
    distance_metric: str = "COSINE"

    #  meta
    version: str="1.0"

    
    def validate(self):
        if not self.embed_api_key or not self.embed_api_url:
             raise ValueError("Embedding API configuration is missing")

class Indexer:
    """
    Класс фасад для работы с индексом.
    Инкапсилирует логику создания, сохранения, загрузки и очистки индекса.
    """
    def __init__(self, config: IndexerConfig):
        self.cfg = config
        self.cfg.validate()
        # настройка логера
        self.logger = setup_logger("indexer", service_dir=self.cfg.service_dir)
        # Инициализация модели эмбеддингов (локально для инстанса, не глобально!)
        Settings.embed_model = RemoteEmbedding(
                api_url=str(self.cfg.embed_api_url),
                api_key=str(self.cfg.embed_api_key),
                model_name=str(self.cfg.embed_model_name),
            )
        self.embed_model = RemoteEmbedding(
            api_url=str(self.cfg.embed_api_url),
            api_key=str(self.cfg.embed_api_key),
            model_name=str(self.cfg.embed_model_name),
        )
        
        self.node_parser = SentenceSplitter(
            chunk_size=self.cfg.chunk_size,
            chunk_overlap=self.cfg.chunk_overlap
        )

        # Qdrant Client (Lazy loading pattern)
        self._qdrant_client_instance = None
        
        # State
        self.index = None
        self.collection_meta_type = "collection_meta"

    def _resolve_embedding_dim(self) -> int:
        """
        Определяет размер вектора из embedding модели.
        """
        if hasattr(self, "_embedding_dim"):
            return self._embedding_dim
        try:
            test_vec = self.embed_model.get_text_embedding("ping")
            self._embedding_dim = len(test_vec)
            self.logger.info(f"Embedding vector size resolved: {self._embedding_dim}")
            return self._embedding_dim
        except Exception as e:
            self.logger.error(f"Failed to resolve embedding dim: {e}")
            raise

    def _get_qdrant_client(self):
        """Получаем клиет qdrant"""
        # получаем клиент один раз и записываем, чтобы не создавать каждый раз новый
        if self._qdrant_client_instance:
            return self._qdrant_client_instance
            
        self._qdrant_client_instance = qdrant_client.QdrantClient(
            host=self.cfg.qdrant_host,
            port=self.cfg.qdrant_port,
            timeout=self.cfg.qdrant_timeout,
            prefer_grpc=True
        )
        return self._qdrant_client_instance
    
    def ensure_qdrant_collection(self, force_recreate: bool=False, check_collection: Optional[str]=None):
        """
        Проверяет существование коллекции и валидирует её. 
        Если коллекции нет - и force_recreate True создаёт 
        """
        if not self.cfg.use_qdrant:
            self.logger.info("Qdrant не используется")
            return
        client = self._get_qdrant_client()
        vector_size = self._resolve_embedding_dim()
        distance = getattr(Distance, self.cfg.distance_metric, Distance.COSINE)
        col_name = check_collection or self.cfg.qdrant_collection

        if client.collection_exists(col_name):
            # eсли коллекция существует
            self.logger.info(f"Qdrant collection '{col_name}' exists, validating...")
            info = client.get_collection(col_name)
            existing_size = info.config.params.vectors.size
            existing_distance = info.config.params.vectors.distance
            # проверяем что размер вектора и дистанция которую использует коллекция одинакова с той,
            #  которую мы пытались создать
            if existing_size != vector_size or existing_distance != distance:
                # если не равен то выдаем предупреждение, удаляем старую коллекцию 
                msg = (f"Incompatible Qdrant collection config: "
                    f"size={existing_size}/{vector_size}, "
                    f"distance={existing_distance}/{distance}")
                if force_recreate:
                    self.logger.warning(f"{msg}. RECREATING collection (DATA LOSS)!")
                    client.delete_collection(col_name)
                else:
                    self.logger.error(f"{msg}. Aborting. Use force_recreate=True to overwrite")
                    raise ValueError(msg)
            else:
                self.logger.info(f"Collection '{col_name}' exists and matches config.")
                return        
        self.logger.info(f"Creating Qdrant collection '{col_name}'")
        # создание коллекции
        self.create_collection(col_name)
        if self.cfg.qdrant_alias:
            self.create_alias(col_name, self.cfg.qdrant_alias)
        self.logger.info(
            f"Qdrant collection '{col_name}' ensured successful"
            f"(size={vector_size}, distance={distance})")
        
    def create_alias(self, collection_name: str, alias_name: str):
        """функция для создания alias"""
        client = self._get_qdrant_client()
        client.update_collection_aliases(
            change_aliases_operations=[
                models.CreateAliasOperation(
                    create_alias=models.CreateAlias(
                        collection_name=collection_name, alias_name=alias_name
                    )
                )
            ]
        )  

    def _build_qdrant_payload(self, item: dict) -> dict:
        """
        Универсальный payload для qdrant.
        Гарантирует:
        - text (обязательный)
        - chunk_id (обязательный)
        - всё остальное — опционально
        """
        payload = {}

        # Обязательный для llama_index по нему и происходит поиск
        payload["text"] = item["text"]
        
        meta = item.get("meta", {})
        if "chunk_id" in meta:
            # Logical id
            payload["chunk_id"] = meta["chunk_id"]

        # перебор мета информации
        for key, value in meta.items():
            if key in ("chunk_id",):
                continue
            if value is None:
                continue
            payload[key] = value

        return payload

    def upsert_docs(self, docs_texts, collection_name, batch_size=50):
        """функция для вставки документов в qdrant с батчингом эмбеддингов."""
        client = self._get_qdrant_client()
        total = len(docs_texts)
        self.logger.info(f"Начинаем upsert {total} чанков (batch_size={batch_size})...")
        # разбиваем на пакеты
        for i in range(0, total, batch_size):
            batch = docs_texts[i: i+batch_size]
            # извлекаем тексты 
            texts_batch = [item['text'] for item in batch]
            # генерируем эмбеддинги пачкой один запрос к API вместо 50
            embeddings_batch = self.embed_model.get_text_embedding_batch(texts_batch)
            points = []
            for j, item in enumerate(batch):
                meta = item.get("meta", {})
                chunk_id = meta.get("chunk_id")
                if not chunk_id:
                    raise ValueError("chunk_id is required for qdrant point")
                # генерация UUID
                point_id = chunk_id_to_uuid(chunk_id)
                payload = self._build_qdrant_payload(item)
                points.append(
                    PointStruct(
                        id=point_id,
                        vector=embeddings_batch[j], # берем вектор из пачки пакетов 
                        payload=payload, 
                    )
                )
            # вставляем подготовленные точки в qdrant 
            client.upsert(collection_name=collection_name, points=points)
        self.logger.info("Upsert complete")

    def create_and_persist_index(self, docs_texts: List[Dict], doc_counter: int, points_count: int, target_collection: Optional[str]=None) -> bool:
        """
        метод для создания индекса и сохранения его в
        return True - если успешно.
        Аргумент target_collection нужен для создание Blue/Green коллекции
        """
        try:
            if not docs_texts:
                self.logger.warning(f"Нет документов для индексации")
                return False
            #  либо то что передали, либо дефолтная из конфига
            actual_collection = target_collection or self.cfg.qdrant_collection
            if self.cfg.use_qdrant:
                self.ensure_qdrant_collection(check_collection=actual_collection)
                self.logger.info(f"Используем Qdrant: {self.cfg.qdrant_host}:{self.cfg.qdrant_port}, collection={actual_collection}")
                client = self._get_qdrant_client()
                self.upsert_docs(docs_texts, actual_collection)
                vector_store = QdrantVectorStore(client=client, collection_name=actual_collection)
                self.index = VectorStoreIndex.from_vector_store(vector_store, embed_model=self.embed_model)
                self.logger.info("Векторы успешно загружены в Qdrant")
            else:
                raise
            # строим retriever
            self.retriever = self.index.as_retriever(similarity_top_k=self.cfg.similarity_top_k, similarity_cutoff=self.cfg.similarity_cutoff)
            # Записываем файл с методанными
            self.metadata = self.load_metadata()
            self.metadata.update({
                             'last_updated': datetime.now().isoformat(),
                             'documents_count': doc_counter,
                             'index_status': 'initialized',
                             'storage_type': 'qdrant' if self.cfg.use_qdrant else 'local_folder',
                             'source': str(self.cfg.documents_dir),
                            "__type__":self.collection_meta_type})
            if points_count is not None:
                self.metadata["points_count"] = points_count
            else:
                self.metadata.pop("points_count", None)
            if self.cfg.use_qdrant:
                # добавляем в мета информацию данные qdrant
                self.metadata["qdrant"] = {
                            "collection": actual_collection,
                            "alias": self.cfg.qdrant_alias,
                            "distance": self.cfg.distance_metric,
                            "vector_size": self._resolve_embedding_dim(),
                        }
            # Добавляем в мета информацию данные эмбединг
            self.metadata["embedding"] = {
                "model": self.cfg.embed_model_name,
                "chunk_size": self.cfg.chunk_size,
                "chunk_overlap": self.cfg.chunk_overlap
            }
            self.save_metadata(actual_collection)
            self.logger.info('Создание индекса завершено')
            return True
        except Exception as e:
            self.logger.exception(f"Ошибка при создании индекса: {e}")
            return False

    def clear_index(self, collection_name: Optional[str]| None=None) -> dict:
        """
        Очистить индекс и метаданные (потокобезопасно).
        - None -> активная коллекция
        - collection_name -> указанная коллекция
        Returns:
            dict with parameters:
            result = {
                "success": False,
                "cleared_collection": None, 
                "is_alias": False,
                "error": None
            }
        """
        result = {
            "success": False,
            "cleared_collection": None, 
            "is_alias": False,
            "error": None
        }
        try:
            self.logger.info("Очищаем FAQ индекс...")
            target = collection_name if collection_name else self.cfg.qdrant_alias
            is_active_alias = (target==self.cfg.qdrant_alias)
            if self.cfg.use_qdrant:
                client = self._get_qdrant_client()
                if is_active_alias:        
                    alias =self.alias_exists(target)
                    target = self.get_active_collection()
                    if not alias:
                        self.logger.warning(f"Alias '{target}' not found. Nothing to clear.")
                        return result
                elif not client.collection_exists(target):
                        self.logger.warning(f"Collection '{target}' not found. Nothing to clear.")
                        return result
                
                client.delete(
                    collection_name=target,
                    points_selector=Filter(
                        must_not=[
                            FieldCondition(
                                key="__type__",
                                match=MatchValue(value=self.collection_meta_type)
                            )
                        ]
                    )
                )
                self._init_collection_meta(target)
                result['cleared_collection'] = target
                result['is_alias'] = is_active_alias
                self.logger.info(f"Коллекция Qdrant '{target}' очищена")
            if is_active_alias:
                # Сбрасываем метаданные
                self.index = None
                self.retriever = None
                # обновляем метаданные
                self.metadata = self._get_default_metadata()
                self.metadata['index_status'] = 'cleared'
                self.save_metadata()
            result['success'] = True
            return result
        except Exception as e:
            self.logger.error(f"Ошибка при очистке FAQ индекса: {e}")
            return result
        
    def load_metadata(self) -> Dict:
        """Загрузить метаданные FAQ или KB из файла или qdrant."""
        default_meta = self._get_default_metadata()
        if self.cfg.use_qdrant:
            try:
                client = self._get_qdrant_client()
                target = self.cfg.qdrant_alias
                meta_id = meta_id_for_collection(target)
                res = client.retrieve(target, ids=[meta_id])
                if res and res[0].payload:
                    return res[0].payload
                else:
                    self.logger.info("Metadata point not found in Qdrant. Using defaults.")
                    return default_meta
            except Exception as e:
                self.logger.warning(f"Failed to load metadata from Qdrant: {e}. Using defaults")
                return default_meta

    def _get_default_metadata(self):
        """Функция для получения пустой структуры метаданных"""
        return {
            "version": self.cfg.version,
            "created_at": datetime.now().isoformat(),
            "last_updated": None,
            "documents_count": 0,
            "llm_using": self.cfg.embed_model_name,
            "index_status": "not_initialized",
            'storage_type': 'qdrant' if self.cfg.use_qdrant else 'local_folder',
            "source": None,
            "__type__": self.collection_meta_type
        }

    def get_active_metadata(self) -> Dict:
        """
        Возвращает metadata из коллекции, на которую указывает активный алиас.
        Если алиаса нет или метаданных нет — возвращает пустой словарь.
        """
        if not self.cfg.use_qdrant:
            return self.metadata

        client = self._get_qdrant_client()
        target_alias = self.cfg.qdrant_alias
        try:
            aliases = client.get_aliases()
            aliases_exists = any(a.alias_name==target_alias for a in aliases.aliases)
            if not aliases_exists:
                self.logger.info(f"Alias '{target_alias}' not found yet.")
                return {}
            scroll = client.scroll(
                collection_name=target_alias,
                scroll_filter=Filter(
                    must=[
                        FieldCondition(
                            key="__type__",
                            match=MatchValue(value=self.collection_meta_type)
                        )
                    ]
                ),
                limit=1,
                with_payload=True, 
                with_vectors=False
            )
            points, _ = scroll
            if points:
                return points[0].payload or {}
            self.logger.warning(f"Alias '{target_alias}' exists, but metadata point is missing.")
            return {}
        except Exception as e:
            self.logger.error(f"Failed to load active metadata: {e}", exc_info=True)
            return {}

    def save_metadata(self, collection: Optional[str]=None) -> None:
        """Сохранить метаданные в файл"""
        if not self.cfg.use_qdrant:
            return
        try:
            qdrant_collection = collection or self.cfg.qdrant_collection
            self._update_collection_meta(qdrant_collection, **self.metadata)
            self.logger.debug("Metadata saved to Qdrant.")
        except Exception as e:
            self.logger.error(f"Ошибка при сохранении метаданных: {e}")

    def _update_collection_meta(self, collection_name: str, **updates):
        """Обновление метаинформации"""
        client = self._get_qdrant_client()
        meta_id = meta_id_for_collection(collection_name)
        points = client.retrieve(collection_name, [meta_id])
        if points:
            payload = points[0].payload or {}    
        else:
            payload={}
        payload.update(updates)
        payload["last_updated"] = datetime.now().isoformat()
        client.upsert(
            collection_name=collection_name,
            points=[
                PointStruct(
                    id=meta_id,
                    vector=[0.0] * self._embedding_dim,
                    payload=payload
                )
            ]
        )

    def collection_delete(self,collection_name: str) -> bool:
        """Удалить коллекцию Qdrant."""
        if not self.cfg.use_qdrant:
            self.logger.warning("Qdrant не используется, удаление коллекции пропущено")
            return False
        try:
            client = self._get_qdrant_client()
            if not client.collection_exists(collection_name):
                self.logger.info(f"Коллекция Qdrant '{collection_name}' не существует, удаление пропущено")
                return False
            client.delete_collection(collection_name)
            self.logger.info(f"Коллекция Qdrant '{collection_name}' удалена")
            return True
        except Exception as e:
            self.logger.error(f"Ошибка при удалении коллекции Qdrant: {e}")
            return False
    
    def filter_documents(self, payload)->Dict:
        """
        Административный поиск (фильтрация) документов в Qdrant (НЕ семантический).
        Для администрирования отладки
        """
        if not self.cfg.use_qdrant:
            self.logger.warning("Qdrant не используется, поиск документов пропущено")
            return []
        if not self.index:
            self.logger.warning("Индекс не загружен, поиск документов пропущено")
            return []
        category = payload.get("category")
        kb_id = payload.get("kb_id")
        limit = int(payload.get("limit", 10))
        offset = payload.get("offset", None) # добавление пагинации
        conditions = []
        must_not_conditions =FieldCondition(
                        key="__type__",
                        match=MatchValue(value=self.collection_meta_type),
                    )
        if category:
            conditions.append(
                FieldCondition(
                    key="category",
                    match=MatchValue(value=category)
                )
            )
        if kb_id:
            conditions.append(
                FieldCondition(
                    key="kb_id",
                    match=MatchValue(value=kb_id)
                )
            )
        q_filter = Filter(must=conditions, must_not=must_not_conditions) if conditions else None
        try:
            client = self._get_qdrant_client()
            #  scroll листает базу
            points, next_offset = client.scroll(
                collection_name=self.cfg.qdrant_alias,
                scroll_filter=q_filter,
                limit=limit,
                offset=offset,
                with_payload=True,
                with_vectors=False
            )
            results = []
            for p in points:
                results.append({
                    "id": p.id,
                    "payload": p.payload,
                    "score": None # в scroll нет score
                })
            return {"items": results, "next_offset": next_offset}
        except Exception as e:
            self.logger.error(f"Error filtering documents: {e}")
            return {}
    
    def get_active_collection(self) -> str|None:
        """Получить активную коллекцию"""
        client = self._get_qdrant_client()
        aliases = client.get_aliases()
        for a in aliases.aliases:
            if a.alias_name == self.cfg.qdrant_alias:
                return a.collection_name
        return None

    def create_collection(self, collection_name) -> bool:
        """Создать коллекцию Qdrant согласно конфигурации."""
        if not self.cfg.use_qdrant:
            self.logger.warning("Qdrant не используется, создание коллекции пропущено")
            return False
        client = self._get_qdrant_client()
        if client.collection_exists(collection_name):
            self.logger.info(f"Коллекция Qdrant '{collection_name}' уже существует, пропускаем создание")
            return False
        client.create_collection(
            collection_name=collection_name,
            vectors_config=VectorParams(
                size=self._resolve_embedding_dim(),
                distance=Distance[self.cfg.distance_metric]
            ),
            on_disk_payload=True,
            optimizers_config=OptimizersConfigDiff(
                indexing_threshold=20000
            )
        )
        self._init_collection_meta(collection_name)
        self.logger.info(f"Создана новая коллекция: {collection_name}")
        return True

    def _init_collection_meta(self, collection_name: str):
        """Внутренняя функция для инициализации мета-информации"""
        client = self._get_qdrant_client()
        vector_size = self._resolve_embedding_dim()
        meta_id = meta_id_for_collection(collection_name)
        payload = {
            "__type__": self.collection_meta_type,
            "index_status": "empty",
            "documents_count": 0,
            "created_at": datetime.now().isoformat(),
            "last_updated": datetime.now().isoformat(),
            "embedding": {
                "model": self.cfg.embed_model_name,
                "chunk_size": self.cfg.chunk_size,
                "chunk_overlap": self.cfg.chunk_overlap,
            },
            "qdrant": {
                "collection": collection_name,
                "alias": self.cfg.qdrant_alias,
                "distance": self.cfg.distance_metric,
                "vector_size": vector_size,
            }
        }
        client.upsert(
            collection_name=collection_name,
            points=[
                PointStruct(
                    id=meta_id,
                    vector=[0.0] * vector_size,
                    payload=payload
                )
            ]
        )

    

    def health_check_collection(self, collection) -> dict|bool:
        """Проверка состояния подключения к Qdrant и существования коллекции."""
        if not self.cfg.use_qdrant:
            self.logger.warning("Qdrant не используется, проверка состояния пропущено")
            return False
        client = self._get_qdrant_client()
        if not client.collection_exists(collection):
            self.logger.error(f"Коллекция Qdrant '{collection}' не найдена")
            return False
        info = client.get_collection(collection)
        count = client.count(collection_name=collection, exact=True).count
        return {
            "success": True,
            "collection": collection,
            "points_count": count,
            "status": info.status
        }
    
    def collection_switch(self, collection) -> dict:
        """Переключить активную коллекцию Qdrant"""
        if not self.cfg.use_qdrant:
            self.logger.warning("Qdrant не используется, переключение коллекции пропущено")
            return {"success":False}
        client = self._get_qdrant_client()
        current = self.get_active_collection()
        if current== collection:
            return {"success": True, "message": "Already on this collection"}
        try:
            #  Атомарная операция в Qdrant удаляем старый алиас и создаем новый, если не было
            # операция удаления просто игнорируется
            alias_ops = []
            if current:
                alias_ops.append(
                    models.DeleteAliasOperation(
                        delete_alias=models.DeleteAlias(alias_name=self.cfg.qdrant_alias)
                    )
                )
            alias_ops.append(models.CreateAliasOperation(
                create_alias=models.CreateAlias(collection_name=collection, alias_name=self.cfg.qdrant_alias)
            ))
            client.update_collection_aliases(change_aliases_operations=alias_ops)
            self.logger.info(f"Смена алиаса {self.cfg.qdrant_alias} с {current} на {collection}")
            self.index = None
            self.retriever = None 
            return {"success": True, "old": current, "new": collection}
        except Exception as e:
            self.logger.critical(f"SWITCH FAILED: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
    
    def prepare_new_collection(self, version: int,
                               docs_texts: list,      
                               doc_counter: int,      
                               points_count: int,    
                               delete_old: bool=False) -> dict:
        """
        Полный цикл смены коллекции Qdrant с загрузкой новых документов.
        1. Создание новой коллекции с уникальным именем
        2. Загрузка документов
        3. Проверка здоровья новой коллекции
        4. Смена алиаса на новую коллекцию
        5. Опциональное удаление старой коллекции
        """
        if not self.cfg.use_qdrant:
            self.logger.warning("Qdrant не используется, подготовка новой коллекции пропущено")
            return {"success": False, "error": "qdrant_not_used"}
        base = self.cfg.qdrant_collection
        new_collection_name = f"{base}_v{version}"
        self.logger.info(f"Подготовка новой коллекции Qdrant: {new_collection_name}")
        old_collection = self.get_active_collection()
        self.logger.info(f"Текущая активная коллекция: {old_collection}")
        if not self.create_collection(collection_name=new_collection_name):
            self.logger.error(f"Не удалось создать новую коллекцию '{new_collection_name}'")
            return {"success": False, "error": "collection_creation_failed"}
        ok = self.create_and_persist_index(docs_texts=docs_texts, doc_counter=doc_counter,points_count=points_count, target_collection=new_collection_name)
        if not ok:
            return {"success": False, "error": "index_creation_failed"}

        health = self.health_check_collection(new_collection_name)
        if not health or not health.get("success"):
            self.logger.error(f"Проверка здоровья новой коллекции '{new_collection_name}' не пройдена")
            return {"success": False, "error": "health_check_failed"}
        switch = self.collection_switch(new_collection_name)
        if not switch["success"]:
            return switch
        if delete_old and old_collection and old_collection != new_collection_name:
            self.collection_delete(old_collection)
        return {"success": True, "new_collection": new_collection_name, "old_collection": old_collection, "points":health['points_count']}

    def alias_exists(self, alias_name: str)-> bool:
        if not self.cfg.use_qdrant:
                return False
        try:
            client = self._get_qdrant_client()
            aliases = client.get_aliases().aliases
            return any(a.alias_name == alias_name for a in aliases)
        except Exception as e:
            return False

    def reload_runtime(self) -> bool:
        """
        Перезагружает runtime-состояния Index, Retriever из Активного алиаса.
        Должен вызываться поле смены алиаса или обновления даных.
        """
        if not self.cfg.use_qdrant:
            return False
        try:
            active_collection = self.get_active_collection()
            if not active_collection:
                self.logger.warning(f"Alias {self.cfg.qdrant_alias} not found or empty.")
                return False
            self.logger.info(f"Reloading runtime from active collection: {active_collection}")
            client = self._get_qdrant_client()
            vector_store = QdrantVectorStore(client=client, collection_name=self.cfg.qdrant_alias) # читаем через alias
            index = VectorStoreIndex.from_vector_store(vector_store=vector_store, embed_model=self.embed_model)
            retriever = index.as_retriever(similarity_top_k=self.cfg.similarity_top_k, similarity_cutoff=self.cfg.similarity_cutoff)
            self.index = index
            self.retriever = retriever
            self.metadata = self.get_active_metadata()
            self.logger.info("Runtime reloaded successfully")
            return True
        except Exception as e:
            self.logger.error(f"Runtime reload failed: {e}", exc_info=True)
            return False
    
    def get_retriever_for_collection(self, collection, top_k, filters: dict | None=None):
        collection_name = collection or self.get_active_collection()
        client = self._get_qdrant_client()
        qdrant_filter = None
        if filters:
            conditions = []
            for key, value in filters.items():
                if value is None:
                    continue
                conditions.append(
                    FieldCondition(
                        key=key,
                        match=MatchValue(value=value)
                    )
                )
            if conditions:
                qdrant_filter = Filter(must=conditions)
        vector_store = QdrantVectorStore(
            client=client,
            collection_name=collection_name,
            filters=qdrant_filter)
        index = VectorStoreIndex.from_vector_store(vector_store)

        return index.as_retriever(similarity_top_k=top_k, similarity_cutoff=self.cfg.similarity_cutoff)

    def rebuild_index(self,
                    docs_texts: list,      
                    doc_counter: int,      
                    points_count: int,  
                    target_collection:Optional[str]=None) -> bool:
        """
        Безопасная пересборка индекса.
        Если target_collection не передан, пытается использовать дефолтную (self.qdrant_collection).
        ВАЖНО: Метод не переключает алиасы, он просто наполняет коллекцию.
        """
        if not self.cfg.use_qdrant:
            self.logger.warning("Qdrant disabled.")
            return False
        target = target_collection or self.cfg.qdrant_alias
        if target == self.cfg.qdrant_alias and self.alias_exists(self.cfg.qdrant_alias):
            target = self.get_active_collection()
        else:
            target = self.cfg.qdrant_collection
        self.logger.info(f"Rebuilding index in collection: {target}")
        try:
            ok =self.create_and_persist_index(docs_texts=docs_texts, doc_counter=doc_counter,points_count=points_count, target_collection=target)
            if ok:
                self.logger.info("Rebuild successful.")
                # Если у нас не было алиаса вообще, создаем его на эту коллекцию
                if not self.alias_exists(self.cfg.qdrant_alias):
                    self.logger.info("Alias missing. Creating initial alias.")
                    self.collection_switch(target) # Это создаст алиас и сделает reload_runtime
                    
                return True
                
                
        except Exception as e:
            self.logger.error(f"Rebuild failed: {e}", exc_info=True)
            return False



class IndexRuntime:
    def __init__(self):
        self.initialized = False
        self.last_update = None