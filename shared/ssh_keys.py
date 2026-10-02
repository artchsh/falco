"""Validate an encrypted OpenSSH envelope without requesting its passphrase."""

import base64
import binascii
import struct

from shared.errors import ConfigError

MAX_KEY_BYTES = 128 * 1024


def _validate_public_blob(data: bytes) -> None:
    offset = 0

    def read_string() -> bytes:
        nonlocal offset
        size = struct.unpack_from(">I", data, offset)[0]
        offset += 4
        if size > len(data) - offset:
            raise ValueError
        value = data[offset:offset + size]
        offset += size
        return value

    algorithm = read_string()
    if algorithm == b"ssh-ed25519":
        if len(read_string()) != 32:
            raise ValueError
    elif algorithm == b"ssh-rsa":
        for value in (read_string(), read_string()):
            # RSA fields are canonical, positive SSH mpints.
            if not value or value[0] & 0x80 or (len(value) > 1 and value[0] == 0 and not value[1] & 0x80):
                raise ValueError
    elif algorithm in (b"ecdsa-sha2-nistp256", b"ecdsa-sha2-nistp384", b"ecdsa-sha2-nistp521"):
        curve = read_string()
        if algorithm != b"ecdsa-sha2-" + curve:
            raise ValueError
        point = read_string()
        coordinate_size = {b"nistp256": 32, b"nistp384": 48, b"nistp521": 66}[curve]
        if not point or point[0] not in (2, 3, 4) or len(point) != 1 + coordinate_size * (2 if point[0] == 4 else 1):
            raise ValueError
    else:
        raise ConfigError("Unsupported private-key algorithm. Use an encrypted OpenSSH Ed25519, RSA or ECDSA key.")
    if offset != len(data):
        raise ValueError


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
        if count != 1:
            raise ValueError
        _validate_public_blob(read_string())
        ciphertext = read_string()
        if len(ciphertext) < 16 or len(ciphertext) % 16 or offset != len(data):
            raise ValueError
    except (ValueError, struct.error, binascii.Error) as exc:
        raise ConfigError("The encrypted OpenSSH private key is malformed or truncated.") from exc
    return "\n".join(lines) + "\n"
