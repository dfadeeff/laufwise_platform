"""Domain error types. The API layer maps these to HTTP responses."""

from __future__ import annotations


class PlatformError(Exception):
    """Base class for all platform errors."""


class NotFoundError(PlatformError):
    """A requested resource (runbook, run, ...) does not exist."""


class StateUnavailableError(PlatformError):
    """The system of record could not be read — a first-class blocking outcome,
    never a crash. The runtime must block the step, not proceed on stale state."""


class RuntimeNotConfiguredError(PlatformError):
    """The control-plane primitive (laufwise) is not installed/wired yet."""