"""Embedding client and Qdrant meta helpers shared by MCP search services."""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from typing import List

import requests
from llama_index.core.base.embeddings.base import BaseEmbedding
from pydantic import Field

logger = logging.getLogger(__name__)

# Total wall-clock budget for retries on embedding API failures.
_DEFAULT_RETRY_TIMEOUT_SEC = 30.0
_DEFAULT_RETRY_INTERVAL_SEC = 1.0


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():  
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning(
            "Invalid %s=%r, using default %s",
            name,
            raw,
            default,
        )
        return default


def embedding_retry_timeout_sec() -> float:
    return max(0.0, _env_float("EMBEDDING_RETRY_TIMEOUT_SEC", _DEFAULT_RETRY_TIMEOUT_SEC))


def embedding_retry_interval_sec() -> float:
    return max(0.0, _env_float("EMBEDDING_RETRY_INTERVAL_SEC", _DEFAULT_RETRY_INTERVAL_SEC))


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

        retry_timeout = embedding_retry_timeout_sec()
        retry_interval = embedding_retry_interval_sec()
        deadline = time.monotonic() + retry_timeout
        attempt = 0
        last_error: BaseException | None = None

        while True:
            attempt += 1
            remaining = deadline - time.monotonic()
            if attempt > 1 and remaining <= 0:
                raise last_error if last_error is not None else RuntimeError(
                    "Embedding API retry budget exhausted"
                )

            # Clamp HTTP timeout to remaining retry budget so we do not overrun it.
            budget = remaining if remaining > 0 else retry_timeout
            http_timeout = max(budget, 0.1)

            try:
                resp = requests.post(
                    self.api_url,
                    json=payload,
                    headers=headers,
                    timeout=http_timeout,
                )
                resp.raise_for_status()
                data = resp.json()["data"]
                if attempt > 1:
                    logger.info(
                        "Embedding API recovered after %s attempt(s)",
                        attempt,
                    )
                return [item["embedding"] for item in data]
            except Exception as exc:
                last_error = exc
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    logger.error(
                        "Embedding API failed after %s attempt(s) "
                        "(retry budget %.1fs exhausted): %s",
                        attempt,
                        retry_timeout,
                        exc,
                    )
                    raise

                sleep_for = (
                    min(retry_interval, remaining) if retry_interval > 0 else 0.0
                )
                logger.warning(
                    "Embedding API error on attempt %s "
                    "(%.1fs left in retry budget): %s. Retrying in %.1fs",
                    attempt,
                    remaining,
                    exc,
                    sleep_for,
                )
                if sleep_for > 0:
                    time.sleep(sleep_for)
                else:
                    # interval=0: immediate retries, but bound attempts so a
                    # frozen/fast-fail clock cannot busy-loop forever.
                    max_immediate = max(1, int(retry_timeout) + 1)
                    if attempt >= max_immediate:
                        raise

    def _get_text_embedding(self, text: str) -> List[float]:
        return self._request_embeddings([text])[0]

    def _get_query_embedding(self, query: str) -> List[float]:
        return self._request_embeddings([query])[0]

    async def _aget_query_embedding(self, query: str) -> List[float]:
        return await asyncio.to_thread(self._get_query_embedding, query)


def meta_id_for_collection(collection_name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{collection_name}::collection_meta"))
