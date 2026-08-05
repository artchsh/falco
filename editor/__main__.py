"""Entry point for ``python -m editor`` and the ``falco-editor`` script."""

from __future__ import annotations

import sys


def main() -> int:
    from editor.app import launch

    return launch()


if __name__ == "__main__":
    sys.exit(main())
