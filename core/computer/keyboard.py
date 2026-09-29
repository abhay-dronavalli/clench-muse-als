"""Row-column keyboard and local word completions. No network or browser dependency."""
from __future__ import annotations

import re

LIMIT = 40
ROWS = (tuple("abcdef"), tuple("ghijkl"), tuple("mnñopq"), tuple("rstuvw"), tuple("xyz"),
        ("space", "delete", "done"))


class Keyboard:
    def __init__(self, candidates, lang="es"):
        self.candidates = list(candidates)
        self.lang = lang
        self.draft = ""
        self.row = None

    def label(self, key):
        labels = {"space": ("Space", "Espacio"), "delete": ("Delete", "Borrar"), "done": ("Done", "Listo")}
        return labels[key][self.lang == "es"] if key in labels else key

    def completions(self):
        prefix = self.draft.rsplit(" ", 1)[-1].casefold()
        if not prefix:
            return []
        stem = self.draft[:len(self.draft) - len(self.draft.rsplit(" ", 1)[-1])]
        words, seen = [], set()
        for query in self.candidates:
            for word in re.findall(r"[^\W\d_]+", query):
                key = word.casefold()
                if key.startswith(prefix) and key != prefix and key not in seen and len(stem + word) <= LIMIT:
                    words.append(word)
                    seen.add(key)
        return words[:3]

    def items(self):
        if self.row is not None:
            return [("key:" + key, self.label(key)) for key in ROWS[self.row]]
        return [(f"complete:{i}", word) for i, word in enumerate(self.completions())] + [
            (f"row:{i}", "   ".join(self.label(key) for key in row)) for i, row in enumerate(ROWS)]

    def pick(self, key):
        """Return draft only for Done; every other pick returns to the row scan after editing."""
        if key.startswith("row:"):
            self.row = int(key.split(":")[1])
            return None
        if key.startswith("complete:"):
            word = self.completions()[int(key.split(":")[1])]
            prefix = self.draft.rsplit(" ", 1)[-1]
            self.draft = self.draft[:-len(prefix)] + word
            if len(self.draft) < LIMIT:
                self.draft += " "
        elif key == "key:done":
            return self.draft.strip()
        elif key == "key:delete":
            self.draft = self.draft[:-1]
        elif key == "key:space":
            if self.draft and not self.draft.endswith(" ") and len(self.draft) < LIMIT:
                self.draft += " "
        elif key.startswith("key:") and len(self.draft) < LIMIT:
            self.draft += key[4:]
        self.row = None
        return None
