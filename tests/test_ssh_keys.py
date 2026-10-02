from pathlib import Path

import pytest

from shared.config import LauncherConfig
from shared.errors import ConfigError

KEY = (Path(__file__).parent / "fixtures/encrypted_ed25519").read_text()


def malformed_envelope(*, public=None, ciphertext=None):
    import base64
    import struct
    data = base64.b64decode("".join(KEY.strip().splitlines()[1:-1]))
    offset = 15
    for _ in range(3):
        size = struct.unpack_from(">I", data, offset)[0]
        offset += 4 + size
    header = data[:offset + 4]
    offset += 4
    size = struct.unpack_from(">I", data, offset)[0]
    old_public = data[offset + 4:offset + 4 + size]
    offset += 4 + size
    size = struct.unpack_from(">I", data, offset)[0]
    old_ciphertext = data[offset + 4:offset + 4 + size]
    fields = [old_public if public is None else public, old_ciphertext if ciphertext is None else ciphertext]
    blob = header + b"".join(struct.pack(">I", len(value)) + value for value in fields)
    encoded = base64.b64encode(blob).decode()
    return "-----BEGIN OPENSSH PRIVATE KEY-----\n" + encoded + "\n-----END OPENSSH PRIVATE KEY-----\n"


def test_embeds_encrypted_key_and_vpn_without_passphrase():
    cfg = LauncherConfig.create(launcher_name="key", host="h", username="u",
                               auth_method="private_key", encrypted_private_key=KEY,
                               requires_vpn=True)
    assert LauncherConfig.from_json(cfg.to_json()) == cfg
    assert cfg.schema_version == 2
    assert "falco-test-passphrase" not in cfg.to_json()


@pytest.mark.parametrize("key", ["private secret", KEY[:90], KEY.replace("b3Bl", "!!!!", 1)])
def test_invalid_key_is_rejected_without_echoing_contents(key):
    with pytest.raises(ConfigError) as caught:
        LauncherConfig.create(launcher_name="key", host="h", username="u",
                              auth_method="private_key", encrypted_private_key=key)
    assert key not in str(caught.value)


def test_plaintext_openssh_key_is_rejected(tmp_path):
    path = Path(__file__).parent / "fixtures/plaintext_ed25519"
    with pytest.raises(ConfigError, match="encrypted"):
        LauncherConfig.create(launcher_name="key", host="h", username="u",
                              auth_method="private_key", encrypted_private_key=path.read_text())


@pytest.mark.parametrize("key", [malformed_envelope(public=b"not-a-public-key"), malformed_envelope(ciphertext=b"x"), malformed_envelope(ciphertext=b""), malformed_envelope(public=b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x01x")])
def test_malformed_public_key_and_ciphertext_are_rejected(key):
    with pytest.raises(ConfigError):
        LauncherConfig.create(launcher_name="key", host="h", username="u", auth_method="private_key", encrypted_private_key=key)
