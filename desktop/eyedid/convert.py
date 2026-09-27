"""Camera millimetres <-> screen pixels, as the SDK's C++ wrapper does it (CoordConverterV2,
makeCameraToDisplayConverter). The C API reports gaze and calibration points in millimetres from
the camera, x to the right and y UP; screens count pixels from the top-left, y DOWN.

    px = S * F * (mm - tl)      S = pixels per mm on each axis, F = flip y, tl = the screen's
                                top-left corner in camera millimetres

With the camera centered above the screen (a laptop), tl = (-width_mm / 2, 0).
"""

from __future__ import annotations

from dataclasses import dataclass

MISSING = -1001.0  # the SDK's "no value" for a coordinate


@dataclass(frozen=True)
class Display:
    """One screen: its size in pixels and millimetres, and where its top-left corner is from the
    camera (millimetres, camera axes). `left` / `top` place it on the virtual desktop in pixels."""

    width_px: int
    height_px: int
    width_mm: float
    height_mm: float
    tl_mm: tuple[float, float] | None = None  # None = camera centered on the top edge
    left: int = 0
    top: int = 0

    @property
    def corner(self) -> tuple[float, float]:
        return self.tl_mm if self.tl_mm is not None else (-self.width_mm / 2, 0.0)

    @property
    def px_per_mm(self) -> float:
        return (self.width_px / self.width_mm + self.height_px / self.height_mm) / 2

    def to_px(self, x_mm: float, y_mm: float) -> tuple[float, float]:
        cx, cy = self.corner
        return (
            self.left + (x_mm - cx) * self.width_px / self.width_mm,
            self.top - (y_mm - cy) * self.height_px / self.height_mm,
        )

    def to_mm(self, x_px: float, y_px: float) -> tuple[float, float]:
        cx, cy = self.corner
        return (
            cx + (x_px - self.left) * self.width_mm / self.width_px,
            cy - (y_px - self.top) * self.height_mm / self.height_px,
        )
