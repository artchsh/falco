"""Tests for the launcher configuration model and its (non-secret) invariants."""

from __future__ import annotations

import pytest

from shared.config import DEFAULT_SSH_PORT, LauncherConfig, default_credential_id
from shared.errors import ConfigError


def test_create_fills_default_port_and_credential_id() -> None:
    cfg = LauncherConfig.create(launcher_name="server-client-X", host="host", username="deploy")
    assert cfg.port == DEFAULT_SSH_PORT
    assert cfg.credential_id == default_credential_id("server-client-X", "deploy", "host", 22)


def test_create_trims_whitespace() -> None:
    cfg = LauncherConfig.create(launcher_name="  server-client-X ", host=" host ", username=" deploy ")
    assert (cfg.launcher_name, cfg.host, cfg.username) == ("server-client-X", "host", "deploy")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"launcher_name": "", "host": "h", "username": "u"},
        {"launcher_name": "n", "host": "", "username": "u"},
        {"launcher_name": "n", "host": "h", "username": ""},
    ],
)
def test_create_rejects_empty_fields(kwargs: dict[str, str]) -> None:
    with pytest.raises(ConfigError):
        LauncherConfig.create(**kwargs)


@pytest.mark.parametrize("port", [0, -1, 65536, 99999])
def test_create_rejects_out_of_range_port(port: int) -> None:
    with pytest.raises(ConfigError):
        LauncherConfig.create(launcher_name="n", host="h", username="u", port=port)


def test_create_rejects_boolean_port() -> None:
    with pytest.raises(ConfigError):
        LauncherConfig.create(launcher_name="n", host="h", username="u", port=True)  # type: ignore[arg-type]


def test_roundtrip_json_preserves_fields() -> None:
    cfg = LauncherConfig.create(launcher_name="server-client-X", host="h", username="u", port=2200)
    restored = LauncherConfig.from_json(cfg.to_json())
    assert restored == cfg


def test_serialised_config_never_contains_a_password() -> None:
    cfg = LauncherConfig.create(launcher_name="server-client-X", host="h", username="u")
    text = cfg.to_json().lower()
    assert '"password":' not in text
    assert set(cfg.to_dict()) == {
        "launcher_name",
        "host",
        "username",
        "port",
        "credential_id",
        "schema_version",
        "auth_method",
        "encrypted_private_key",
        "requires_vpn",
    }


def test_from_json_rejects_non_object() -> None:
    with pytest.raises(ConfigError):
        LauncherConfig.from_json("[1, 2, 3]")


def test_from_json_rejects_invalid_json() -> None:
    with pytest.raises(ConfigError):
        LauncherConfig.from_json("{not json")


def test_config_is_immutable() -> None:
    cfg = LauncherConfig.create(launcher_name="server-client-X", host="h", username="u")
    with pytest.raises(Exception):
        cfg.host = "evil"  # type: ignore[misc]


def test_legacy_config_defaults_to_password():
    cfg = LauncherConfig.from_json('{"launcher_name":"n","host":"h","username":"u","schema_version":1}')
    assert cfg.auth_method == "password"
    assert cfg.requires_vpn is False


@pytest.mark.parametrize("field,value", [
    ("schema_version", 99), ("port", True), ("port", "22"),
    ("host", 123), ("requires_vpn", "false"), ("auth_method", "unknown"),
    ("encrypted_private_key", "unencrypted"),
])
def test_rejects_invalid_serialized_fields(field, value):
    data = {"launcher_name": "n", "host": "h", "username": "u", field: value}
    with pytest.raises(ConfigError):
        LauncherConfig.from_dict(data)
