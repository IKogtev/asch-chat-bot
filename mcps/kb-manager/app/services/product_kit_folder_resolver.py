from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any


NOT_FOUND_VALUE = "не найдена"
DATE_IN_NAME_RE = re.compile(r"(?<!\d)(\d{1,2})\.(\d{1,2})\.(\d{2}|\d{4})(?!\d)")


@dataclass(frozen=True)
class ProductKitFolderResolution:
    folder_kit: str
    folder_kit_status: str
    resolved_from_file: bool = False
    source_root: str="products"


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_product_code(value: Any) -> str:
    text = _normalize_text(value)
    if re.fullmatch(r"\d+\.0+", text):
        return text.split(".", 1)[0]
    return text


def _folder_value(kits_root: Path, folder: Path) -> str:
    return folder.relative_to(kits_root).as_posix()


def _path_has_product_code(relative_path: str, code: str) -> bool:
    for match in re.finditer(r"\(([^)]*)\)", relative_path):
        if re.search(rf"(?<!\d){re.escape(code)}(?!\d)", match.group(1)):
            return True
    return False


def _path_has_product_name(relative_path: str, product_name: str) -> bool:
    normalized_name = _normalize_text(product_name)
    if not normalized_name:
        return False
    return normalized_name.casefold() in relative_path.casefold()


def _candidate_folders(kits_root: Path, code: str) -> list[Path]:
    return sorted(
        [
            item
            for item in kits_root.rglob("*")
            if item.is_dir()
            and _path_has_product_code(_folder_value(kits_root, item), code)
        ],
        key=lambda path: _folder_value(kits_root, path).casefold(),
    )

# обработка файлов кандидатов
def _candidate_files(
    kits_root: Path,
    code: str,
) -> list[Path]:
    return sorted(
        [
            item
            for item in kits_root.rglob("*")
            if item.is_file()
            and _path_has_product_code(
                _folder_value(
                    kits_root,
                    item,
                ),
                code,
            )
        ],
        key=lambda path: _folder_value(
            kits_root,
            path,
        ).casefold(),
    )

def _date_from_match(match: re.Match[str]) -> date | None:
    day = int(match.group(1))
    month = int(match.group(2))
    year = int(match.group(3))
    if year < 100:
        year += 2000
    try:
        return date(year, month, day)
    except ValueError:
        return None


def dates_from_name(value: Any) -> list[date]:
    text = _normalize_text(value)
    dates = []
    for match in DATE_IN_NAME_RE.finditer(text):
        parsed = _date_from_match(match)
        if parsed is not None:
            dates.append(parsed)
    return dates


def latest_date_from_name(value: Any) -> date | None:
    dates = dates_from_name(value)
    return max(dates) if dates else None


def resolve_product_input_date_from_kit(
    *,
    kits_root: Path,
    folder_kit: Any,
) -> date | None:
    folder_value = _normalize_text(folder_kit)
    if not folder_value or folder_value == NOT_FOUND_VALUE:
        return None

    folder_date = latest_date_from_name(folder_value)
    if folder_date is not None:
        return folder_date

    folder_path = kits_root / Path(*Path(folder_value).parts)
    if not folder_path.exists() or not folder_path.is_dir():
        return None

    file_dates = []
    for item in folder_path.rglob("*"):
        if item.is_file():
            file_dates.extend(dates_from_name(item.name))

    return max(file_dates) if file_dates else None


def resolve_product_kit_folder(
    *,
    kits_root: Path,
    product_code: Any,
    product_name: Any,
) -> ProductKitFolderResolution:
    code = _normalize_product_code(product_code)
    name = _normalize_text(product_name)

    if not code:
        return ProductKitFolderResolution(
            folder_kit=NOT_FOUND_VALUE,
            folder_kit_status=(
                f"code is empty; product_name={name!r}; "
                f"root={str(kits_root)!r}; result={NOT_FOUND_VALUE!r}"
            ),
        )

    if not kits_root.exists() or not kits_root.is_dir():
        return ProductKitFolderResolution(
            folder_kit=NOT_FOUND_VALUE,
            folder_kit_status=(
                f"root not found; code={code!r}; product_name={name!r}; "
                f"root={str(kits_root)!r}; result={NOT_FOUND_VALUE!r}"
            ),
        )

    candidates = _candidate_folders(kits_root, code)
    candidate_values = [_folder_value(kits_root, item) for item in candidates]

    if not candidates:

        file_candidates = _candidate_files(
            kits_root,
            code,
        )

        if file_candidates:

            selected_folder = _folder_value(
                kits_root,
                file_candidates[0].parent,
            )

            return ProductKitFolderResolution(
                folder_kit=selected_folder,
                folder_kit_status=(
                    f"resolved from file; "
                    f"code={code!r}; "
                    f"product_name={name!r}; "
                    f"files="
                    f"{[_folder_value(kits_root, x) for x in file_candidates]!r}; "
                    f"result={selected_folder!r}"
                ),
                resolved_from_file=True,
            )

        return ProductKitFolderResolution(
            folder_kit=NOT_FOUND_VALUE,
            folder_kit_status=(
                f"not found; "
                f"code={code!r}; "
                f"product_name={name!r}; "
                f"candidates=[]; "
                f"result={NOT_FOUND_VALUE!r}"
            ),
        )

    if len(candidates) == 1:
        selected = _folder_value(kits_root, candidates[0])
        return ProductKitFolderResolution(
            folder_kit=selected,
            folder_kit_status=(
                f"single candidate; code={code!r}; product_name={name!r}; "
                f"candidates={candidate_values!r}; result={selected!r}"
            ),
        )

    name_matches = [
        item
        for item in candidates
        if _path_has_product_name(_folder_value(kits_root, item), name)
    ]
    if name_matches:
        selected = _folder_value(kits_root, name_matches[0])
        return ProductKitFolderResolution(
            folder_kit=selected,
            folder_kit_status=(
                f"multiple candidates, selected by product name in path; "
                f"code={code!r}; product_name={name!r}; "
                f"candidates={candidate_values!r}; name_matches="
                f"{[_folder_value(kits_root, item) for item in name_matches]!r}; "
                f"result={selected!r}"
            ),
        )

    selected = _folder_value(kits_root, candidates[0])
    return ProductKitFolderResolution(
        folder_kit=selected,
        folder_kit_status=(
            f"multiple candidates, selected first; code={code!r}; "
            f"product_name={name!r}; candidates={candidate_values!r}; "
            f"result={selected!r}"
        ),
    )
