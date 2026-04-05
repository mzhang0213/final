"""
Region picker and menu built entirely in PyQt6 — no tkinter.

On macOS, mixing tkinter and PyQt6 in the same process causes an
NSInvalidArgumentException because both frameworks try to own NSApplication.
"""

import colorsys
import random

from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame
from PyQt6.QtCore import Qt, QRect, QEventLoop
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush, QFont


def _gen_colors(options: list[str]) -> dict[str, tuple[int, int, int]]:
    """Evenly-spaced HSV hues with a random start offset → vivid, distinct RGB colors."""
    n = len(options)
    offset = random.random()
    colors = {}
    for i, opt in enumerate(options):
        hue = (offset + i / n) % 1.0
        r, g, b = colorsys.hsv_to_rgb(hue, 0.85, 0.95)
        colors[opt] = (int(r * 255), int(g * 255), int(b * 255))
    return colors


class ScreenRegionPicker(QWidget):
    def __init__(self, color: tuple[int, int, int] = (255, 50, 50)):
        super().__init__()
        self.color = color
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(Qt.CursorShape.CrossCursor)

        screen = QApplication.primaryScreen().geometry()
        self.setGeometry(screen)

        self.start_point = None
        self.end_point = None
        self.is_selecting = False
        self.selected_region = None
        self._loop = None

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Dim overlay
        painter.fillRect(self.rect(), QColor(0, 0, 0, 60))

        # Instruction label — gray filled, black border, bold text
        text = "Click and drag to select a region.  ESC to cancel."
        font = QFont('Arial', 13)
        font.setBold(True)
        painter.setFont(font)
        fm = painter.fontMetrics()
        tw = fm.horizontalAdvance(text)
        th = fm.height()
        pad = 10
        bx = (self.width() - tw) // 2 - pad
        by = 40
        bw = tw + pad * 2
        bh = th + pad * 2
        painter.fillRect(bx, by, bw, bh, QColor(255, 255, 255, 230))
        painter.setPen(QPen(QColor(0, 0, 0), 1))
        painter.drawRect(bx, by, bw, bh)
        painter.setPen(Qt.GlobalColor.black)
        painter.drawText(bx + pad, by + pad + fm.ascent(), text)

        # Selection rectangle in the option's color
        if self.start_point and self.end_point:
            r, g, b = self.color
            rect = QRect(self.start_point, self.end_point).normalized()
            painter.setPen(QPen(QColor(r, g, b), 2))
            painter.setBrush(QBrush(QColor(r, g, b, 70)))
            painter.drawRect(rect)

        painter.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.start_point = event.pos()
            self.end_point = event.pos()
            self.is_selecting = True

    def mouseMoveEvent(self, event):
        if self.is_selecting:
            self.end_point = event.pos()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.is_selecting:
            self.is_selecting = False
            self.end_point = event.pos()
            rect = QRect(self.start_point, self.end_point).normalized()
            self.selected_region = {
                'left': rect.x(),
                'top': rect.y(),
                'width': rect.width(),
                'height': rect.height(),
            }
            self.close()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()

    def closeEvent(self, event):
        if self._loop and self._loop.isRunning():
            self._loop.quit()
        super().closeEvent(event)

    def pick(self) -> dict | None:
        """Block until the user drags a region. Returns {left, top, width, height} or None."""
        self._loop = QEventLoop()
        self.show()
        self._loop.exec()
        return self.selected_region


