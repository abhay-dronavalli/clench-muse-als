"""Search text validation and the patient-owned search panel; no browser or AI work."""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from core.computer.keyboard import Keyboard

MAX_QUERY = 40

@lru_cache(maxsize=1)
def _defaults():
    path = Path(__file__).resolve().parents[2] / "data" / "computer_suggestions.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    for site in ("youtube", "spotify", "google"):
        for lang in ("en", "es"):
            values = data[site][lang]
            if not isinstance(values, list) or len(values) < 15 or any(clean_query(q) != q for q in values):
                raise ValueError(f"Invalid computer search defaults: {site}/{lang}")
    return data


def fallback_queries(site: str, lang: str) -> list[str]:
    return list(_defaults()[site][lang])


def clean_query(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(unicodedata.normalize("NFKC", value).split())
    if not text or len(text) > MAX_QUERY or any(unicodedata.category(c).startswith("C") for c in text):
        return None
    # Search text only, including when supplied by history or the scanning keyboard.
    if re.search(r"(?i)([a-z][\w+.-]*:|www\.|[\w-]+\.[\w-]+|[@\\/])", text):
        return None
    return text


def site_for(url: str) -> str | None:
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    if host == "youtube.com" or host.endswith(".youtube.com"):
        return "youtube"
    if host == "open.spotify.com":
        return "spotify"
    if host in ("google.com", "www.google.com") and parsed.path in ("", "/", "/search", "/webhp"):
        return "google"
    return None


def unique_queries(values, policy) -> list[str]:
    out, seen = [], set()
    for value in values:
        text = clean_query(value)
        if text and policy.allows_label(text) and text.casefold() not in seen:
            out.append(text)
            seen.add(text.casefold())
    return out


class SearchPanel:
    def __init__(self, queries: list[str], lang: str = "es"):
        self.lang = lang
        self.pages = [queries[:5]]
        self.page = 0
        self.mode = "search"
        self.keyboard = Keyboard(queries, lang)

    def label(self, en: str, es: str) -> str:
        return es if self.lang == "es" else en

    @property
    def shown(self) -> tuple[str, ...]:
        return tuple(q for page in self.pages for q in page)

    def items(self) -> list[tuple[str, str]]:
        if self.mode == "keyboard":
            return self.keyboard.items()
        return [(f"query:{i}", text) for i, text in enumerate(self.pages[self.page])] + [
            ("other", self.label("Other...", "Otro...")),
            ("keyboard", self.label("Keyboard", "Teclado")),
            ("cancel", self.label("Cancel", "Cancelar")),
        ]

    def more(self, queries: list[str]) -> None:
        if self.page == 2:
            self.page = 0
        elif self.page + 1 < len(self.pages):
            self.page += 1
        else:
            seen = {q.casefold() for q in self.shown}
            fresh = [q for q in queries if q.casefold() not in seen][:5]
            if fresh:
                self.pages.append(fresh)
                self.page += 1
            else:
                self.page = 0
