import sys
from pathlib import Path


def _ensure_repo_utils_on_path() -> None:
    start = Path(__file__).resolve().parent
    for candidate in (start, *start.parents):
        utils_logger = candidate / "utils" / "logger.py"
        if utils_logger.is_file():
            root = str(candidate)
            if root not in sys.path:
                sys.path.insert(0, root)
            return


_ensure_repo_utils_on_path()
