"""Root ignore rules keep local secrets out of git and leave room for the RDS CA bundle."""

from __future__ import annotations

import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def _ignored(relative: str) -> bool:
    result = subprocess.run(
        ["git", "check-ignore", "-q", relative],
        cwd=_ROOT,
        check=False,
    )
    return result.returncode == 0


def test_backend_env_is_ignored() -> None:
    assert _ignored("backend/.env")


def test_loose_pem_is_ignored_and_rds_bundle_path_is_not() -> None:
    assert _ignored("notes.pem")
    assert not _ignored("backend/certs/rds-ca.pem")
