"""Typed exception hierarchy shared across Falco.

Every error raised deliberately by Falco derives from :class:`FalcoError` so that
callers can present a single, clear message to the user instead of a raw
traceback.
"""

from __future__ import annotations


class FalcoError(Exception):
    """Base class for all Falco errors."""


class ConfigError(FalcoError):
    """The launcher/editor configuration is missing or invalid."""


class CredentialError(FalcoError):
    """A credential could not be retrieved from or stored in the OS keystore."""


class SSHConnectionError(FalcoError):
    """The SSH connection could not be established or authenticated."""


class RemoteCommandError(FalcoError):
    """A remote command could not be started or streamed."""
