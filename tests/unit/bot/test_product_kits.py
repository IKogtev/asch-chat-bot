from pathlib import Path

import pytest

from bot.services.product_kits import get_product_kit


FIXTURES_ROOT = Path(__file__).resolve().parents[2] / "fixtures" / "product_kits"
CYRILLIC_FIXTURES_ROOT = (
    Path(__file__).resolve().parents[2] / "fixtures" / "product_kits_cyrillic"
)


@pytest.mark.unit
def test_get_product_kit_returns_files_from_cyrillic_root() -> None:
    root = CYRILLIC_FIXTURES_ROOT / "Комплекты документов по продуктам"
    file_path = root / "2832" / "условия.pdf"

    result = get_product_kit("2832", "Fort Knox", root=root)

    assert result["status"] == "ok"
    assert result["product_code"] == "2832"
    assert result["files"] == [
        {"path": str(file_path.resolve()), "name": "условия.pdf", "size": file_path.stat().st_size}
    ]


@pytest.mark.unit
def test_get_product_kit_returns_not_found_for_missing_folder() -> None:
    result = get_product_kit("missing", root=FIXTURES_ROOT)

    assert result["status"] == "not_found"
    assert result["files"] == []


@pytest.mark.unit
def test_get_product_kit_uses_folder_kit_when_present(tmp_path) -> None:
    folder = tmp_path / "Fort Knox (2832)"
    folder.mkdir()
    file_path = folder / "kit.pdf"
    file_path.write_text("kit", encoding="utf-8")

    result = get_product_kit("2832", "Fort Knox", folder_kit=folder.name, root=tmp_path)

    assert result["status"] == "ok"
    assert result["files"] == [
        {"path": str(file_path.resolve()), "name": "kit.pdf", "size": file_path.stat().st_size}
    ]


@pytest.mark.unit
def test_get_product_kit_ignores_files_in_nested_folders(tmp_path) -> None:
    folder = tmp_path / "Fort Knox" / "Fort Knox (2832)"
    nested = folder / "nested"
    nested.mkdir(parents=True)
    direct_file = folder / "kit.pdf"
    nested_file = nested / "nested.pdf"
    direct_file.write_text("kit", encoding="utf-8")
    nested_file.write_text("nested", encoding="utf-8")

    result = get_product_kit(
        "2832",
        "Fort Knox",
        folder_kit="Fort Knox/Fort Knox (2832)",
        root=tmp_path,
    )

    assert result["status"] == "ok"
    assert result["files"] == [
        {
            "path": str(direct_file.resolve()),
            "name": "kit.pdf",
            "size": direct_file.stat().st_size,
        }
    ]


@pytest.mark.unit
def test_get_product_kit_treats_not_found_folder_kit_as_missing() -> None:
    result = get_product_kit("2832", "Fort Knox", folder_kit="не найдена", root=FIXTURES_ROOT)

    assert result["status"] == "not_found"
    assert result["files"] == []


@pytest.mark.unit
def test_get_product_kit_returns_empty_when_all_files_are_skipped() -> None:
    result = get_product_kit("hidden_only", root=FIXTURES_ROOT)

    assert result["status"] == "empty"
    assert result["files"] == []


@pytest.mark.unit
def test_get_product_kit_filters_hidden_and_service_files() -> None:
    result = get_product_kit("hidden_only", root=FIXTURES_ROOT)

    assert result["status"] == "empty"
    assert {item["reason"] for item in result["skipped_files"]} == {"hidden_or_service"}


@pytest.mark.unit
def test_get_product_kit_blocks_path_escape() -> None:
    result = get_product_kit("..", root=FIXTURES_ROOT)

    assert result["status"] == "invalid_request"


@pytest.mark.unit
def test_get_product_kit_limits_file_count_and_size() -> None:
    result = get_product_kit("2832", root=FIXTURES_ROOT, max_files=1, max_file_size_mb=1)

    assert result["status"] == "ok"
    assert len(result["files"]) == 1
    assert {item["reason"] for item in result["skipped_files"]} == {"too_many_files"}


@pytest.mark.unit
def test_get_product_kit_skips_too_large_files() -> None:
    result = get_product_kit("2832", root=FIXTURES_ROOT, max_file_size_mb=0)
    file_path = FIXTURES_ROOT / "2832" / "a.txt"

    assert result["status"] == "empty"
    assert {
        (item["path"], item["reason"], item["size"]) for item in result["skipped_files"]
    } == {
        (str(file_path.resolve()), "too_large", file_path.stat().st_size),
        (
            str((FIXTURES_ROOT / "2832" / "b.txt").resolve()),
            "too_large",
            (FIXTURES_ROOT / "2832" / "b.txt").stat().st_size,
        ),
        (
            str((FIXTURES_ROOT / "2832" / "c.txt").resolve()),
            "too_large",
            (FIXTURES_ROOT / "2832" / "c.txt").stat().st_size,
        ),
    }
