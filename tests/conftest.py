import asyncio
from collections.abc import Iterator
import itertools
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import types

import pytest


_TEST_TMP = Path(__file__).resolve().parents[1] / ".test_tmp" / f"run-{os.getpid()}"
_TEST_TMP.mkdir(exist_ok=True)
tempfile.tempdir = str(_TEST_TMP)
_TMP_PATH_COUNTER = itertools.count()


if importlib.util.find_spec("qdrant_client") is None:
    qdrant_client_stub = types.ModuleType("qdrant_client")
    qdrant_models_stub = types.ModuleType("qdrant_client.models")
    qdrant_http_stub = types.ModuleType("qdrant_client.http")
    qdrant_http_models_stub = types.ModuleType("qdrant_client.http.models")

    class _Distance:
        COSINE = "Cosine"

    class _Modifier:
        IDF = "idf"

    class _SparseVector:
        def __init__(self, indices, values):
            self.indices = indices
            self.values = values

    class _SparseVectorParams:
        def __init__(self, modifier=None):
            self.modifier = modifier

    class _VectorParams:
        def __init__(self, size, distance):
            self.size = size
            self.distance = distance

    class _MatchValue:
        def __init__(self, value):
            self.value = value

    class _FieldCondition:
        def __init__(self, key, match):
            self.key = key
            self.match = match

    class _Filter:
        def __init__(self, must=None, must_not=None):
            self.must = must or []
            self.must_not = must_not or []

    qdrant_client_stub.QdrantClient = object
    qdrant_models_stub.Distance = _Distance
    qdrant_models_stub.Modifier = _Modifier
    qdrant_models_stub.SparseVector = _SparseVector
    qdrant_models_stub.SparseVectorParams = _SparseVectorParams
    qdrant_models_stub.VectorParams = _VectorParams
    qdrant_models_stub.FieldCondition = _FieldCondition
    qdrant_models_stub.Filter = _Filter
    qdrant_models_stub.MatchValue = _MatchValue
    qdrant_http_models_stub.FieldCondition = _FieldCondition
    qdrant_http_models_stub.Filter = _Filter
    qdrant_http_models_stub.MatchValue = _MatchValue
    qdrant_http_stub.models = qdrant_http_models_stub
    qdrant_client_stub.models = qdrant_models_stub
    qdrant_client_stub.http = qdrant_http_stub

    sys.modules["qdrant_client"] = qdrant_client_stub
    sys.modules["qdrant_client.models"] = qdrant_models_stub
    sys.modules["qdrant_client.http"] = qdrant_http_stub
    sys.modules["qdrant_client.http.models"] = qdrant_http_models_stub


@pytest.fixture
def event_loop() -> Iterator[asyncio.AbstractEventLoop]:
    """Create a dedicated event loop for each test case."""
    loop = asyncio.new_event_loop()
    try:
        yield loop
    finally:
        loop.close()


@pytest.fixture
def tmp_path(request) -> Path:
    safe_name = "".join(
        char if char.isalnum() or char in "-_" else "_"
        for char in request.node.name
    )[:80]
    path = _TEST_TMP / f"{next(_TMP_PATH_COUNTER):04d}_{safe_name}"
    path.mkdir(parents=True, exist_ok=False)
    return path
