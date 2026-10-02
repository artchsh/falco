"""Validate an encrypted OpenSSH envelope without requesting its passphrase."""

import base64
import binascii
import struct

from shared.errors import ConfigError

MAX_KEY_BYTES = 128 * 1024


def validate_encrypted_private_key(text: str) -> str:
    if not isinstance(text, str) or len(text) > MAX_KEY_BYTES:
        raise ConfigError("Select an encrypted OpenSSH private key smaller than 128 KiB.")
    lines = text.strip().splitlines()
    if len(lines) < 3 or lines[0] != "-----BEGIN OPENSSH PRIVATE KEY-----" or lines[-1] != "-----END OPENSSH PRIVATE KEY-----":
        raise ConfigError("Select an encrypted OpenSSH private key, not a public key or certificate.")
    try:
        data = base64.b64decode("".join(lines[1:-1]), validate=True)
        if not data.startswith(b"openssh-key-v1\x00"):
            raise ValueError
        offset = 15

        def read_string() -> bytes:
            nonlocal offset
            length = struct.unpack_from(">I", data, offset)[0]
            offset += 4
            if length > len(data) - offset:
                raise ValueError
            value = data[offset:offset + length]
            offset += length
            return value

        cipher, kdf, options = read_string(), read_string(), read_string()
        if cipher == b"none" or kdf == b"none":
            raise ConfigError("The private key must be encrypted with a passphrase before embedding it.")
        if cipher not in (b"aes128-ctr", b"aes192-ctr", b"aes256-ctr", b"aes256-cbc") or kdf != b"bcrypt":
            raise ConfigError("Unsupported key encryption. Use ssh-keygen to save an OpenSSH key with AES encryption.")
        salt_len = struct.unpack_from(">I", options)[0]
        if salt_len < 16 or len(options) != salt_len + 8:
            raise ValueError
        rounds = struct.unpack_from(">I", options, 4 + salt_len)[0]
        if not 1 <= rounds <= 1024:
            raise ConfigError("Unsupported private-key KDF work factor; use 1–1024 bcrypt rounds.")
        count = struct.unpack_from(">I", data, offset)[0]
        offset += 4
        if count != 1 or not read_string() or not read_string() or offset != len(data):
            raise ValueError
    except (ValueError, struct.error, binascii.Error) as exc:
        raise ConfigError("The encrypted OpenSSH private key is malformed or truncated.") from exc
    return "\n".join(lines) + "\n"
