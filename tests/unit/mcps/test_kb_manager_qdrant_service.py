import ast
import types
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest


def _load_class(file_path: Path, class_name: str, extra_globals: dict):
    tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
    selected = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name]
    module = ast.Module(body=selected, type_ignores=[])
    namespace = {"__builtins__": __builtins__}
    namespace.update(extra_globals)
    exec(compile(module, str(file_path), "exec"), namespace)
    return namespace[class_name]


CollectionType = Enum("CollectionType", {"DOCUMENTS": "documents", "FAQ": "faq"})
CollectionTypeAlias = Enum("CollectionTypeAlias", {"kb": "kb", "faq": "faq"})

QdrantService = _load_class(
    Path(__file__).resolve().parents[3] / "mcps" / "kb-manager" / "app" / "services" / "qdrant_service.py",
    "QdrantService",
    {
        "List": List,
        "Optional": Optional,
        "Dict": Dict,
        "Any": Any,
        "datetime": datetime,
        "CollectionType": CollectionType,
        "CollectionTypeAlias": CollectionTypeAlias,
        "Distance": types.SimpleNamespace(COSINE=types.SimpleNamespace(name="COSINE")),
        "PointStruct": lambda **kwargs: kwargs,
        "meta_id_for_collection": lambda name: f"meta:{name}",
        "chunk_id_to_uuid": lambda chunk_id: f"uuid:{chunk_id}",
        "models": types.SimpleNamespace(),
        "Filter": object,
        "FieldCondition": object,
        "MatchValue": object,
        "MatchAny": object,
        "VectorParams": object,
        "QdrantVectorStore": object,
        "SentenceSplitter": object,
        "RemoteEmbedding": object,
        "Settings": types.SimpleNamespace(),
        "qdrant_client": types.SimpleNamespace(QdrantClient=object),
    },
)


@pytest.mark.unit
def test_generate_alias_name_builds_expected_name() -> None:
    service = QdrantService.__new__(QdrantService)

    assert service._generate_alias_name("faq_collection") == "faq_collection_active"


@pytest.mark.unit
def test_generate_alias_name_rejects_invalid_name() -> None:
    service = QdrantService.__new__(QdrantService)

    with pytest.raises(ValueError, match="Invalid collection name"):
        service._generate_alias_name("faq")


@pytest.mark.unit
def test_detect_collection_type_distinguishes_faq_and_documents() -> None:
    service = QdrantService.__new__(QdrantService)

    assert service.detect_collection_type("faq_collection") == "faq"
    assert service.detect_collection_type("kb_collection") == "documents"


@pytest.mark.unit
def test_build_qdrant_payload_keeps_required_fields_and_ignores_none() -> None:
    service = QdrantService.__new__(QdrantService)

    payload = service._build_qdrant_payload(
        {
            "text": "chunk",
            "meta": {
                "chunk_id": "doc#0",
                "kb_id": "kb1",
                "source": "doc.txt",
                "optional": None,
            },
        }
    )

    assert payload == {
        "text": "chunk",
        "chunk_id": "doc#0",
        "kb_id": "kb1",
        "source": "doc.txt",
    }


@pytest.mark.unit
def test_get_collection_info_returns_error_payload_on_client_exception() -> None:
    service = QdrantService.__new__(QdrantService)
    service.collection_name = "kb_collection"
    service.qdrant_client = types.SimpleNamespace(
        get_collection=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom"))
    )

    result = service.get_collection_info()

    assert result == {"name": "kb_collection", "error": "boom"}
