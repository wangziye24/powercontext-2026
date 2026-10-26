"""Runtime locations of the SDD context module.

Working data (the seekdb store and the PowerContext home) stays outside the
repository. Deliverable evidence (transcripts, logs, diffs) is written into the
``minimax/artifacts`` directory of the repository so it can be submitted.
"""

from __future__ import annotations

import os
from pathlib import Path

ENV_HOME = "SDDCTX_HOME"
ENV_ARTIFACTS = "SDDCTX_ARTIFACTS"


def home() -> Path:
    """Return the writable working directory of the module."""

    raw = os.environ.get(ENV_HOME)
    base = Path(raw).expanduser() if raw else Path.home() / ".sddctx"
    base.mkdir(parents=True, exist_ok=True)
    return base


def seekdb_path() -> Path:
    """Return the embedded seekdb store location."""

    return home() / "seekdb"


def powercontext_home() -> Path:
    """Return the PowerContext home that keeps the task state."""

    target = home() / "powercontext"
    target.mkdir(parents=True, exist_ok=True)
    return target


def package_root() -> Path:
    """Return the ``minimax`` package directory that ships the module."""

    return Path(__file__).resolve().parents[2]


def artifacts_dir() -> Path:
    """Return the directory that collects submission evidence."""

    raw = os.environ.get(ENV_ARTIFACTS)
    target = Path(raw).expanduser() if raw else package_root() / "artifacts"
    target.mkdir(parents=True, exist_ok=True)
    return target
