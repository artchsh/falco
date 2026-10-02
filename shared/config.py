"""The non-secret launcher configuration model.

A :class:`LauncherConfig` holds everything a generated launcher needs *except*
the password: host, port, username and a ``credential_id`` (the keyring service
name under which the password is stored on the local machine).

The password is deliberately **never** part of this model, so it can be embedded
in a generated executable, serialised to JSON, or logged without leaking a
secret.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from shared.errors import ConfigError

DEFAULT_SSH_PORT = 22

# Bump when the on-disk / embedded schema changes in a breaking way.
CONFIG_SCHEMA_VERSION = 2


def default_credential_id(launcher_name: str, username: str, host: str, port: int) -> str:
    """Return the canonical keyring service name for a launcher.

    The identifier is a *label*, not a secret. It only needs to be stable and
    unique per (launcher, account, host) so repeated runs find the same stored
    password.
    """

    return f"falco:{launcher_name}:{username}@{host}:{port}"


@dataclass(frozen=True)
class LauncherConfig:
    """Immutable, non-secret configuration for a single launcher."""

    launcher_name: str
    host: str
    username: str
    port: int = DEFAULT_SSH_PORT
    credential_id: str = ""
    schema_version: int = CONFIG_SCHEMA_VERSION
    auth_method: str = "password"
    encrypted_private_key: str | None = field(default=None, repr=False)
    requires_vpn: bool = False

    @classmethod
    def create(
        cls,
        *,
        launcher_name: str,
        host: str,
        username: str,
        port: int = DEFAULT_SSH_PORT,
        credential_id: str | None = None,
        auth_method: str = "password",
        encrypted_private_key: str | None = None,
        requires_vpn: bool = False,
    ) -> "LauncherConfig":
        """Validate inputs and build a config, deriving ``credential_id`` if absent."""

        for label, value in (("Launcher name", launcher_name), ("SSH host", host), ("SSH username", username)):
            if not isinstance(value, str):
                raise ConfigError(f"{label} must be text.")
        if credential_id is not None and not isinstance(credential_id, str):
            raise ConfigError("Credential ID must be text.")
        if not isinstance(requires_vpn, bool):
            raise ConfigError("VPN requirement must be a boolean.")
        if auth_method not in ("password", "private_key"):
            raise ConfigError("Choose password or private-key authentication.")
        if auth_method == "private_key":
            from shared.ssh_keys import validate_encrypted_private_key
            encrypted_private_key = validate_encrypted_private_key(encrypted_private_key)
        elif encrypted_private_key is not None:
            raise ConfigError("A private key requires private-key authentication.")
        name = launcher_name.strip()
        host_value = host.strip()
        user = username.strip()

        if not name:
            raise ConfigError("Launcher name must not be empty.")
        if not host_value:
            raise ConfigError("SSH host/IP must not be empty.")
        if not user:
            raise ConfigError("SSH username must not be empty.")
        if any(ch.isspace() or ord(ch) < 32 for ch in host_value) or "/" in host_value or "\\" in host_value:
            raise ConfigError("Enter a hostname or IP address without a URL, path, or whitespace.")
        if any(ord(ch) < 32 for ch in name + user):
            raise ConfigError("Launcher name and SSH username must not contain control characters.")
        if not isinstance(port, int) or isinstance(port, bool):
            raise ConfigError("SSH port must be an integer.")
        if not (1 <= port <= 65535):
            raise ConfigError(f"SSH port must be between 1 and 65535 (got {port}).")

        cred = (credential_id or "").strip() or default_credential_id(
            name, user, host_value, port
        )
        return cls(
            launcher_name=name,
            host=host_value,
            username=user,
            port=port,
            credential_id=cred,
            auth_method=auth_method,
            encrypted_private_key=encrypted_private_key,
            requires_vpn=requires_vpn,
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dict (never contains a password)."""

        return asdict(self)

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LauncherConfig":
        version = data.get("schema_version", 1)
        if type(version) is not int or version not in (1, 2):
            raise ConfigError("Unsupported configuration schema; rebuild with a current Falco editor.")
        if version == 1 and (data.get("auth_method", "password") != "password" or data.get("encrypted_private_key") is not None):
            raise ConfigError("Private-key authentication requires schema 2.")
        try:
            return cls.create(
                launcher_name=data["launcher_name"],
                host=data["host"],
                username=data["username"],
                port=data.get("port", DEFAULT_SSH_PORT),
                credential_id=data.get("credential_id"),
                auth_method=data.get("auth_method", "password"),
                encrypted_private_key=data.get("encrypted_private_key"),
                requires_vpn=data.get("requires_vpn", False),
            )
        except KeyError as exc:  # pragma: no cover - defensive
            raise ConfigError(f"Missing required config field: {exc.args[0]}") from exc
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"Invalid config value: {exc}") from exc

    @classmethod
    def from_json(cls, text: str) -> "LauncherConfig":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"Configuration is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ConfigError("Configuration JSON must be an object.")
        return cls.from_dict(data)

    @classmethod
    def load(cls, path: str | Path) -> "LauncherConfig":
        p = Path(path)
        try:
            text = p.read_text(encoding="utf-8")
        except OSError as exc:
            raise ConfigError(f"Could not read config file {p}: {exc}") from exc
        return cls.from_json(text)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(self.to_json(), encoding="utf-8")
