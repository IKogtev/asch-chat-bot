from pathlib import Path
from typing import Any

SERVICE_FILE_NAMES = {"thumbs.db", "desktop.ini", ".ds_store"}
DEFAULT_MAX_FILES = 10
DEFAULT_MAX_FILE_SIZE_MB = 50


def _settings():
    from bot.services.config import Settings

    return Settings


def _is_hidden_or_service_file(path: Path) -> bool:
    return path.name.startswith(".") or path.name.lower() in SERVICE_FILE_NAMES


def _resolve_inside(root: Path, child: Path) -> tuple[Path, Path]:
    root_resolved = root.resolve()
    child_resolved = child.resolve()

    if child_resolved != root_resolved and root_resolved not in child_resolved.parents:
        raise ValueError("Product kit path escapes PRODUCT_KITS_ROOT")

    return root_resolved, child_resolved


def get_product_kit(
    product_code: str,
    product_name: str | None = None,
    folder_kit: str | None = None,
    *,
    root: Path | None = None,
    max_files: int | None = None,
    max_file_size_mb: int | None = None,
) -> dict[str, Any]:
    normalized_product_code = str(product_code or "").strip()
    normalized_product_name = str(product_name or "").strip()
    normalized_folder_kit = str(folder_kit or "").strip()

    if not normalized_product_code:
        return {
            "status": "invalid_request",
            "product_code": normalized_product_code,
            "product_name": normalized_product_name,
            "message": "Не удалось определить ID продукта для комплекта.",
            "files": [],
            "skipped_files": [],
        }

    if root is None:
        settings = _settings()
        kits_root = settings.PRODUCT_KITS_ROOT
        default_max_files = settings.PRODUCT_KITS_MAX_FILES
        default_max_file_size_mb = settings.PRODUCT_KITS_MAX_FILE_SIZE_MB
    else:
        kits_root = Path(root)
        default_max_files = DEFAULT_MAX_FILES
        default_max_file_size_mb = DEFAULT_MAX_FILE_SIZE_MB

    limit = max_files if max_files is not None else default_max_files
    max_size_mb = max_file_size_mb if max_file_size_mb is not None else default_max_file_size_mb
    max_size_bytes = max(int(max_size_mb), 0) * 1024 * 1024
    folder_name = normalized_folder_kit or normalized_product_code

    try:
        root_resolved, folder = _resolve_inside(kits_root, kits_root / folder_name)
    except ValueError:
        return {
            "status": "invalid_request",
            "product_code": normalized_product_code,
            "product_name": normalized_product_name,
            "message": "Некорректный путь комплекта продукта.",
            "files": [],
            "skipped_files": [],
        }

    if not folder.exists() or not folder.is_dir():
        return {
            "status": "not_found",
            "product_code": normalized_product_code,
            "product_name": normalized_product_name,
            "folder": str(folder),
            "message": "Комплект для продукта пока не загружен.",
            "files": [],
            "skipped_files": [],
        }

    files: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for item in sorted(folder.iterdir(), key=lambda path: path.name.lower()):
        if not item.is_file():
            continue

        if _is_hidden_or_service_file(item):
            skipped.append({"path": str(item), "reason": "hidden_or_service"})
            continue

        try:
            _, file_path = _resolve_inside(root_resolved, item)
            size = file_path.stat().st_size
        except OSError:
            skipped.append({"path": str(item), "reason": "stat_error"})
            continue

        if size > max_size_bytes:
            skipped.append({"path": str(file_path), "reason": "too_large", "size": size})
            continue

        if limit >= 0 and len(files) >= limit:
            skipped.append({"path": str(file_path), "reason": "too_many_files", "size": size})
            continue

        files.append({"path": str(file_path), "name": file_path.name, "size": size})

    if not files:
        return {
            "status": "empty",
            "product_code": normalized_product_code,
            "product_name": normalized_product_name,
            "folder": str(folder),
            "message": "Папка комплекта продукта есть, но подходящих файлов в ней нет.",
            "files": [],
            "skipped_files": skipped,
        }

    return {
        "status": "ok",
        "product_code": normalized_product_code,
        "product_name": normalized_product_name,
        "folder": str(folder),
        "message": "Комплект продукта найден.",
        "files": files,
        "skipped_files": skipped,
    }
