from pathlib import Path
from urllib.parse import unquote

import pytest

from bot.services.config import Settings
from web_bff.files import FileTokenError, FileUrlIssuer, resolve_kit_file


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
