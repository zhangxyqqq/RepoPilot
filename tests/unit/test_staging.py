from pathlib import Path

import pytest

from repopilot.sandbox.staging import StagingError, stage_repository


def test_staging_copies_regular_files_and_excludes_secrets(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    (source / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
    git_directory = source / ".git"
    git_directory.mkdir()
    (git_directory / "config").write_text("credential = secret\n", encoding="utf-8")

    destination = stage_repository(source, tmp_path / "staged")

    assert (destination / "module.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert not (destination / ".env").exists()
    assert not (destination / ".git").exists()


def test_staging_rejects_symbolic_links(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    target = tmp_path / "outside"
    target.write_text("secret", encoding="utf-8")
    (source / "link").symlink_to(target)

    with pytest.raises(StagingError, match="symbolic links"):
        stage_repository(source, tmp_path / "staged")