class RegionMenu(QWidget):
    def __init__(self, options: list[str]):
        super().__init__()
        self.options = options
        self.cursor = 0
        self.regions: dict[str, dict] = {}
        self.colors = _gen_colors(options)
        self._loop = None

        self.setWindowTitle("Region Menu")
        self.setWindowFlags(Qt.WindowType.WindowStaysOnTopHint)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)

        self._render()
        self._center()

    def _center(self):
        screen = QApplication.primaryScreen().geometry()
        self.adjustSize()
        self.move(
            (screen.width()  - self.width())  // 2,
            (screen.height() - self.height()) // 2,
        )

    def _render(self):
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Title
        title = QLabel("Assign screen regions")
        title.setStyleSheet("font: bold 13pt Arial; padding: 10px 20px; background: gray;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._layout.addWidget(title)

        self._layout.addWidget(self._divider())

        # Option rows
        for i, opt in enumerate(self.options):
            selected = i == self.cursor
            region   = self.regions.get(opt)
            status   = f"{region['width']}x{region['height']}" if region else "not set"
            arrow    = "▶" if selected else "  "
            bg       = "#ddeeff" if selected else "gray"
            r, g, b  = self.colors[opt]

            row = QWidget()
            row.setStyleSheet(f"background: {bg};")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(12, 6, 12, 6)

            # Color swatch
            swatch = QFrame()
            swatch.setFixedSize(14, 14)
            swatch.setStyleSheet(f"background: rgb({r},{g},{b}); border-radius: 3px;")

            left = QLabel(f" {arrow}  {opt}")
            left.setStyleSheet(f"font: {'bold ' if selected else ''}12pt Courier; background: {bg};")

            right = QLabel(status)
            right.setStyleSheet(
                f"font: 11pt Courier; color: {'#228822' if region else '#999999'}; background: {bg};"
            )
            right.setAlignment(Qt.AlignmentFlag.AlignRight)

            row_layout.addWidget(swatch)
            row_layout.addWidget(left)
            row_layout.addStretch()
            row_layout.addWidget(right)
            self._layout.addWidget(row)

        self._layout.addWidget(self._divider())

        # Continue button row
        continue_selected = self.cursor == len(self.options)
        cont_bg = "#c8e6c9" if continue_selected else "gray"
        cont_arrow = "▶" if continue_selected else "  "
        cont_row = QWidget()
        cont_row.setStyleSheet(f"background: {cont_bg};")
        cont_layout = QHBoxLayout(cont_row)
        cont_layout.setContentsMargins(12, 8, 12, 8)
        cont_label = QLabel(f" {cont_arrow}  Continue")
        cont_label.setStyleSheet(
            f"font: {'bold ' if continue_selected else ''}12pt Courier; "
            f"color: #1a7a1a; background: {cont_bg};"
        )
        cont_layout.addWidget(cont_label)
        cont_layout.addStretch()
        self._layout.addWidget(cont_row)

        self._layout.addWidget(self._divider())

        # Footer
        footer = QLabel("↑↓ navigate    Enter assign / continue    ESC quit")
        footer.setStyleSheet("font: 9pt Arial; color: #888888; padding: 6px; background: gray;")
        footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._layout.addWidget(footer)

        self.adjustSize()

    def _divider(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color: #cccccc; background: #cccccc;")
        line.setFixedHeight(1)
        return line

    def keyPressEvent(self, event):
        key = event.key()
        total = len(self.options) + 1  # options + Continue
        if key == Qt.Key.Key_Up:
            self.cursor = (self.cursor - 1) % total
            self._render()
        elif key == Qt.Key.Key_Down:
            self.cursor = (self.cursor + 1) % total
            self._render()
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self.cursor == len(self.options):
                # Continue button — close and return
                self.close()
            else:
                opt = self.options[self.cursor]
                self.hide()
                region = ScreenRegionPicker(color=self.colors[opt]).pick()
                if region:
                    region['color'] = self.colors[opt]
                    self.regions[opt] = region
                self.show()
                self.raise_()
                self.activateWindow()
                self._render()
        elif key == Qt.Key.Key_Escape:
            self.close()

    def closeEvent(self, event):
        if self._loop and self._loop.isRunning():
            self._loop.quit()
        super().closeEvent(event)

    def run(self) -> dict[str, dict]:
        """Show the menu and block until Continue/ESC. Returns assigned regions (each with 'color')."""
        self._loop = QEventLoop()
        self.show()
        self._loop.exec()
        return self.regions
