"""Tests for credential lookup, prompting and storage."""

from __future__ import annotations

import pytest

from launcher import credentials
from shared.config import LauncherConfig
from shared.errors import CredentialError


def test_get_password_returns_none_when_absent(config: LauncherConfig, fake_keyring) -> None:
    assert credentials.get_password(config, keyring_backend=fake_keyring) is None


def test_store_then_get_roundtrip(config: LauncherConfig, fake_keyring) -> None:
    credentials.store_password(config, "s3cret", keyring_backend=fake_keyring)
    assert credentials.get_password(config, keyring_backend=fake_keyring) == "s3cret"


def test_resolve_returns_existing_without_prompting(config: LauncherConfig, fake_keyring) -> None:
    credentials.store_password(config, "stored", keyring_backend=fake_keyring)

    def _fail_prompt(_label: str) -> str:  # pragma: no cover
        raise AssertionError("prompt should not be called when a password exists")

    assert credentials.resolve_password(
        config, keyring_backend=fake_keyring, prompt=_fail_prompt
    ) == "stored"


def test_resolve_prompts_and_stores_on_first_use(config: LauncherConfig, fake_keyring) -> None:
    prompts: list[str] = []

    def _prompt(label: str) -> str:
        prompts.append(label)
        return "typed-password"

    result = credentials.resolve_password(
        config, keyring_backend=fake_keyring, prompt=_prompt
    )
    assert result == "typed-password"
    assert prompts, "user should have been prompted exactly once"
    # And it must be persisted for next time.
    assert credentials.get_password(config, keyring_backend=fake_keyring) == "typed-password"


def test_resolve_stores_under_credential_id_and_username(config: LauncherConfig, fake_keyring) -> None:
    credentials.resolve_password(
        config, keyring_backend=fake_keyring, prompt=lambda _l: "pw"
    )
    assert (config.credential_id, config.username) in fake_keyring.store


def test_empty_password_is_rejected(config: LauncherConfig, fake_keyring) -> None:
    with pytest.raises(CredentialError):
        credentials.resolve_password(
            config, keyring_backend=fake_keyring, prompt=lambda _l: ""
        )


def test_delete_password_removes_entry(config: LauncherConfig, fake_keyring) -> None:
    credentials.store_password(config, "pw", keyring_backend=fake_keyring)
    credentials.delete_password(config, keyring_backend=fake_keyring)
    assert credentials.get_password(config, keyring_backend=fake_keyring) is None


def test_backend_errors_are_wrapped(config: LauncherConfig) -> None:
    class Boom:
        def get_password(self, *_a: object) -> str | None:
            raise RuntimeError("backend down")

        def set_password(self, *_a: object) -> None:  # pragma: no cover
            raise RuntimeError("backend down")

        def delete_password(self, *_a: object) -> None:  # pragma: no cover
            raise RuntimeError("backend down")

    with pytest.raises(CredentialError):
        credentials.get_password(config, keyring_backend=Boom())
