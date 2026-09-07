from pathlib import Path
from urllib.parse import unquote

import pytest

from bot.services.config import Settings
from web_bff.files import FileTokenError, FileUrlIssuer, build_kit_zip, resolve_kit_file


@pytest.mark.unit
def test_kit_token_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Settings, "PRODUCT_KITS_ROOT", tmp_path)
    file_path = tmp_path / "условия.pdf"
    file_path.write_bytes(b"pdf")
    issuer = FileUrlIssuer("secret")
    url = issuer.kit_url("user-1", str(file_path), "условия.pdf")
    token = unquote(url.rsplit("/", 1)[-1])
    payload = issuer.parse(token)
    assert payload["u"] == "user-1"
    assert payload["k"] == "kit"
    resolved = resolve_kit_file(payload["root"], payload["p"])
    assert resolved == file_path.resolve()


@pytest.mark.unit
def test_token_rejects_wrong_secret() -> None:
    issuer = FileUrlIssuer("a")
    url = issuer.kb_url("user-1", "doc_1", "a.pdf")
    token = unquote(url.rsplit("/", 1)[-1])
    with pytest.raises(FileTokenError):
        FileUrlIssuer("b").parse(token)


@pytest.mark.unit
def test_refresh_url_reissues_expired_token(monkeypatch: pytest.MonkeyPatch) -> None:
    issuer = FileUrlIssuer("secret", ttl_sec=10)
    monkeypatch.setattr("web_bff.files.time.time", lambda: 100)
    url = issuer.kb_url("user-1", "doc_1", "a.pdf")

    monkeypatch.setattr("web_bff.files.time.time", lambda: 200)
    old_token = unquote(url.rsplit("/", 1)[-1])
    with pytest.raises(FileTokenError, match="expired"):
        issuer.parse(old_token)

    refreshed = issuer.refresh_url(url, "user-1")
    payload = issuer.parse(unquote(refreshed.rsplit("/", 1)[-1]))
    assert payload["id"] == "doc_1"
    assert payload["exp"] == 210


@pytest.mark.unit
def test_refresh_url_rejects_other_user() -> None:
    issuer = FileUrlIssuer("secret")
    url = issuer.kb_url("user-1", "doc_1", "a.pdf")
    with pytest.raises(FileTokenError, match="wrong_user"):
        issuer.refresh_url(url, "user-2")


@pytest.mark.unit
def test_kit_zip_token_and_archive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Settings, "PRODUCT_KITS_ROOT", tmp_path)
    first = tmp_path / "a.pdf"
    second = tmp_path / "b.pdf"
    first.write_bytes(b"aaa")
    second.write_bytes(b"bbb")
    issuer = FileUrlIssuer("secret")
    url = issuer.kit_zip_url(
        "user-1",
        [(str(first), "a.pdf"), (str(second), "b.pdf")],
        "Комплект.zip",
    )
    token = unquote(url.rsplit("/", 1)[-1])
    payload = issuer.parse(token)
    assert payload["k"] == "kit_zip"
    blob = build_kit_zip(payload["files"])
    import zipfile
    import io

    names = zipfile.ZipFile(io.BytesIO(blob)).namelist()
    assert names == ["a.pdf", "b.pdf"]
