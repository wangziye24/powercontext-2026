"""sddctx - SDD context module for the MiniMax x OceanBase challenge.

The module keeps engineering material (specifications, code symbols, tests) inside
an embedded seekdb instance, selects the material a development task needs, and
hands the selection to a MiniMax-backed coding assistant. Task progress is kept
in PowerContext so a restarted module can resume from the saved state.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
