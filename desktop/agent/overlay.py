"""The overlay: one full-screen window over the primary screen that clicks pass through and that never
takes focus. It only draws; the agent (app.py) owns the state. Everything is drawn in physical
pixels (the painter is scaled by 1 / device pixel ratio), the same pixels as the gaze and UIA.

Looks follow the board: yellow-300 highlight, red-700 help, sky-400 for "zoom / scanning" cues,
dark translucent cards with large white text.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from desktop.agent import winput
from desktop.agent.snap import Candidate, Rect

if TYPE_CHECKING:
    from desktop.agent.app import Agent
    from desktop.agent.interaction import Controller

YELLOW = QColor("#fde047")
SKY = QColor("#38bdf8")
RED = QColor("#b91c1c")
GREEN = QColor("#34d399")
GRAY = QColor("#9ca3af")
CARD = QColor(17, 24, 39, 225)
WHITE = QColor("#f9fafb")

HELP_TEXT = {"en": "Calling for help. Double blink to cancel.", "es": "Pidiendo ayuda. Parpadea dos veces para cancelar."}
BACK_TEXT = {"en": ("Go back?", "Clench to go back. Do nothing to stay."),
             "es": ("¿Volver?", "Aprieta para volver. No hagas nada para quedarte.")}
CALIB_TEXT = {"en": ("Look at the dot", "Double blink to stop"),
              "es": ("Mira el punto", "Parpadea dos veces para parar")}
BOARD_TEXT = {"en": "Clench board has control", "es": "El tablero Clench tiene el control"}
ARMED_TEXT = {"en": {"right": "Right click armed", "double": "Double click armed", "scroll": "Scroll armed",
                     "drag": "Drag armed"},
              "es": {"right": "Clic derecho listo", "double": "Doble clic listo", "scroll": "Desplazar listo",
                     "drag": "Arrastrar listo"}}
ZOOM_TEXT = {"en": "Clench to click. Double blink to close.", "es": "Aprieta para hacer clic. Parpadea dos veces para cerrar."}


def qrect(r: Rect, grow: float = 0) -> QRectF:
    return QRectF(r.left - grow, r.top - grow, r.width + 2 * grow, r.height + 2 * grow)


class Overlay(QWidget):
    def __init__(self, agent: "Agent") -> None:
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool | Qt.WindowType.WindowTransparentForInput
                         | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.agent = agent
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowTitle("Clench desktop overlay")
        screen = agent.qscreen
        self.setGeometry(screen.geometry())
        self.dpr = screen.devicePixelRatio()
        self.hide_all = False  # while grabbing the screen for the zoom
        self.zoom_pixmap: QPixmap | None = None

    def show_overlay(self) -> None:
        self.show()
        winput.make_overlay(int(self.winId()))

    def keep_on_top(self) -> None:
        winput.keep_on_top(int(self.winId()))

    # --- painting -----------------------------------------------------------------------------------

    def _font(self, px: float, bold: bool = False) -> QFont:
        f = QFont("Segoe UI")
        f.setPixelSize(max(int(px), 8))
        f.setBold(bold)
        return f

    def paintEvent(self, _event: object) -> None:  # noqa: N802 (Qt)
        if self.hide_all:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.scale(1 / self.dpr, 1 / self.dpr)
        a = self.agent
        ctl = a.ctl
        mm = ctl.px_per_mm
        lang = ctl.lang if ctl.lang in HELP_TEXT else "en"
        s = ctl.screen
        if a.help_countdown is not None:
            self.paint_help(p, a.help_countdown, lang)
        elif a.dots is not None:
            self.paint_calibration(p, lang)
        elif ctl.active:
            self.paint_desktop(p, lang, mm)
        else:
            self.pill(p, BOARD_TEXT[lang], s.left + 4 * mm, s.bottom - 16 * mm, GRAY, size=3.2 * mm)
        self.paint_status(p, mm)
        p.end()

    def paint_help(self, p: QPainter, n: int, lang: str) -> None:
        s = self.agent.ctl.screen
        p.fillRect(qrect(s), QColor(185, 28, 28, 215))
        p.setPen(WHITE)
        p.setFont(self._font(s.height * 0.35, bold=True))
        p.drawText(qrect(s), Qt.AlignmentFlag.AlignCenter, str(n))
        p.setFont(self._font(s.height * 0.045, bold=True))
        p.drawText(QRectF(s.left, s.bottom - s.height * 0.22, s.width, s.height * 0.1),
                   Qt.AlignmentFlag.AlignCenter, HELP_TEXT[lang])

    def paint_calibration(self, p: QPainter, lang: str) -> None:
        a = self.agent
        s = a.ctl.screen
        mm = a.ctl.px_per_mm
        p.fillRect(qrect(s), QColor(3, 7, 18, 235))
        title, hint = CALIB_TEXT[lang]
        p.setPen(WHITE)
        p.setFont(self._font(7 * mm, bold=True))
        p.drawText(QRectF(s.left, s.top + s.height * 0.06, s.width, 12 * mm), Qt.AlignmentFlag.AlignCenter, title)
        p.setPen(GRAY)
        p.setFont(self._font(4 * mm))
        p.drawText(QRectF(s.left, s.bottom - 16 * mm, s.width, 8 * mm), Qt.AlignmentFlag.AlignCenter, hint)
        dots = a.dots
        if dots is None or dots.point is None:
            return
        x, y = dots.point
        c = QPointF(x, y)
        if dots.collecting:
            p.setPen(QPen(GREEN, 1.6 * mm))
            p.setBrush(Qt.BrushStyle.NoBrush)
            r = 8 * mm
            p.drawArc(QRectF(x - r, y - r, 2 * r, 2 * r), 90 * 16, -int(360 * 16 * dots.progress))
        else:  # settling: a ring closing in on the dot
            left = max(0.0, 1 - (time.time() - a.dot_since))
            r = (3 + 9 * left) * mm
            p.setPen(QPen(SKY, 0.8 * mm))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(c, r, r)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(YELLOW)
        p.drawEllipse(c, 2.6 * mm, 2.6 * mm)
        p.setBrush(QColor("#111827"))
        p.drawEllipse(c, 0.8 * mm, 0.8 * mm)

    def paint_desktop(self, p: QPainter, lang: str, mm: float) -> None:
        a = self.agent
        ctl = a.ctl
        s = ctl.screen
        target = ctl.target
        tab = ctl.menu_tab
        tab_on = bool(target and target.candidate is tab)
        if ctl.mode == "palette":
            p.fillRect(qrect(s), QColor(3, 7, 18, 170))
            for tile in ctl.palette_tiles():
                on = bool(target and target.candidate and target.candidate.kind == tile.kind)
                self.card(p, tile.rect, YELLOW if on else None, mm)
                p.setPen(QColor("#111827") if on else WHITE)
                p.setFont(self._font(6.5 * mm, bold=True))
                p.drawText(qrect(tile.rect), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, tile.name)
        elif ctl.mode == "zoom" and ctl.zoom is not None:
            z = ctl.zoom
            p.fillRect(qrect(s), QColor(3, 7, 18, 150))
            if self.zoom_pixmap is not None:
                p.drawPixmap(qrect(z.panel), self.zoom_pixmap, QRectF(self.zoom_pixmap.rect()))
            else:
                p.fillRect(qrect(z.panel), QColor(31, 41, 55, 240))
            p.setPen(QPen(SKY, 0.8 * mm))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(qrect(z.panel, 0.4 * mm), 2 * mm, 2 * mm)
            if target is not None:
                if target.sure and target.candidate is not None:
                    self.box(p, z.rect_to_panel(target.candidate.rect), mm)
                else:
                    self.crosshair(p, *z.to_panel(*target.point), mm)
            self.pill(p, ZOOM_TEXT[lang], z.panel.left, z.panel.bottom + 3 * mm, SKY, size=3.4 * mm)
        else:
            if target is not None and not tab_on:
                if target.sure and target.candidate is not None:
                    self.box(p, target.candidate.rect, mm)
                else:
                    x, y = target.point
                    p.setPen(QPen(SKY, 0.6 * mm, Qt.PenStyle.DashLine))
                    p.setBrush(QColor(56, 189, 248, 28))
                    p.drawEllipse(QPointF(x, y), ctl.radius, ctl.radius)
                    self.magnifier(p, x + ctl.radius * 0.7, y - ctl.radius * 0.7, mm)
            if ctl.mode == "back" and ctl.back_until is not None:
                self.paint_back(p, lang, mm)
            if ctl.mode == "scroll" and ctl.scroll is not None:
                self.paint_scroll(p, mm)
            if ctl.mode == "keyboard":
                self.paint_keyboard(p, mm)
            if ctl.mode == "drag" and ctl.drag_from is not None:
                self.paint_drag(p, mm)
        # the tab to the palette, on the right edge (hidden under the keyboard, where it does nothing)
        if ctl.mode != "keyboard":
            self.paint_tab(p, tab, tab_on, mm)
        self.paint_notes(p, lang, mm, a, ctl, s)

    def paint_tab(self, p: QPainter, tab: "Candidate", tab_on: bool, mm: float) -> None:
        self.card(p, tab.rect, YELLOW if tab_on else None, mm, radius=2.5 * mm)
        p.save()
        c = tab.rect.center
        p.translate(*c)
        p.rotate(-90)
        p.setPen(QColor("#111827") if tab_on else WHITE)
        p.setFont(self._font(3.6 * mm, bold=True))
        p.drawText(QRectF(-tab.rect.height / 2, -tab.rect.width / 2, tab.rect.height, tab.rect.width),
                   Qt.AlignmentFlag.AlignCenter, "Clench")
        p.restore()

    def paint_notes(self, p: QPainter, lang: str, mm: float, a: "Agent", ctl: "Controller", s: Rect) -> None:
        # a small gaze dot, so the person sees where the tracker thinks they look
        if a.gaze_point is not None and ctl.eyes and ctl.mode != "palette":
            x, y = a.gaze_point
            p.setPen(QPen(QColor(17, 24, 39, 200), 0.4 * mm))
            p.setBrush(QColor(249, 250, 251, 150))
            p.drawEllipse(QPointF(x, y), 1.3 * mm, 1.3 * mm)
        top = s.top + 5 * mm
        notes = []
        if not ctl.eyes and a.source_kind == "eyedid":
            notes.append((ctl.t("no_eyes"), GRAY))
        if ctl.armed != "left":
            notes.append((ARMED_TEXT[lang][ctl.armed], YELLOW))
        if ctl.paused:
            notes.append((ctl.t("paused"), GRAY))
        if ctl.toast:
            notes.append((ctl.toast[0], SKY))
        for text, color in notes:
            self.pill(p, text, None, top, color, size=4 * mm)
            top += 11 * mm

    def paint_keyboard(self, p: QPainter, mm: float) -> None:
        ctl = self.agent.ctl
        area = ctl.keyboard_area()
        p.fillRect(qrect(area), QColor(3, 7, 18, 225))
        p.setPen(QColor(249, 250, 251, 220))
        p.setFont(self._font(5 * mm))
        typed = ctl.kb_typed + "\u2502"
        p.drawText(QRectF(area.left + 4 * mm, area.top + 1.5 * mm, area.width - 8 * mm, 10 * mm),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, typed)
        target = ctl.target
        on_kind = target.candidate.kind if target and target.candidate else None
        for key in ctl.keyboard_keys():
            on = key.kind == on_kind
            lit = key.kind == "key:shift" and ctl.kb_shift
            self.card(p, key.rect, YELLOW if on else SKY if lit else None, mm, radius=2 * mm)
            p.setPen(QColor("#111827") if on or lit else WHITE)
            p.setFont(self._font(6.5 * mm if len(key.name) == 1 else 4.5 * mm, bold=True))
            p.drawText(qrect(key.rect), Qt.AlignmentFlag.AlignCenter, key.name)

    def paint_scroll(self, p: QPainter, mm: float) -> None:
        ctl = self.agent.ctl
        c = ctl.scroll
        assert c is not None
        gaze = self.agent.gaze_point
        on = c.direction(*gaze)[0] if gaze is not None else 0
        for zone, sign, label in ((c.up, 1, ctl.t("up")), (c.down, -1, ctl.t("down"))):
            self.card(p, zone, SKY if on == sign else None, mm)
            p.setPen(QColor("#111827") if on == sign else WHITE)
            p.setFont(self._font(5 * mm, bold=True))
            p.drawText(qrect(zone), Qt.AlignmentFlag.AlignCenter, ("\u25b2 " if sign > 0 else "\u25bc ") + label)
        x, y = c.anchor
        p.setPen(QPen(SKY, 0.6 * mm))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(x, y), 2 * mm, 2 * mm)

    def paint_drag(self, p: QPainter, mm: float) -> None:
        a = self.agent
        assert a.ctl.drag_from is not None
        x0, y0 = a.ctl.drag_from
        p.setPen(QPen(YELLOW, 0.8 * mm, Qt.PenStyle.DashLine))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(x0, y0), 2.5 * mm, 2.5 * mm)
        if a.gaze_point is not None:
            x, y = a.gaze_point
            p.drawLine(QPointF(x0, y0), QPointF(x, y))
            self.crosshair(p, x, y, mm)

    def paint_back(self, p: QPainter, lang: str, mm: float) -> None:
        ctl = self.agent.ctl
        s = ctl.screen
        title, hint = BACK_TEXT[lang]
        w, h = 120 * mm, 42 * mm
        r = Rect(s.left + (s.width - w) / 2, s.top + (s.height - h) / 2, s.left + (s.width + w) / 2,
                 s.top + (s.height + h) / 2)
        self.card(p, r, None, mm)
        p.setPen(WHITE)
        p.setFont(self._font(9 * mm, bold=True))
        p.drawText(QRectF(r.left, r.top + 5 * mm, r.width, 13 * mm), Qt.AlignmentFlag.AlignCenter, title)
        p.setFont(self._font(4 * mm))
        p.drawText(QRectF(r.left, r.top + 19 * mm, r.width, 8 * mm), Qt.AlignmentFlag.AlignCenter, hint)
        left = max(0.0, (ctl.back_until or 0) - time.time()) / 3.0
        p.fillRect(QRectF(r.left + 8 * mm, r.bottom - 9 * mm, (r.width - 16 * mm) * left, 2 * mm), YELLOW)

    def paint_status(self, p: QPainter, mm: float) -> None:
        a = self.agent
        s = a.ctl.screen
        parts = [("Core", GREEN if a.core.connected else RED),
                 ("Eyes" if a.source_kind == "eyedid" else "Mouse", GREEN if a.ctl.eyes else GRAY)]
        ctl = a.ctl
        low = ctl.active and ctl.mode == "keyboard" and not ctl.kb_top
        x, y = s.left + 3 * mm, s.top + 3 * mm if low else s.bottom - 14 * mm  # off a bottom keyboard
        p.setFont(self._font(2.6 * mm))
        hint = "F8 clench (hold: help) · F9 double blink · F7 calibrate · F10 pause · Ctrl+F8 board/desktop"
        width = sum(p.fontMetrics().horizontalAdvance(label) + 5 * mm for label, _ in parts)
        width += p.fontMetrics().horizontalAdvance(hint) + 5 * mm
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(CARD)
        p.drawRoundedRect(QRectF(x - 1.5 * mm, y - 1.2 * mm, width, 5.8 * mm), 2.9 * mm, 2.9 * mm)
        for label, color in parts:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
            p.drawEllipse(QPointF(x + 1.2 * mm, y + 1.6 * mm), 1 * mm, 1 * mm)
            p.setPen(QColor(249, 250, 251, 200))
            w = p.fontMetrics().horizontalAdvance(label) + 1
            p.drawText(QRectF(x + 3 * mm, y, w, 3.4 * mm), Qt.AlignmentFlag.AlignVCenter, label)
            x += 4 * mm + w
        p.setPen(QColor(249, 250, 251, 150))
        p.drawText(QRectF(x + 2 * mm, y, s.width, 3.4 * mm), Qt.AlignmentFlag.AlignVCenter, hint)

    # --- shapes ---------------------------------------------------------------------------------------

    def card(self, p: QPainter, r: Rect, highlight: QColor | None, mm: float, radius: float | None = None) -> None:
        radius = 3 * mm if radius is None else radius
        p.setPen(QPen(highlight, 1.2 * mm) if highlight else QPen(QColor(255, 255, 255, 40), 0.3 * mm))
        p.setBrush(highlight if highlight else CARD)
        p.drawRoundedRect(qrect(r), radius, radius)

    def box(self, p: QPainter, r: Rect, mm: float) -> None:
        p.setPen(QPen(QColor(17, 24, 39, 180), 1.6 * mm))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(qrect(r, 1.2 * mm), 1.5 * mm, 1.5 * mm)
        p.setPen(QPen(YELLOW, 0.9 * mm))
        p.setBrush(QColor(253, 224, 71, 40))
        p.drawRoundedRect(qrect(r, 1.2 * mm), 1.5 * mm, 1.5 * mm)

    def crosshair(self, p: QPainter, x: float, y: float, mm: float) -> None:
        for color, width in ((QColor(17, 24, 39, 200), 1.4 * mm), (YELLOW, 0.6 * mm)):
            p.setPen(QPen(color, width))
            p.drawLine(QPointF(x - 5 * mm, y), QPointF(x + 5 * mm, y))
            p.drawLine(QPointF(x, y - 5 * mm), QPointF(x, y + 5 * mm))

    def magnifier(self, p: QPainter, x: float, y: float, mm: float) -> None:
        p.setPen(QPen(SKY, 0.6 * mm))
        p.setBrush(QColor(17, 24, 39, 200))
        p.drawEllipse(QPointF(x, y), 2.6 * mm, 2.6 * mm)
        p.drawLine(QPointF(x + 1.9 * mm, y + 1.9 * mm), QPointF(x + 3.8 * mm, y + 3.8 * mm))
        p.drawLine(QPointF(x - 1.2 * mm, y), QPointF(x + 1.2 * mm, y))
        p.drawLine(QPointF(x, y - 1.2 * mm), QPointF(x, y + 1.2 * mm))

    def pill(self, p: QPainter, text: str, left: float | None, top: float, color: QColor, size: float) -> None:
        s = self.agent.ctl.screen
        p.setFont(self._font(size, bold=True))
        w = p.fontMetrics().horizontalAdvance(text) + size * 2.4
        h = size * 2.2
        x = s.left + (s.width - w) / 2 if left is None else left
        path = QPainterPath()
        path.addRoundedRect(QRectF(x, top, w, h), h / 2, h / 2)
        p.setPen(QPen(color, size * 0.18))
        p.setBrush(QBrush(CARD))
        p.drawPath(path)
        p.setPen(WHITE)
        p.drawText(QRectF(x, top, w, h), Qt.AlignmentFlag.AlignCenter, text)
