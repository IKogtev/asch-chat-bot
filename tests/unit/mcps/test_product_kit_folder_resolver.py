import importlib.util
import sys
from datetime import date
from pathlib import Path

import pytest


def _load_resolver_module():
    repo_root = Path(__file__).resolve().parents[3]
    module_path = (
        repo_root
        / "mcps"
        / "kb-manager"
        / "app"
        / "services"
        / "product_kit_folder_resolver.py"
    )
    spec = importlib.util.spec_from_file_location(
        "product_kit_folder_resolver_under_test",
        module_path,
    )
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


resolver = _load_resolver_module()


@pytest.mark.unit
def test_resolve_product_kit_folder_returns_not_found_without_candidates(tmp_path) -> None:
    result = resolver.resolve_product_kit_folder(
        kits_root=tmp_path,
        product_code="2832",
        product_name="Fort Knox",
    )

    assert result.folder_kit == resolver.NOT_FOUND_VALUE
    assert "code='2832'" in result.folder_kit_status
    assert "[]" in result.folder_kit_status


@pytest.mark.unit
def test_resolve_product_kit_folder_selects_nested_single_candidate(tmp_path) -> None:
    product_group = tmp_path / "Fort Knox"
    product_group.mkdir()
    (product_group / "Fort Knox (2832)").mkdir()

    result = resolver.resolve_product_kit_folder(
        kits_root=tmp_path,
        product_code="2832",
        product_name="Fort Knox",
    )

    assert result.folder_kit == "Fort Knox/Fort Knox (2832)"
    assert "Fort Knox/Fort Knox (2832)" in result.folder_kit_status


@pytest.mark.unit
def test_resolve_product_kit_folder_matches_code_and_name_in_full_path(tmp_path) -> None:
    product_group = tmp_path / "Unit Linked (7698)"
    product_group.mkdir()
    (product_group / "Unit Linked Daily").mkdir()
    (product_group / "Unit Linked Monthly").mkdir()

    result = resolver.resolve_product_kit_folder(
        kits_root=tmp_path,
        product_code="7698",
        product_name="Unit Linked Daily",
    )

    assert result.folder_kit == "Unit Linked (7698)/Unit Linked Daily"


@pytest.mark.unit
def test_resolve_product_kit_folder_matches_composite_code_marker(tmp_path) -> None:
    folder = tmp_path / "Fort Knox" / "Bundle Fort Knox 6+12 (8928+8929)"
    folder.mkdir(parents=True)

    result = resolver.resolve_product_kit_folder(
        kits_root=tmp_path,
        product_code="8928",
        product_name="Bundle Fort Knox 6+12",
    )

    assert result.folder_kit == "Fort Knox/Bundle Fort Knox 6+12 (8928+8929)"


@pytest.mark.unit
def test_resolve_product_kit_folder_selects_first_name_match(tmp_path) -> None:
    (tmp_path / "Archive (2832)").mkdir()
    (tmp_path / "Fort Knox (2832)").mkdir()

    result = resolver.resolve_product_kit_folder(
        kits_root=tmp_path,
        product_code="2832",
        product_name="Fort Knox",
    )

    assert result.folder_kit == "Fort Knox (2832)"


@pytest.mark.unit
def test_resolve_product_kit_folder_selects_first_candidate_without_name_match(tmp_path) -> None:
    (tmp_path / "B folder (2832)").mkdir()
    (tmp_path / "A folder (2832)").mkdir()

    result = resolver.resolve_product_kit_folder(
        kits_root=tmp_path,
        product_code="2832",
        product_name="Fort Knox",
    )

    assert result.folder_kit == "A folder (2832)"


@pytest.mark.unit
def test_resolve_product_kit_folder_requires_exact_code_marker(tmp_path) -> None:
    (tmp_path / "Fort Knox (283)").mkdir()
    (tmp_path / "Fort Knox (28320)").mkdir()
    (tmp_path / "Fort Knox (12832)").mkdir()

    result = resolver.resolve_product_kit_folder(
        kits_root=tmp_path,
        product_code="2832",
        product_name="Fort Knox",
    )

    assert result.folder_kit == resolver.NOT_FOUND_VALUE


@pytest.mark.unit
def test_latest_date_from_name_ignores_invalid_dates() -> None:
    assert resolver.dates_from_name("file 31.02.26 and 08.04.26.pdf") == [date(2026, 4, 8)]


@pytest.mark.unit
def test_resolve_product_input_date_uses_folder_date_first(tmp_path) -> None:
    folder = tmp_path / "Fort Knox" / "Fort Knox 1 year 12,7% (8914) 20.05.26"
    folder.mkdir(parents=True)
    (folder / "presenter 25.05.26.pdf").write_text("x", encoding="utf-8")

    result = resolver.resolve_product_input_date_from_kit(
        kits_root=tmp_path,
        folder_kit="Fort Knox/Fort Knox 1 year 12,7% (8914) 20.05.26",
    )

    assert result == date(2026, 5, 20)


@pytest.mark.unit
def test_resolve_product_input_date_uses_latest_nested_file_date(tmp_path) -> None:
    folder = tmp_path / "DSZH" / "Protected shares 5 years (8542)"
    nested = folder / "mobile"
    nested.mkdir(parents=True)
    (folder / "old 08.04.26.pdf").write_text("x", encoding="utf-8")
    (nested / "Protected shares 5 years (8542) mobile presenter 20.05.26.pdf").write_text(
        "x",
        encoding="utf-8",
    )

    result = resolver.resolve_product_input_date_from_kit(
        kits_root=tmp_path,
        folder_kit="DSZH/Protected shares 5 years (8542)",
    )

    assert result == date(2026, 5, 20)
