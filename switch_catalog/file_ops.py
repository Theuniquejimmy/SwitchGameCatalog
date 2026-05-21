from __future__ import annotations

import shutil
from pathlib import Path


def unique_destination(folder: str | Path, filename: str) -> Path:
    root = Path(folder)
    candidate = root / filename
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    index = 1
    while True:
        next_candidate = root / f"{stem} ({index}){suffix}"
        if not next_candidate.exists():
            return next_candidate
        index += 1


def move_file_to_folder(source: str | Path, folder: str | Path) -> Path:
    source_path = Path(source)
    target_root = Path(folder)
    target_root.mkdir(parents=True, exist_ok=True)
    if source_path.parent.resolve() == target_root.resolve():
        return source_path
    destination = unique_destination(target_root, source_path.name)
    shutil.move(str(source_path), str(destination))
    return destination


def delete_file_if_present(path: str | Path) -> bool:
    file_path = Path(path)
    if not file_path.exists():
        return False
    file_path.unlink()
    return True
