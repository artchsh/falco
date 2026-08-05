"""Password retrieval and storage backed by the OS credential store.

The :mod:`keyring` library selects the native backend automatically:

* Windows  -> Windows Credential Manager
* macOS    -> Keychain
* Linux    -> Secret Service / KWallet (optional platform)

The password is only ever held in memory for the duration of a connection. It is
read from and written to the OS keystore under the launcher's ``credential_id``
service name. It is never written to disk by Falco, never placed on a command
line, and never exported as an environment variable.
"""

from __future__ import annotations

import getpass
from typing import Callable, Protocol

from shared.config import LauncherConfig
from shared.errors import CredentialError


class KeyringLike(Protocol):
    """The small slice of the :mod:`keyring` API that Falco uses.

    Declaring it as a Protocol lets tests inject a fake keystore without
    touching the real OS credential manager.
    """

    def get_password(self, service_name: str, username: str) -> str | None: ...

    def set_password(self, service_name: str, username: str, password: str) -> None: ...

    def delete_password(self, service_name: str, username: str) -> None: ...


def _default_keyring() -> KeyringLike:
    import keyring  # imported lazily so tests need no backend

    return keyring


PromptFunc = Callable[[str], str]


def get_password(config: LauncherConfig, *, keyring_backend: KeyringLike | None = None) -> str | None:
    """Return the stored password, or ``None`` if none has been saved yet."""

    backend = keyring_backend or _default_keyring()
    try:
        return backend.get_password(config.credential_id, config.username)
    except Exception as exc:  # keyring raises backend-specific errors
        raise CredentialError(
            f"Could not read the password from the OS credential store: {exc}"
        ) from exc


def store_password(
    config: LauncherConfig,
    password: str,
    *,
    keyring_backend: KeyringLike | None = None,
) -> None:
    """Persist ``password`` in the OS credential store."""

    backend = keyring_backend or _default_keyring()
    try:
        backend.set_password(config.credential_id, config.username, password)
    except Exception as exc:
        raise CredentialError(
            f"Could not save the password to the OS credential store: {exc}"
        ) from exc


def delete_password(config: LauncherConfig, *, keyring_backend: KeyringLike | None = None) -> None:
    """Remove any stored password (used by ``--reset-password``)."""

    backend = keyring_backend or _default_keyring()
    try:
        backend.delete_password(config.credential_id, config.username)
    except Exception:
        # Deleting a non-existent entry is not an error worth surfacing.
        pass


def prompt_for_password(config: LauncherConfig, *, prompt: PromptFunc = getpass.getpass) -> str:
    """Prompt for a password without echoing it. Raises if left blank."""

    label = f"SSH password for {config.username}@{config.host}: "
    password = prompt(label)
    if not password:
        raise CredentialError("No password entered; aborting.")
    return password


def resolve_password(
    config: LauncherConfig,
    *,
    keyring_backend: KeyringLike | None = None,
    prompt: PromptFunc = getpass.getpass,
) -> str:
    """Return a usable password, prompting for and storing one on first use.

    1. Try the OS credential store.
    2. If empty, prompt the user (no echo) and save the result for next time.
    """

    backend = keyring_backend or _default_keyring()
    existing = get_password(config, keyring_backend=backend)
    if existing:
        return existing

    password = prompt_for_password(config, prompt=prompt)
    store_password(config, password, keyring_backend=backend)
    return password
