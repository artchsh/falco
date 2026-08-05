"""Shared pytest fixtures and path setup.

Ensures the repo root is importable so ``import launcher`` / ``import shared``
work when running ``pytest`` from anywhere without installing the package.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared.config import LauncherConfig  # noqa: E402


class FakeKeyring:
    """An in-memory stand-in for the :mod:`keyring` backend."""

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.store.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.store[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self.store.pop((service, username), None)


@pytest.fixture()
def config() -> LauncherConfig:
    return LauncherConfig.create(
        launcher_name="meks",
        host="192.0.2.10",
        username="deploy",
        port=2222,
    )


@pytest.fixture()
def fake_keyring() -> FakeKeyring:
    return FakeKeyring()
