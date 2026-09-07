from pathlib import Path
from unittest.mock import AsyncMock
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from web_bff.config import settings
from web_bff.files import FileTokenError, FileUrlIssuer
from web_bff.kb_tree import KbTreeError, list_kb_node, normalize_rel_path, resolve_kb_file


class FakeOtp:
    def __init__(self) -> None:
        self.sessions = {"session-token": "user-1"}

    async def user_id_for_token(self, token: str):
        return self.sessions.get(token)


def _tree(tmp_path: Path) -> Path:
    (tmp_path / "1 Продукты" / "Fort Knox").mkdir(parents=True)
    (tmp_path / "1 Продукты" / "Fort Knox" / "условия.pdf").write_bytes(b"pdf")
    (tmp_path / "_prepared").mkdir()
    (tmp_path / "_prepared" / "hidden.pdf").write_bytes(b"x")
    (tmp_path / "readme.md").write_text("hi", encoding="utf-8")
    return tmp_path


@pytest.mark.unit
def test_list_root_hides_service_folders(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    node = list_kb_node(root, "")
    assert node["path"] == ""
    assert node["parent"] is None
    assert [item["name"] for item in node["folders"]] == ["1 Продукты"]
    assert [item["name"] for item in node["files"]] == ["readme.md"]


@pytest.mark.unit
def test_list_nested_folder(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    node = list_kb_node(root, "1 Продукты/Fort Knox")
    assert node["path"] == "1 Продукты/Fort Knox"
    assert node["parent"] == "1 Продукты"
    assert node["files"][0]["name"] == "условия.pdf"
    assert node["files"][0]["path"] == "1 Продукты/Fort Knox/условия.pdf"


@pytest.mark.unit
def test_rejects_parent_and_ignored_paths(tmp_path: Path) -> None:
    with pytest.raises(KbTreeError, match="invalid_path"):
        normalize_rel_path("../etc")
    with pytest.raises(KbTreeError, match="invalid_path"):
        list_kb_node(_tree(tmp_path), "_prepared")


@pytest.mark.unit
def test_resolve_kb_file_stays_inside_root(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    resolved = resolve_kb_file(root, "1 Продукты/Fort Knox/условия.pdf")
    assert resolved.is_file()
    with pytest.raises(FileTokenError, match="not_found"):
        resolve_kb_file(root, "../readme.md")


def _client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    root = _tree(tmp_path)
    monkeypatch.setattr(settings, "allow_dev_auth", False)
    monkeypatch.setattr(settings, "cookie_name", "nastya_web")
    monkeypatch.setattr(settings, "kb_root", str(root))
    from web_bff.app import app

    otp = FakeOtp()

    async def fake_lifespan(_app):
        _app.state.otp = otp
        _app.state.pool = object()
        _app.state.file_urls = FileUrlIssuer("secret")
        yield

    app.router.lifespan_context = fake_lifespan
    client = TestClient(app)
    app.state.otp = otp
    app.state.pool = object()
    app.state.file_urls = FileUrlIssuer("secret")
    monkeypatch.setattr(
        "web_bff.auth.get_user_by_id",
        AsyncMock(
            return_value={
                "id": "user-1",
                "is_blocked": False,
                "first_name": "Ivan",
                "last_name": "",
                "phone_number": "+1",
            }
        ),
    )
    return client


@pytest.mark.unit
def test_kb_tree_requires_auth(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, tmp_path)
    response = client.get("/kb/tree")
    assert response.status_code == 401


@pytest.mark.unit
def test_kb_tree_returns_signed_file_urls(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, tmp_path)
    response = client.get(
        "/kb/tree",
        params={"path": "1 Продукты/Fort Knox"},
        cookies={"nastya_web": "session-token"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["folders"] == []
    assert body["files"][0]["name"] == "условия.pdf"
    url = body["files"][0]["url"]
    assert url.startswith("/files/")
    token = unquote(url.rsplit("/", 1)[-1])
    payload = FileUrlIssuer("secret").parse(token)
    assert payload["k"] == "fs"
    assert payload["u"] == "user-1"
    assert payload["p"] == "1 Продукты/Fort Knox/условия.pdf"
