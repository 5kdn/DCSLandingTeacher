"""The tuning YAMLs must survive a configured path that is not there.

Production ran for weeks on the built-in code defaults because an empty bind
mount shadowed ``/app/config``: ``load_grading_config()`` found no file, fell
back without a word, and the LSO factor table -- which lives only in the YAML
-- was simply absent, so every carrier landing graded "OK". These tests pin
the two guards added for that: say so in the log, and keep a copy the mount
cannot reach.
"""

from __future__ import annotations

import logging
import shutil

from pathlib import Path

from app.grading import packaged
from app.grading.config import load_grading_config
from app.grading.packaged import packaged_config, resolve_config_path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_a_missing_configured_path_is_reported_not_swallowed(caplog, tmp_path) -> None:
    """The failure that shipped was silent. It must never be silent again."""
    missing = tmp_path / "config" / "grading.yaml"
    with caplog.at_level(logging.WARNING, logger="app.grading.packaged"):
        resolve_config_path(missing, "grading.yaml")
    assert any(str(missing) in record.getMessage() for record in caplog.records), (
        "a configured-but-absent config path must produce a WARNING naming it"
    )


def test_the_configured_file_wins_when_it_is_there(tmp_path) -> None:
    real = tmp_path / "grading.yaml"
    real.write_text("version: 1\n", encoding="utf-8")
    assert resolve_config_path(real, "grading.yaml") == real


def test_the_packaged_copy_is_used_when_the_mount_is_empty(tmp_path, caplog) -> None:
    """Reproduces production exactly: an empty directory mounted over the path.

    The packaged copy only exists in the built image, so it is staged here to
    exercise the same resolution the image gets.
    """
    empty_mount = tmp_path / "config"
    empty_mount.mkdir()

    staged = Path(packaged.__file__).parent / "defaults"
    created = not staged.exists()
    staged.mkdir(parents=True, exist_ok=True)
    shipped = staged / "grading.yaml"
    try:
        if not shipped.exists():
            shutil.copyfile(REPO_ROOT / "config" / "grading.yaml", shipped)
        with caplog.at_level(logging.WARNING, logger="app.grading.packaged"):
            resolved = resolve_config_path(empty_mount / "grading.yaml", "grading.yaml")
        assert resolved is not None and resolved.is_file()

        # And the point of all of it: the LSO factor table is present, so a
        # carrier landing can be graded on something other than "OK".
        config = load_grading_config(resolved)
        assert config.lso_grading["factors"], "the packaged copy must carry the factors"
        assert config.lso_grading["decision"]["cut_low_gs_deviation_m"] == -4.5
    finally:
        if shipped.exists():
            shipped.unlink()
        if created and staged.exists():
            staged.rmdir()


def test_no_packaged_copy_in_a_source_checkout() -> None:
    """In the repo the canonical YAML is config/; the packaged copy is a build
    artefact. If one appears in git, the two can drift."""
    assert packaged_config("grading.yaml") is None or not (
        REPO_ROOT / "backend" / "app" / "grading" / "defaults" / "grading.yaml"
    ).exists(), "config/*.yaml must have exactly one copy in git"


def test_the_image_build_copies_both_yamls_into_the_package() -> None:
    """The guard is only real if the build actually stages the files."""
    dockerfile = (REPO_ROOT / "docker" / "backend.Dockerfile").read_text(encoding="utf-8")
    assert "./app/grading/defaults/" in dockerfile
    assert "config/carriers.yaml" in dockerfile, (
        "carriers.yaml was missing from the image entirely, so no carrier "
        "approach ever had FLOLS geometry"
    )
    pyproject = (REPO_ROOT / "backend" / "pyproject.toml").read_text(encoding="utf-8")
    assert 'defaults/*.yaml' in pyproject, "package-data must ship the staged copy"
