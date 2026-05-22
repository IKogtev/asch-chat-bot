from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, Literal
from enum import Enum
from datetime import datetime


class DocumentInfo(BaseModel):
    """Information about a document"""
    document_id: str
    source_name: Optional[str]
    source_type: Optional[str]
    doc_hash: Optional[str]
    kb_id: Optional[str]
    user_id: str
    chunks_count: Optional[int]
    created_at: Optional[str] = None
    version: int = 1


class SearchRequest(BaseModel):
    """Search request model"""
    query: str
    limit: int = Field(default=10, ge=1, le=100)
    filters: Optional[Dict[str, Any]] = None
    search_mode: Literal["hybrid", "dense"] = Field(
        default="hybrid",
        description="hybrid: dense + sparse BM25 + RRF; dense: только семантический поиск по вектору dense",
    )


class SearchResult(BaseModel):
    """Search result model"""
    document_id: str
    point_id: str
    chunk_index: int
    score: float
    text: str
    answer: Optional[str] = None
    source_name: Optional[str] = None
    source_type: Optional[str] = None
    source_hash: Optional[str] = None
    kb_id: Optional[str] = None
    user_id: Optional[str] = None
    created_at: Optional[datetime]
    rrf_score: Optional[float] = None
    dense_score: Optional[float] = None
    sparse_score: Optional[float] = None

class SwitchCollectionRequest(BaseModel):
    """Switch collection model"""
    collection_name: str
    collection_type: str

class DeleteCollectionRequest(BaseModel):
    collection: str

class DeleteKBRequest(BaseModel):
    kb_id: str
    collection_name: str


class CollectionType(str, Enum):
    kb = "kb"
    faq = "faq"

class SwitchAliasRequest(BaseModel):
    collection_name: str
    collection_type: CollectionType

class SyncInterval(BaseModel):
    hours: int