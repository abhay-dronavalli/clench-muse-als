"""Errors shared by every provider."""

from __future__ import annotations


class ProviderError(Exception):
    """A provider request failed. `pause_s` asks the Suggester to stop calling the AI for that long
    (bad key, rate limit), so every request does not fail the same way."""

    def __init__(self, detail: str, *, pause_s: float | None = None) -> None:
        super().__init__(detail)
        self.pause_s = pause_s
