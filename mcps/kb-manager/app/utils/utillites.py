import requests
import asyncio
from typing import List
from llama_index.core.base.embeddings.base import BaseEmbedding
from pydantic import Field
from pathlib import Path
import hashlib
import uuid

class RemoteEmbedding(BaseEmbedding):
    api_url: str = Field(...)
    api_key: str = Field(...)
    model_name: str = Field(...)

    def __init__(self, api_url: str, api_key: str, model_name: str):
        # Важно: сначала вызываем __init__ Pydantic
        super().__init__(
            api_url=api_url.rstrip("/"),
            api_key=api_key,
            model_name=model_name,
        )

    #
    # Реальный запрос к API
    #
    def _request_embeddings(self, inputs: List[str]) -> List[List[float]]:
        url = f"{self.api_url}"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        payload = {"model": self.model_name, "input": inputs}

        resp = requests.post(url, json=payload, headers=headers, timeout=60)
        resp.raise_for_status()

        data = resp.json()["data"]
        return [item["embedding"] for item in data]

    #
    # Требуемые LlamaIndex методы
    #
    def _get_text_embedding(self, text: str) -> List[float]:
        return self._request_embeddings([text])[0]

    def _get_query_embedding(self, query: str) -> List[float]:
        return self._request_embeddings([query])[0]

    async def _aget_query_embedding(self, query: str) -> List[float]:
        return await asyncio.to_thread(self._get_query_embedding, query)

def hash_file(path: Path) -> str:
    """Вычисляет SHA256 хеш файла."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()  

def chunk_id_to_uuid(chunk_id: str)-> str:
    """функция для приведение к виду uuid обычной строки doc#1..."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_id))


def meta_id_for_collection(collection_name: str) -> str:
        """Получение id для мета информации"""
        return str(uuid.uuid5(
            uuid.NAMESPACE_DNS,
            f"{collection_name}::collection_meta"
        ))

def compute_chunk_hash(text: str, section_path: list, source_name: str) -> str:
    """
    Хеш по содержанию + section_path + имени файла
    Это гарантирует:
    - одинаковый текст в разных разделах = разный hash
    - одинаковый текст в разных файлах = разный hash
    """
    normalized = text.strip().lower()
    
    context = "|".join([
        normalized,
        "/".join(section_path) if section_path else "",
        source_name
    ])
    
    return hashlib.sha256(context.encode("utf-8")).hexdigest()