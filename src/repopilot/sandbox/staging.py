from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

from repopilot.sandbox.policy import is_sensitive_name


class StagingError(ValueError):
    pass


def stage_repository(
    source: Path,
    destination: Path,
    *,
    max_files: int = 10_000,
    max_bytes: int = 100 * 1024 * 1024,
) -> Path:
    source = source.expanduser().resolve(strict=True)
    destination = destination.expanduser().resolve(strict=False)
    if not source.is_dir():
        raise StagingError("repository path must be a directory")
    if source == destination or source in destination.parents:
        raise StagingError("staging destination cannot be inside the source repository")
    if destination.exists() and any(destination.iterdir()):
        raise StagingError("staging destination must be empty")
    destination.mkdir(parents=True, exist_ok=True)

    file_count = 0
    byte_count = 0
    for root, directory_names, file_names in os.walk(source, followlinks=False):
        root_path = Path(root)
        relative_root = root_path.relative_to(source)
        kept_directories: list[str] = []
        for name in sorted(directory_names):
            candidate = root_path / name
            if is_sensitive_name(name) or name in {"__pycache__", ".pytest_cache"}:
                continue
            if candidate.is_symlink():
                raise StagingError(f"symbolic links are not accepted in the MVP: {candidate}")
            kept_directories.append(name)
            target_directory = destination / relative_root / name
            target_directory.mkdir(parents=True, exist_ok=True)
            target_directory.chmod(0o777)
        directory_names[:] = kept_directories

        for name in sorted(file_names):
            if is_sensitive_name(name) or name.endswith((".pyc", ".pyo")):
                continue
            candidate = root_path / name
            if candidate.is_symlink():
                raise StagingError(f"symbolic links are not accepted in the MVP: {candidate}")
            mode = candidate.stat().st_mode
            if not stat.S_ISREG(mode):
                raise StagingError(f"only regular files are accepted: {candidate}")
            size = candidate.stat().st_size
            file_count += 1
            byte_count += size
            if file_count > max_files or byte_count > max_bytes:
                raise StagingError("repository exceeds staging size limits")
            target = destination / relative_root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(candidate, target)
            target.chmod(0o666)

    destination.chmod(0o777)
    return destination
