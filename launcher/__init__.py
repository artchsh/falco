"""The Falco launcher runtime.

This package is what a generated launcher executable runs. It is intentionally
free of any embedded configuration: the config is supplied by a tiny generated
entry script (see ``editor/templates.py``) or, during development, via the
``FALCO_CONFIG_JSON`` environment variable (see ``launcher/__main__.py``).
"""

from launcher.runtime import run

__all__ = ["run"]
