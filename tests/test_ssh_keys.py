from pathlib import Path

import pytest

from shared.config import LauncherConfig
from shared.errors import ConfigError

KEY = (Path(__file__).parent / "fixtures/encrypted_ed25519").read_text()


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
