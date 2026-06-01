"""Embedding client and Qdrant meta helpers shared by MCP search services."""

from __future__ import annotations

import asyncio
import uuid
from typing import List

import requests
from llama_index.core.base.embeddings.base import BaseEmbedding
from pydantic import Field


class RemoteEmbedding(BaseEmbedding):
    api_url: str = Field(...)
    api_key: str = Field(...)
    model_name: str = Field(...)

    def __init__(self, api_url: str, api_key: str, model_name: str):
        super().__init__(
            api_url=api_url.rstrip("/"),
            api_key=api_key,
            model_name=model_name,
        )

    def _request_embeddings(self, inputs: List[str]) -> List[List[float]]:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        payload = {"model": self.model_name, "input": inputs}
        resp = requests.post(self.api_url, json=payload, headers=headers, timeout=60)
        resp.raise_for_status()
        data = resp.json()["data"]
        return [item["embedding"] for item in data]

    def _get_text_embedding(self, text: str) -> List[float]:
        return self._request_embeddings([text])[0]

    def _get_query_embedding(self, query: str) -> List[float]:
        return self._request_embeddings([query])[0]

    async def _aget_query_embedding(self, query: str) -> List[float]:
        return await asyncio.to_thread(self._get_query_embedding, query)


def meta_id_for_collection(collection_name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{collection_name}::collection_meta"))
