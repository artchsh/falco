"""Shared pytest path setup.

Ensures the repo root is importable so ``import editor`` / ``import shared``
work when running ``pytest`` from anywhere without installing the package.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
