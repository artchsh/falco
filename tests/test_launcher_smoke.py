"""Exercise a real release binary when a native stub has been built locally."""
import json
import socket
import subprocess
from pathlib import Path

import pytest

from editor import builder
from shared.config import LauncherConfig


@pytest.fixture
def native_stub():
    available = builder.discover_stubs()
    stub = available.get(builder._current_os_key())
    if stub is None:
        pytest.skip("Build a native release stub to run binary smoke tests")
    return stub


def test_unconfigured_stub_reports_json_configuration_error(native_stub):
    result = subprocess.run([str(native_stub.resolve())], stdin=subprocess.DEVNULL, capture_output=True, timeout=10)
    assert result.returncode == 2
    error = json.loads(result.stderr)
    assert error["error"] == "CONFIGURATION_ERROR"
    assert error["action"]


@pytest.mark.parametrize("private_key", [False, True])
def test_generated_launcher_has_structured_network_error_without_prompt(native_stub, tmp_path, private_key):
    # Hold an unused port without listening so no real service receives secrets.
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        key = (Path(__file__).parent / "fixtures/encrypted_ed25519").read_text() if private_key else None
        config = LauncherConfig.create(launcher_name="smoke", host="127.0.0.1", username="tester",
                                       port=reserved.getsockname()[1], requires_vpn=True,
                                       auth_method="private_key" if private_key else "password", encrypted_private_key=key)
        output = builder.build_launcher(config, output_name="smoke", output_dir=tmp_path)
        result = subprocess.run([str(output.executable), "--accept-new-key", "echo smoke"],
                                stdin=subprocess.DEVNULL, capture_output=True, timeout=10)
    assert result.returncode == 4
    assert result.stdout == b""
    error = json.loads(result.stderr)
    # OS/firewall handling of a bound non-listening port may refuse or time out.
    assert error["error"] in ("CONNECTION_REFUSED", "CONNECTION_TIMEOUT")
    assert "Tailscale/WireGuard/VPN" in error["action"]
    assert "OPENSSH" not in result.stderr.decode()
