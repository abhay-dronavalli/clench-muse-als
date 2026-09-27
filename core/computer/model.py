"""Pure band/target selection model. A stable target id never changes selection silently."""
from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True)
class Target:
    id: str
    label: str
    x: float
    y: float
    width: float
    height: float
    text: bool = False

    @classmethod
    def parse(cls, data: dict) -> "Target":
        rect = {k: float(data[k]) for k in ("x", "y", "width", "height")}
        if not all(isfinite(v) for v in rect.values()) or min(rect["width"], rect["height"]) < 8:
            raise ValueError("invalid target rectangle")
        return cls(str(data["id"])[:100], str(data["label"])[:60], **rect, text=bool(data.get("text")))


def reading_order(targets: list[Target]) -> list[Target]:
    # Treat tops within 12 px as one line, then read left to right.
    rows: list[list[Target]] = []
    for target in sorted(targets, key=lambda t: (t.y, t.x, t.id)):
        if not rows or target.y - rows[-1][0].y > 12:
            rows.append([])
        rows[-1].append(target)
    return [t for row in rows for t in sorted(row, key=lambda t: (t.x, t.y, t.id))]


def split_bands(targets: list[Target], height: float) -> dict[int, list[Target]]:
    bands: dict[int, list[Target]] = {}
    for t in reading_order(targets):
        band = min(3, max(0, int((t.y + t.height / 2) / max(height, 1) * 4)))
        bands.setdefault(band, []).append(t)
    return dict(sorted(bands.items()))


MENU = [("down", "Scroll down"), ("up", "Scroll up"), ("back", "Back (history)"),
        ("home", "Home"), ("exit", "Exit")]


class Selection:
    def __init__(self):
        self.bands: dict[int, list[Target]] = {}
        self.level = "bands"
        self.band = 0
        self.page = 0
        self.index = 0
        self.height = 1.0

    def update(self, targets: list[Target], height: float, *, navigation=False):
        navigation = navigation or not self.bands
        old = self.items()
        selected = old[self.index][0] if old else None
        self.height = height
        self.bands = split_bands(targets, height)
        if navigation:
            self.back_to_bands()
        elif self.level == "targets" and self.band not in self.bands:
            self.back_to_bands()
        items = self.items()
        ids = [i[0] for i in items]
        if selected in ids and not navigation:
            self.index = ids.index(selected)
        elif selected is not None and not navigation:
            self.back_to_bands()
        self.index = min(self.index, len(self.items()) - 1)

    def items(self) -> list[tuple[str, str]]:
        if self.level == "bands":
            return [(f"band:{b}", f"Band {b + 1}") for b in self.bands] + [("menu", "Browser menu")]
        if self.level == "menu":
            return MENU
        if self.level == "text":
            return [("cancel", "Cancel")]
        targets = self.bands.get(self.band, [])
        # Eight slots total: seven targets and More when pagination is needed.
        size = 7 if len(targets) > 8 else 8
        start = self.page * size
        items = [(t.id, t.label) for t in targets[start:start + size]]
        if len(targets) > 8:
            items.append(("more", "More..."))
        return items or [("cancel", "Cancel")]

    def tick(self):
        self.index = (self.index + 1) % len(self.items())

    def back_to_bands(self):
        self.level, self.page, self.index = "bands", 0, 0

    def back(self):
        if self.level == "text":
            self.level, self.index = "targets", 0
        elif self.level != "bands":
            self.back_to_bands()

    def pick(self) -> tuple[str, str]:
        key, label = self.items()[self.index]
        if key.startswith("band:"):
            self.band = int(key.split(":")[1])
            self.level, self.index, self.page = "targets", 0, 0
            return "", label
        if key == "menu":
            self.level, self.index = "menu", 0
            return "", label
        if key == "more":
            self.page = (self.page + 1) % ((len(self.bands[self.band]) + 6) // 7)
            self.index = 0
            return "", label
        if key == "cancel":
            self.back()
            return "", label
        return key, label
