"""Qdrant service for Qdrant document management"""
from typing import List, Optional, Dict, Any
from datetime import datetime
import qdrant_client
from llama_index.core import Settings
from llama_index.vector_stores.qdrant import QdrantVectorStore
from llama_index.core.node_parser import SentenceSplitter
from qdrant_client import models
from qdrant_client.models import (
    Distance, VectorParams,Filter, FieldCondition, MatchValue, PointStruct, MatchAny
    )
from enum import Enum
from app.utils.utillites import RemoteEmbedding, chunk_id_to_uuid, meta_id_for_collection
from app.models import CollectionType as CollectionTypeAlias

class CollectionType(str, Enum):
    DOCUMENTS = "documents"
    FAQ = "faq"

class QdrantService:
    """Service for managing documents in Qdrant using llama_index"""
    
    def __init__(
        self,
        collection_name: str,
        embedding_api_base: str,
        embedding_api_key: str,
        embedding_model: str,
        embedding_dimensions: int,
        collection_type: CollectionType=CollectionType.DOCUMENTS,
        qdrant_port: int=6333,
        qdrant_host: str="localhost",
        chunk_size: int = 1000,
        chunk_overlap: int = 200
    ):
        qdrant_timeout = 10
        self.qdrant_client = qdrant_client.QdrantClient(
            host=qdrant_host,
            port=qdrant_port,
            timeout=qdrant_timeout,
            prefer_grpc=True
        )
        self.collection_name = collection_name
        self.collection_type = collection_type or CollectionType.DOCUMENTS
        self.vector_size = embedding_dimensions
        # Initialize embeddings
        Settings.embed_model = RemoteEmbedding(
                api_url=str(embedding_api_base),
                api_key=str(embedding_api_key),
                model_name=str(embedding_model),
            )
        self.embed_model = RemoteEmbedding(
            api_url=str(embedding_api_base),
            api_key=str(embedding_api_key),
            model_name=str(embedding_model),
        )
        # Initialize text splitter
        self.text_splitter = SentenceSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap
        )
        # Ensure collection exists
        self.ensure_collection()
        
        # Initialize vector store
        self.vector_store = QdrantVectorStore(
            client=self.qdrant_client,
            collection_name=self.collection_name
        )
    
    def ensure_collection(self):
        """Create collection if it doesn't exist"""
        try:
            collections = self.qdrant_client.get_collections().collections
            collection_names = [c.name for c in collections]
            if self.collection_name not in collection_names:
                self.qdrant_client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config=VectorParams(
                        size=self.vector_size,
                        distance=Distance.COSINE
                    )
                )
                print(f"Created collection: {self.collection_name}")
            else:
                print(f"Collection {self.collection_name} already exists")
            self.switch_alias(self.collection_name, CollectionTypeAlias.kb)
        except Exception as e:
            print(f"Error ensuring collection: {e}")
    
        
    def check_filename_exists(self, kb_id: str, source_name: str) -> Optional[Dict[str, Any]]:
        """Check if a document with the same kb_id and source_name already exists"""
        try:
            result = self.qdrant_client.scroll(
                collection_name=self.collection_name,
                scroll_filter=Filter(
                    must=[
                        FieldCondition(key="kb_id", match=MatchValue(value=kb_id)),
                        FieldCondition(key="source", match=MatchValue(value=source_name)),
                    ]
                ),
                limit=1,
                with_payload=True,
                with_vectors=False
            )

            points, _ = result
            if points:
                payload = points[0].payload or {}
                return {
                    "document_id": payload.get("document_id"),
                    "source_name": payload.get("source"),
                    "source_type": payload.get("source_type"),
                    "source_hash": payload.get("doc_hash"),
                    "version": payload.get("version", 1),
                    "created_at": payload.get("created_at"),
                    "chunks_count": None,
                }
        except Exception as e:
            print(f"[WARN] MCP filename check failed: {e}")
        
    def get_max_version(self, kb_id: str, source_name: str) -> int:
        """Get the maximum version number for a given source_name in a KB"""
        result = self.qdrant_client.scroll(
            collection_name=self.collection_name,
            scroll_filter=Filter(
                must=[
                    FieldCondition(
                        key="metadata.kb_id",
                        match=MatchValue(value=kb_id)
                    ),
                    FieldCondition(
                        key="metadata.source_name",
                        match=MatchValue(value=source_name)
                    )
                ]
            ),
            limit=100,
            with_payload=True,
            with_vectors=False
        )
        
        points, _ = result
        max_version = 0
        for point in points:
            metadata = point.payload.get("metadata", {})
            version = metadata.get("version", 1)
            if version > max_version:
                max_version = version
        
        return max_version
    
    
    def get_document_chunks(self, document_id: str) -> List[Dict[str, Any]]:
        """Get all chunks for a document"""
        # Use Qdrant client directly for filtering
        result = self.qdrant_client.scroll(
            collection_name=self.collection_name,
            scroll_filter=Filter(
                should=[
                    FieldCondition(
                        key="document_id",
                        match=MatchValue(value=document_id)
                    ),
                ]
            ),
            limit=1000,
            with_payload=True,
            with_vectors=False
        )
        
        points, _ = result
        # Sort by chunk_index
        chunks = []
        for point in points:
            payload = point.payload or {}
            if payload.get("document_id") == document_id and "chunk_id" in payload:
                try:
                    chunk_index = int(payload["chunk_id"].split("#")[-1])
                except Exception:
                    chunk_index = 0
                
                normalized_meta = {
                    "document_id": payload.get("document_id"),
                    "source_name": payload.get("source"),      
                    "source": payload.get("source"),
                    "source_type": payload.get("source_type", "md"),
                    "kb_id": payload.get("kb_id"),
                    "user_id": payload.get("user_id"),
                    "version": payload.get("version", 1),
                    "doc_hash": payload.get("doc_hash"),
                    "section_path": payload.get("section_path", []),
                    "created_at": payload.get("created_at"),
                    "file_path": payload.get("file_path"),
                }

                chunks.append({
                    "point_id": str(point.id),
                    "chunk_index": chunk_index,
                    "text": payload.get("text", ""),
                    "answer": payload.get("answer"),
                    "metadata": normalized_meta 
                })
        chunks.sort(key=lambda x: x["chunk_index"])
        return chunks
    
    def delete_document(self, document_id: str) -> bool:
        """Delete all chunks of a document"""
        # Check if document exists
        result = self.qdrant_client.scroll(
            collection_name=self.collection_name,
            scroll_filter=Filter(
                should=[
                    FieldCondition(
                        key="document_id",
                        match=MatchValue(value=document_id)
                    ),
                ]
            ),
            limit=1,
            with_payload=False,
            with_vectors=False
        )
        
        points, _ = result
        if not points:
            return False
        
        # Delete by filter using proper Qdrant models
        self.qdrant_client.delete(
            collection_name=self.collection_name,
            points_selector=Filter(
                should=[
                    FieldCondition(
                        key="document_id",
                        match=MatchValue(value=document_id)
                    )
                ]
            )
        )
        
        return True
    
    def list_documents(self) -> List[Dict[str, Any]]:
        """List all documents (one entry per document, not per chunk)"""
        # Scroll through all points
        result = self.qdrant_client.scroll(
            collection_name=self.collection_name,
            limit=10000,
            with_payload=True,
            with_vectors=False
        )
        
        points, _ = result
        
        # Group by document_id
        docs = {}
        for point in points:
            payload = point.payload or {}
            if "document_id" in payload and "chunk_id" in payload:
                doc_id = payload["document_id"]
                # извлекаем chunk index из "doc_id#N"
                chunk_index = 0
                try:
                    chunk_index = int(payload["chunk_id"].split("#")[-1])
                except Exception:
                    pass

                if doc_id not in docs:
                    docs[doc_id] = {
                        "document_id": doc_id,
                        "source_name": payload.get("source"),
                        "source_type": payload.get("source_type"),
                        "doc_hash": payload.get("doc_hash"),
                        "kb_id": payload.get("kb_id"),
                        "user_id": payload.get("user_id"),
                        "version": payload.get("version", 1),
                        "_max_chunk_index": chunk_index,
                        "created_at": payload.get("created_at", self.get_date_iso("2000-01-01")),
                        "section_path": payload.get("section_path", []),
                    }
                else: 
                    docs[doc_id]["_max_chunk_index"] = max(
                        docs[doc_id]["_max_chunk_index"],
                        chunk_index
                    )
                if self.collection_type == 'faq':
                    docs[doc_id]['question_preview'] = payload.get("text", "")
                    docs[doc_id]['answer_preview'] = payload.get("answer")

        for doc in docs.values():
            doc["chunks_count"] = doc.pop("_max_chunk_index")+1
            
        # Convert to list and sort by created_at
        documents = list(docs.values())
        documents.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        
        return list(docs.values())
    
    def list_knowledge_bases(self) -> List[Dict[str, Any]]:
        """List all knowledge bases with document counts"""
        # Get all documents
        documents = self.list_documents()
        
        # Group by kb_id
        kb_dict = {}
        for doc in documents:
            kb_id = doc.get("kb_id", "default")
            if kb_id not in kb_dict:
                kb_dict[kb_id] = {
                    "kb_id": kb_id,
                    "document_count": 0,
                    "total_chunks": 0,
                    "documents": []
                }
            kb_dict[kb_id]["document_count"] += 1
            kb_dict[kb_id]["total_chunks"] += doc.get("chunks_count", 0)
            kb_dict[kb_id]["documents"].append(doc)
        
        # Convert to list and sort by kb_id
        knowledge_bases = list(kb_dict.values())
        knowledge_bases.sort(key=lambda x: x["kb_id"])
        
        return knowledge_bases
    
    def search(
        self,
        query: str,
        limit: int = 10,
        filters: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """Search documents by semantic similarity using LangChain retriever for OLD Payload
        And using MCP payload -> qdrant search"""
        # Build filter if provided
        results: List[Dict[str, Any]] = []
        query_vector = self.embed_model.get_text_embedding_batch([query])[0]
        must_conditions = []

        must_not_conditions = [
            FieldCondition(
                key="__type__",
                match=MatchValue(value="collection_meta"),
            )
        ]
        # Дополнительные фильтры
        if filters:
            for key, value in filters.items():
                must_conditions.append(
                    FieldCondition(
                        key=key,
                        match=MatchValue(value=value)
                    )
                )
        q_filter = Filter(must=must_conditions, must_not=must_not_conditions)
        #  считаем доступные точки: 
        count_response = self.qdrant_client.count(
            collection_name=self.collection_name,
            count_filter=q_filter,
            exact=True
        )
        available = count_response.count or 0
        if available == 0:
            return []
        effective_limit = min(limit, available)
        response = self.qdrant_client.query_points(
            collection_name=self.collection_name,
            query = query_vector,
            limit=effective_limit,
            query_filter=q_filter,
            with_payload=True,
            with_vectors=False
        )
        if not response or not getattr(response, "points", None):
            return []
        for hit in response.points:
            payload = hit.payload or {}
            chunk_index = 0
            chunk_id = payload.get("chunk_id")
            if chunk_id and "#" in chunk_id:
                try:
                    chunk_index = int(chunk_id.split("#")[1])
                except Exception:
                    pass

            results.append({
                "document_id": payload.get("document_id"),
                "point_id": str(hit.id),
                "chunk_index": chunk_index,
                "text": payload.get("text", ""),
                "answer": payload.get("answer", ""),
                "score": float(hit.score),  # ❗ MCP — не семантический поиск
                "source_name": payload.get("source") or payload.get("source_name"),
                "source_type": payload.get("source_type", "md"),
                "kb_id": payload.get("kb_id"),
                "user_id": payload.get("user_id"),
                "created_at": payload.get("created_at")
            })

        return results
    
    def get_collection_info(self) -> Dict[str, Any]:
        """Get information about the collection"""
        try:
            info = self.qdrant_client.get_collection(collection_name=self.collection_name)
            return {
                "name": self.collection_name,
                "points_count": info.points_count,
                "vectors_count": info.vectors_count if hasattr(info, 'vectors_count') else info.points_count,
                "status": info.status.value if hasattr(info, 'status') else "unknown"
            }
        except Exception as e:
            return {
                "name": self.collection_name,
                "error": str(e)
            }
        
    def list_collections(self) -> Dict:
        try:
            result = self.qdrant_client.get_collections()
            collection_names = [{"name":c.name, "type": self.detect_collection_type(c.name)} for c in result.collections]
            return {
                "current_collection": self.collection_name,
                "current_type": self.collection_type,
                "collections": collection_names
            }
        except Exception as e:
            return {"current_collection": e,
                    "collections": "NONE"} 

    def detect_collection_type(self, name: str) -> str:
        if 'faq' in name.lower():
            return 'faq'
        return "documents"
    
    def switch_collection(self, collection_name: str, collection_type:CollectionType):
        if collection_name == self.collection_name:
            return
        self.collection_name = collection_name
        self.collection_type = collection_type
        self.vector_store = QdrantVectorStore(
            client=self.qdrant_client,
            collection_name=self.collection_name
        )

    def get_date_iso(self, date:str):
        dt = datetime.fromisoformat(date)
        iso_result = dt.isoformat()
        return iso_result
    
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

    def content_hash_exists(self, kb_id: str, content_hash: str) -> bool:
        """
        Проверяем есть ли уже chunk с таким content_hash в рамках kb_id
        """
        from qdrant_client.models import Filter, FieldCondition, MatchValue

        result = self.qdrant_client.scroll(
            collection_name=self.collection_name,
            scroll_filter=Filter(
                must=[
                    FieldCondition(
                        key="kb_id",
                        match=MatchValue(value=kb_id),
                    ),
                    FieldCondition(
                        key="content_hash",
                        match=MatchValue(value=content_hash),
                    )
                ]
            ),
            limit=1
        )

        points = result[0]
        return len(points) > 0

    def upload_points_qdrant(self,
                    docs_texts: list,
                    docs_count: int,
                    points_count: int, 
                    ) -> dict:
        
        if not docs_texts:
            return {"error": "No documents found"}
        batch_size = 50
        for i in range(0, points_count, batch_size):
            batch = docs_texts[i: i+batch_size]
            # извлекаем тексты 
            texts_batch = [item['text'] for item in batch]
            # генерируем эмбеддинги пачкой один запрос к API вместо 50
            embeddings_batch = self.embed_model.get_text_embedding_batch(texts_batch)
            points = []
            for j, item in enumerate(batch):
                meta = item.get("meta", {})
                kb_id = meta.get("kb_id")
                content_hash = meta.get("content_hash")
                chunk_id = meta.get("chunk_id")
                if not content_hash:
                    raise ValueError("content_hash is required for deduplication")

                # проверка на дубль
                if self.content_hash_exists(kb_id, content_hash):
                    continue
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
            if not points:
                continue
            self.qdrant_client.upsert(collection_name=self.collection_name, points=points)    
            info = self.qdrant_client.get_collection(collection_name=self.collection_name)
            
            meta = {'last_update': datetime.now().isoformat(),
                    'points_count': int(str(info.points_count))-1,
                    'documents_count': docs_count,
                    'index_status': 'initialized',
                    'storage_type': 'qdrant',
                    'source': "update from ui",
                    '__type__': "collection_meta"
                    }
            self.upsert_meta(**meta)
        return {
            "documents_count": points_count
    }
    
    def upsert_meta(self, **updates):
        meta_id = meta_id_for_collection(self.collection_name)
        points = self.qdrant_client.retrieve(self.collection_name, [meta_id])
        if points:
            payload = points[0].payload or {}    
        else:
            payload={}
        payload.update(updates)
        payload["last_update"] = datetime.now().isoformat()
        self.qdrant_client.upsert(
            collection_name=self.collection_name,
            points=[
                PointStruct(
                    id=meta_id,
                    vector=[0.0] * self.vector_size,
                    payload=payload
                )
            ]
        )

    def check_duplicates(self, kb_id: str, hashes: List[str]) -> List[str]:
        """
        Проверяет, есть ли FAQ с такими hash (canonical_question+answer)
        Возвращает список найденных hash
        """

        if not hashes:
            return []
        filt = Filter(
            must=[
                FieldCondition(key="kb_id", match=MatchValue(value=kb_id)
                ),
                FieldCondition(
                    key="doc_hash",
                    match=MatchAny(any=hashes)
                )
            ]
        )

        result = self.qdrant_client.scroll(
            collection_name=self.collection_name,
            scroll_filter=filt,
            limit=len(hashes)
        )

        points, _ = result
        return [p.payload.get("doc_hash") for p in points]


    def collection_delete(self, collection_name: str) -> bool:
        client = self.qdrant_client
        if not client.collection_exists(collection_name):
            return False

        client.delete_collection(collection_name)
        return True

    def create_collection(self, collection_name: str) -> bool:
        client = self.qdrant_client
        if client.collection_exists(collection_name):
            raise ValueError("Collection already exists")
                
        client.create_collection(
            collection_name=collection_name,
            vectors_config=VectorParams(
                size=self.vector_size,
                distance=Distance.COSINE
            )
        )

        return True
    
    def delete_kb(self, kb_id: str, collection_name: str) -> int:
        if not self.qdrant_client.collection_exists(collection_name):
            raise ValueError(f"Collection '{collection_name}' does not exist")
        filt = Filter(
            must=[
                FieldCondition(
                    key="kb_id",
                    match=MatchValue(value=kb_id)
                )
            ]
        )
        self.qdrant_client.delete(
            collection_name=collection_name,
            points_selector=filt
        )

    def get_active_collection(self, alias_name) -> str|None:
        aliases = self.qdrant_client.get_aliases()
        for a in aliases.aliases:
            if a.alias_name == alias_name:
                return a.collection_name
        return None
    
    def get_active_collections(self) -> dict:
        result = {}
        for ctype in ["faq", "kb"]:
            alias_name = f"{ctype}_collection_active"
            try:
                collection = self.get_active_collection(alias_name)
                result[ctype] = {
                    "alias": alias_name,
                    "collection": collection
                }
            except Exception:
                result[ctype] = None
        return result
    
    def switch_alias(self, collection_name: str, collection_type: CollectionType):
        alias_name = f"{collection_type.value}_collection_active"

        # проверка, что коллекция существует
        collections = self.qdrant_client.get_collections().collections
        names = {c.name for c in collections}

        if collection_name not in names:
            raise ValueError(f"Collection '{collection_name}' does not exist")

        # атомарное переключение alias
        current = self.get_active_collection(alias_name)
        if current == collection_name:
            return
        alias_ops = []
        if current:
            alias_ops.append(
                models.DeleteAliasOperation(
                    delete_alias=models.DeleteAlias(alias_name=alias_name)
                )
            )
        alias_ops.append(models.CreateAliasOperation(
            create_alias=models.CreateAlias(collection_name=collection_name, alias_name=alias_name)
        ))
        self.qdrant_client.update_collection_aliases(change_aliases_operations=alias_ops)  
        # обновляем runtime-состояние сервиса
        if collection_type == self.collection_type:
            self.collection_name = collection_name
