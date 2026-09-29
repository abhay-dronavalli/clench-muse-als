"""Navigation and action policy shared by discovery and the final click check."""
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
import unicodedata

import yaml

CONFIG = Path(__file__).resolve().parents[2] / "data" / "computer.yaml"


@dataclass(frozen=True)
class Policy:
    domains: tuple[str, ...]
    blocked_words: tuple[str, ...]
    start_url: str

    @classmethod
    def load(cls, start_url: str) -> "Policy":
        data = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
        return cls(tuple(data["domains"]), tuple(data["blocked_words"]), start_url)

    def allows_url(self, url: str) -> bool:
        try:
            parsed = urlsplit(url)
            if parsed.username or parsed.password:
                return False
            local = urlsplit(self.start_url)
            if (parsed.scheme, parsed.netloc, parsed.path) == (local.scheme, local.netloc, local.path):
                return not parsed.query
            return (parsed.scheme == "https" and parsed.port in (None, 443)
                    and any(parsed.hostname == d or (d != "open.spotify.com" and
                            (parsed.hostname or "").endswith("." + d)) for d in self.domains))
        except ValueError:
            return False

    def allows_label(self, label: str) -> bool:
        folded = unicodedata.normalize("NFKD", label).casefold()
        return not any(word in folded for word in self.blocked_words)
