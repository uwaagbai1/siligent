"""Errors raised by Siligent."""

from __future__ import annotations


class SiligentError(Exception):
    """Base class for Siligent errors."""


class SafetyRefusal(SiligentError):
    """An action was refused by the safety gate before it reached the hardware.

    `rule` names the rule that fired, so an agent can tell refusals apart and
    explain them, instead of retrying blindly.
    """

    def __init__(self, rule: str, reason: str, **detail: object) -> None:
        super().__init__(f"refused by rule '{rule}': {reason}")
        self.rule = rule
        self.reason = reason
        self.detail = detail

    def to_dict(self) -> dict:
        return {"ok": False, "refused": True, "rule": self.rule, "reason": self.reason, "detail": self.detail}


class HardwareError(SiligentError):
    """The hardware did not respond as expected."""


class ImageError(SiligentError):
    """A firmware image could not be read."""
