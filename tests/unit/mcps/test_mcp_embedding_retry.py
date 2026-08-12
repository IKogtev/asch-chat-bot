"""Unit tests for RemoteEmbedding retry behaviour."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests


@pytest.fixture
def embedding_module(monkeypatch):
    monkeypatch.setenv("EMBEDDING_RETRY_TIMEOUT_SEC", "3")
    monkeypatch.setenv("EMBEDDING_RETRY_INTERVAL_SEC", "0")

    import importlib

    import utils.mcp_embedding as mod

    importlib.reload(mod)
    return mod


def _ok_response(vectors=None):
    vectors = vectors or [[0.1, 0.2]]
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {
        "data": [{"embedding": v} for v in vectors],
    }
    return resp


def _http_error(status_code: int) -> requests.HTTPError:
    response = MagicMock()
    response.status_code = status_code
    err = requests.HTTPError(f"{status_code} error")
    err.response = response
    return err


@pytest.mark.unit
def test_embedding_succeeds_without_retry(embedding_module):
    client = embedding_module.RemoteEmbedding(
        api_url="https://embed.example/v1/embeddings",
        api_key="key",
        model_name="model",
    )
    with patch.object(
        embedding_module.requests,
        "post",
        return_value=_ok_response([[0.5, 0.6]]),
    ) as post:
        result = client._request_embeddings(["hello"])

    assert result == [[0.5, 0.6]]
    assert post.call_count == 1


@pytest.mark.unit
def test_embedding_retries_on_connection_error_then_succeeds(
    embedding_module, monkeypatch
):
    monkeypatch.setenv("EMBEDDING_RETRY_TIMEOUT_SEC", "5")
    monkeypatch.setenv("EMBEDDING_RETRY_INTERVAL_SEC", "1")

    client = embedding_module.RemoteEmbedding(
        api_url="https://embed.example/v1/embeddings",
        api_key="key",
        model_name="model",
    )
    side_effects = [
        requests.ConnectionError("down"),
        requests.ConnectionError("still down"),
        _ok_response([[1.0, 2.0]]),
    ]
    with patch.object(
        embedding_module.requests, "post", side_effect=side_effects
    ) as post:
        with patch.object(embedding_module.time, "sleep") as sleep:
            result = client._request_embeddings(["q"])

    assert result == [[1.0, 2.0]]
    assert post.call_count == 3
    assert sleep.call_count == 2
    assert all(call.args[0] == 1.0 for call in sleep.call_args_list)


@pytest.mark.unit
def test_embedding_retries_on_503_then_raises_after_budget(
    embedding_module, monkeypatch
):
    monkeypatch.setenv("EMBEDDING_RETRY_TIMEOUT_SEC", "2")
    monkeypatch.setenv("EMBEDDING_RETRY_INTERVAL_SEC", "1")

    client = embedding_module.RemoteEmbedding(
        api_url="https://embed.example/v1/embeddings",
        api_key="key",
        model_name="model",
    )

    clock = {"t": 100.0}

    def monotonic():
        return clock["t"]

    def sleep(seconds):
        clock["t"] += float(seconds)

    with patch.object(
        embedding_module.requests,
        "post",
        side_effect=_http_error(503),
    ) as post:
        with patch.object(embedding_module.time, "monotonic", side_effect=monotonic):
            with patch.object(embedding_module.time, "sleep", side_effect=sleep):
                with pytest.raises(requests.HTTPError):
                    client._request_embeddings(["q"])

    assert post.call_count >= 2


@pytest.mark.unit
def test_embedding_retries_on_400_then_succeeds(embedding_module, monkeypatch):
    monkeypatch.setenv("EMBEDDING_RETRY_TIMEOUT_SEC", "5")
    monkeypatch.setenv("EMBEDDING_RETRY_INTERVAL_SEC", "1")

    client = embedding_module.RemoteEmbedding(
        api_url="https://embed.example/v1/embeddings",
        api_key="key",
        model_name="model",
    )
    with patch.object(
        embedding_module.requests,
        "post",
        side_effect=[_http_error(400), _ok_response([[0.3, 0.4]])],
    ) as post:
        with patch.object(embedding_module.time, "sleep"):
            result = client._request_embeddings(["bad"])

    assert result == [[0.3, 0.4]]
    assert post.call_count == 2


@pytest.mark.unit
def test_embedding_retry_timeout_env_invalid_falls_back(embedding_module, monkeypatch):
    monkeypatch.setenv("EMBEDDING_RETRY_TIMEOUT_SEC", "not-a-number")
    assert embedding_module.embedding_retry_timeout_sec() == 30.0
