import random

import cv2 as cv
import numpy as np
import os
import pytesseract
from matplotlib import pyplot as plt
import sys
from PyQt6.QtWidgets import QApplication, QMainWindow, QWidget
from PyQt6.QtCore import Qt, QRect
from PyQt6.QtGui import QPainter, QPen, QColor, QBrush, QFont
from mss import mss
import time
import platform
import threading
from queue import Queue

import logging

from resources.region_menu import RegionMenu, ScreenRegionPicker
import requests


class MDebug:
    def __init__(self):
        self.info = []
        self.start = time.time()
        logging.basicConfig(level=logging.INFO)

    def log_msg(self, msg):
        self.info.append({"type":"message","message":str(msg), "time":time.time()-self.start})

    def log_img(self, img, name):
        self.info.append({"type":"image","name":str(name), "img":img, "time":time.time()-self.start})

    def log_error(self, msg):
        self.info.append({"type":"error","message":str(msg), "time":time.time()-self.start})

    def dump(self, write_file=False):
        ret = ""
        os.makedirs("./logs/", exist_ok=True)
        for msg in self.info:
            if msg["type"]=="message":
                fmt_msg = "INFO ("+str(msg["time"])+"): "+msg["message"]
                logging.info(fmt_msg)
                ret+=fmt_msg+"\n"
            elif msg["type"]=="image":
                cv.imwrite(msg["name"]+str(msg["time"]),msg["img"])
            elif msg["type"]=="error":
                fmt_msg = f"(ERROR {str(msg["time"])}) |  {msg["message"]}"
                logging.error(fmt_msg)
                ret+=fmt_msg+"\n"

        if write_file:
            with open(f"./logs/logs{round(time.time(), ndigits=5)}.txt", "w") as f:
                f.write(ret)

FEATURES = {
    #screen_feature: (tl,br)
    "gamestats":  ((0.7,  0.0), (1.0,  0.5 )),
    "hotbar":     ((0.0,  0.5), (1.0,  1.0 )),
    "map":        ((0.5,  0.5), (1.0,  1.0 )),
    "playerstats":((0.0,  0.5), (0.5,  1.0 )),
    "tab_menu":   ((0.05, 0.0), (0.95, 0.85)),  # full-screen overlay (held Tab)
}
BASE_RESOLUTION = (1512, 982) #my screen res FEATURES were captured at

# Relative sub-regions for each readable value within a feature crop.
# Format: name -> { value_name: ((rx1, ry1), (rx2, ry2)) }
# All coords are fractions of the crop's (width, height) — tune from captures.

def _tab_menu_values() -> dict[str, tuple]:
    """
    Generate relative cell positions for the LoL tab scoreboard grid.
    Layout: game_time header | 5 blue rows | 5 red rows
    Columns: name, kda, cs, gold  (x anchors shared across all rows)
    """
    # Column x-ranges within the tab crop
    cols = {
        "name": (0.18, 0.38),
        "kda":  (0.40, 0.52),
        "cs":   (0.52, 0.59),
        "gold": (0.60, 0.68),
    }
    # Row y-ranges: blue team rows then red team rows
    # Each team occupies roughly half the crop; header ~7% at top
    row_h = 0.088  # height of one player row
    blue_y0, red_y0 = 0.10, 0.58
    blue_rows = [(blue_y0 + i * row_h, blue_y0 + (i + 1) * row_h) for i in range(5)]
    red_rows  = [(red_y0  + i * row_h, red_y0  + (i + 1) * row_h) for i in range(5)]

    values: dict[str, tuple] = {
        "game_time": ((0.38, 0.00), (0.62, 0.07)),
    }
    for team, rows in (("blue", blue_rows), ("red", red_rows)):
        for i, (y1, y2) in enumerate(rows, 1):
            for stat, (x1, x2) in cols.items():
                values[f"{team}{i}_{stat}"] = ((x1, y1), (x2, y2))
    return values


FEATURE_VALUES: dict[str, dict[str, tuple]] = {

    # Top HUD bar: timer (center), team gold/kills (sides)
    "gamestats": {
        "timer":         ((0.38, 0.00), (0.62, 1.00)),  # game clock, center
        "blue_kills":    ((0.10, 0.00), (0.30, 1.00)),  # blue team kill count
        "red_kills":     ((0.70, 0.00), (0.90, 1.00)),  # red team kill count
        "blue_gold":     ((0.05, 0.00), (0.25, 1.00)),  # blue team total gold
        "red_gold":      ((0.75, 0.00), (0.95, 1.00)),  # red team total gold
    },

    # Bottom-left player HUD: health/mana bars, level badge, XP bar
    "playerstats": {
        "health":        ((0.08, 0.55), (0.55, 0.68)),  # HP number on health bar
        "mana":          ((0.08, 0.72), (0.55, 0.84)),  # MP number on mana bar
        "level":         ((0.00, 0.45), (0.10, 0.60)),  # champion level badge
        "xp_bar":        ((0.00, 0.88), (1.00, 1.00)),  # XP progress (numeric if shown)
    },

    # Bottom-center hotbar: summoner spell CDs, gold, CS, KDA
    "hotbar": {
        "gold":          ((0.42, 0.72), (0.58, 0.90)),  # current gold
        "cs":            ((0.28, 0.72), (0.42, 0.90)),  # creep score
        "kda":           ((0.38, 0.10), (0.62, 0.30)),  # K/D/A line
        "spell1_cd":     ((0.03, 0.30), (0.13, 0.55)),  # summoner spell 1 cooldown
        "spell2_cd":     ((0.03, 0.55), (0.13, 0.80)),  # summoner spell 2 cooldown
    },

    # Minimap: no OCR targets (visual only)
    "map": {},

    # Tab scoreboard: game_time + blue1..5 / red1..5 × {name, kda, cs, gold}
    "tab_menu": _tab_menu_values(),
}

app = QApplication.instance() or QApplication(sys.argv)
_screensize=app.primaryScreen().size()
SCREEN_SIZE = _screensize.width(),_screensize.height()
MLOG = MDebug()
# KEYS = [
#     {
#         "name":"gamestats",
#         "box":((0.75,0),(1,0.2))
#     },
#     {
#         "name":"hotbar",
#         "box":((0,0.5),(1,1))
#     },
#     {
#         "name":"items",
#         "box":((0.5,0.75),(0.9,1))
#     },
#     {
#         "name":"map",
#         "box":((0.5,0.5),(1,1))
#     },
#     {
#         "name":"playerstats",
#         "box":((0,0.5),(0.5,1))
#     }
# ]

def show_imgs(_img):
    """
    show cv images, auto use quit with Q
    BLOCKS INPUT AND EXECUTION
    :param _img: LIST OF IMAGES
    """
    while True:
        for i,_ in enumerate(_img):
            cv.imshow(f'img{str(i)}', _)

        if cv.waitKey(1) & 0xFF == ord('q'):
            break

def get_screen_coords(w:int, h:int, scale:tuple[tuple[float,float],tuple[float,float]])-> tuple[tuple[int, int], tuple[int, int]]:
    return (int(scale[0][0]*w),int(scale[0][1]*h)),(int(scale[1][0]*w),int(scale[1][1]*h))


class Overlay(QMainWindow):

    def __init__(self):
        super().__init__()
        # Set flags for: No border, Always on Top, and Click-Through
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        # Create a central widget to enable painting
        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)

        # Format: [(top_left_x, top_left_y, bottom_right_x, bottom_right_y, filled)]
        # self.rectangles = [
        #     (50, 50, 200, 150, True),    # Rectangle 1: TL(50,50), BR(200,150), filled
        #     (300, 100, 500, 250, False),  # Rectangle 2: TL(300,100), BR(500,250), not filled
        #     (100, 200, 400, 300, True)   # Rectangle 3: TL(100,200), BR(400,300), filled
        # ]
        #
        # # Format: [(center_x, center_y, radius, filled)]
        # self.circles = [
        #     (150, 80, 40, True),   # Circle 1: Center(150,80), radius 40, filled
        #     (400, 180, 30, False), # Circle 2: Center(400,180), radius 30, not filled
        #     (250, 250, 50, True)   # Circle 3: Center(250,250), radius 50, filled
        # ]
        #
        # # Format: [(start_x, start_y, end_x, end_y)]
        # self.arrows = [
        #     (10, 350, 100, 350),   # Arrow from (10,350) to (100,350)
        #     (150, 350, 150, 250),  # Arrow from (150,350) to (150,250)
        #     (200, 300, 250, 350)   # Arrow from (200,300) to (250,350)
        # ]
        self.rectangles = []
        self.circles = []
        self.arrows = []
        self.text_boxes = []

        self.resize(SCREEN_SIZE[0], SCREEN_SIZE[1])
        self.show()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)


        for i,box in enumerate(self.text_boxes):
            x, y, text, color, font_size = box
            font = QFont('Arial', font_size)
            font.setBold(font_size >= 12)
            painter.setFont(font)
            fm = painter.fontMetrics()
            tw = fm.horizontalAdvance(text)
            th = fm.height()
            pad = 10
            bx = x
            by = y
            bw = tw + pad * 2
            bh = th + pad * 2
            painter.fillRect(bx, by, bw, bh, QColor(255, 255, 255, 255))
            painter.setPen(QPen(QColor(0, 0, 0), 1))
            painter.drawRect(bx, by, bw, bh)
            painter.setPen(Qt.GlobalColor.black)
            painter.drawText(bx + pad, by + pad + fm.ascent(), text)

        # Draw each rectangle using TL and BR points
        for rect_coords in self.rectangles:
            top_left_x, top_left_y, bottom_right_x, bottom_right_y, filled, color = rect_coords
            r, g, b = color

            width = bottom_right_x - top_left_x
            height = bottom_right_y - top_left_y
            rect = QRect(top_left_x, top_left_y, width, height)

            painter.setPen(QPen(QColor(r, g, b), 3))
            if filled:
                painter.setBrush(QBrush(QColor(r, g, b, 50)))
            else:
                painter.setBrush(QBrush())

            painter.drawRect(rect)

        # Draw each circle
        for circle_coords in self.circles:
            center_x, center_y, radius, filled = circle_coords

            # Set pen for drawing the circle
            painter.setPen(QPen(QColor(0, 255, 0), 3))  # Green border, 3px thick

            # Set brush based on fill option
            if filled:
                painter.setBrush(QBrush(QColor(0, 255, 0, 50)))  # Semi-transparent green fill
            else:
                painter.setBrush(QBrush())  # No fill

            # Draw the circle using drawEllipse
            painter.drawEllipse(int(center_x - radius), int(center_y - radius),
                                int(radius * 2), int(radius * 2))

        # Draw each arrow
        for arrow_coords in self.arrows:
            start_x, start_y, end_x, end_y = arrow_coords

            # Draw the line
            painter.setPen(QPen(QColor(0, 0, 255), 3))  # Blue line, 3px thick
            painter.drawLine(int(start_x), int(start_y), int(end_x), int(end_y))

            # Draw arrowhead
            self.draw_arrowhead(painter, start_x, start_y, end_x, end_y)

    def draw_arrowhead(self, painter, start_x, start_y, end_x, end_y):
        """Draw an arrowhead at the end point of the arrow

        Args:
            painter: QPainter object
            start_x, start_y: start coordinates of the arrow
            end_x, end_y: end coordinates of the arrow
        """
        import math

        # Calculate the angle of the line
        angle = math.atan2(end_y - start_y, end_x - start_x)

        # Size of the arrowhead
        arrowhead_length = 15

        # Point 1 of the arrowhead (rotated -30 degrees from main line)
        angle1 = angle - math.pi / 6  # -30 degrees in radians
        x1 = end_x - arrowhead_length * math.cos(angle1)
        y1 = end_y - arrowhead_length * math.sin(angle1)

        # Point 2 of the arrowhead (rotated +30 degrees from main line)
        angle2 = angle + math.pi / 6  # +30 degrees in radians
        x2 = end_x - arrowhead_length * math.cos(angle2)
        y2 = end_y - arrowhead_length * math.sin(angle2)

        # Draw the arrowhead lines
        painter.drawLine(int(end_x), int(end_y), int(x1), int(y1))
        painter.drawLine(int(end_x), int(end_y), int(x2), int(y2))

    def add_rectangle(self, top_left:tuple[int,int], bottom_right:tuple[int,int], filled=True, color=(255, 0, 0)):
        """Add a new rectangle given top-left and bottom-right points

        Args:
            top_left: tuple (x, y) representing top-left corner
            bottom_right: tuple (x, y) representing bottom-right corner
            filled: bool indicating whether to fill the rectangle
            color: tuple (r, g, b) border/fill color
        """
        tl_x, tl_y = top_left
        br_x, br_y = bottom_right
        self.rectangles.append((tl_x, tl_y, br_x, br_y, filled, color))
        self.update()  # Trigger repaint

    @staticmethod
    def _measure_text_box(text: str, font_size: int, pad: int = 10) -> tuple[int, int]:
        from PyQt6.QtGui import QFontMetrics
        font = QFont('Arial', font_size)
        font.setBold(font_size >= 12)
        fm = QFontMetrics(font)
        return fm.horizontalAdvance(text) + pad * 2, fm.height() + pad * 2

    def add_text_box(self, x: int, y: int, text: str, color=(255, 0, 0)):
        self.text_boxes.append((x, y, text, color, 13))
        self.update()
        return self._measure_text_box(text, 13)

    def add_small_text_box(self, x: int, y: int, text: str, color=(80, 80, 80), font_size: int = 9):
        self.text_boxes.append((x, y, text, color, font_size))
        self.update()
        return self._measure_text_box(text, font_size)

    def add_circle(self, center, radius, filled=True):
        """Add a new circle given center point and radius

        Args:
            center: tuple (x, y) representing center of the circle
            radius: int representing radius of the circle
            filled: bool indicating whether to fill the circle
        """
        center_x, center_y = center
        self.circles.append((center_x, center_y, radius, filled))
        self.update()  # Trigger repaint

    def add_arrow(self, start_point, end_point):
        """Add a new arrow given start and end points

        Args:
            start_point: tuple (x, y) representing start of the arrow
            end_point: tuple (x, y) representing end of the arrow
        """
        start_x, start_y = start_point
        end_x, end_y = end_point
        self.arrows.append((start_x, start_y, end_x, end_y))
        self.update()  # Trigger repaint

    def draw_button(self, completion: float, text: str,
                    x: float = 0.775, y: float = 0.15,
                    color: tuple = (38, 148, 73), progress_color: tuple = (55, 222, 108)):
        tl_x = int(SCREEN_SIZE[0] * x)
        tl_y = int(SCREEN_SIZE[1] * y)
        tb_w, tb_h = self.add_text_box(tl_x, tl_y, text, color)
        br_x = tl_x + tb_w
        br_y = tl_y + tb_h
        prog_x = tl_x + int(tb_w * completion)
        self.add_rectangle((tl_x, tl_y), (prog_x, br_y), True, progress_color)
        return (tl_x, tl_y), (br_x, br_y)

    def draw_status(self, text: str, color: tuple = (200, 120, 0), x: float = 0.775, y: float = 0.05):
        px = int(SCREEN_SIZE[0] * x)
        py = int(SCREEN_SIZE[1] * y)
        self.add_small_text_box(px, py, text, color=color, font_size=10)

    def draw_transaction_table(self, transactions: list[dict], x: float = 0.10, y: float = 0.30,
                               hover_zones: dict | None = None):
        """Draw extracted transactions as a table on the overlay.

        Args:
            transactions: list of dicts with keys: date, company, amount
            x: left edge as fraction of screen width
            y: top edge as fraction of screen height
            hover_zones: dict of (row, col) -> completion float for hover highlights.
                         col -1 = X button. cols 0/1/2 = date/company/amount.

        Returns:
            list of hitboxes: [(x1, y1, x2, y2, row_idx, col_idx), ...]
            col_idx -1 = X delete button, 0 = date, 1 = company, 2 = amount
        """
        if not transactions:
            return []

        if hover_zones is None:
            hover_zones = {}

        px = int(SCREEN_SIZE[0] * x)
        py = int(SCREEN_SIZE[1] * y)
        x_btn_w = 25  # width of X button column
        col_widths = [120, 180, 80]  # date, company, amount
        row_h = 35
        header_color = (40, 40, 40)
        row_color = (60, 60, 60)
        hitboxes = []

        # Header row (offset right to account for X column)
        headers = ["Date", "Company", "Amount"]
        cx = px + x_btn_w
        for j, hdr in enumerate(headers):
            self.add_small_text_box(cx, py, hdr, color=header_color, font_size=10)
            cx += col_widths[j]

        # Data rows
        keys = ["date", "company", "amount"]
        for i, txn in enumerate(transactions):
            ry = py + (i + 1) * row_h

            # X delete button
            x_compl = hover_zones.get((i, -1), 0.0)
            x_color = (200, 50, 50) if x_compl > 0 else (150, 150, 150)
            self.add_small_text_box(px, ry, "X", color=x_color, font_size=9)
            x_w, x_h = self._measure_text_box("X", 9)
            if x_compl > 0:
                prog_x = px + int(x_w * x_compl)
                self.add_rectangle((px, ry), (prog_x, ry + x_h), True, (200, 50, 50))
            hitboxes.append((px, ry, px + x_w, ry + x_h, i, -1))

            # Data cells
            cx = px + x_btn_w
            vals = [
                txn.get("date") or "—",
                txn.get("company") or "—",
                txn.get("amount") or "—",
            ]
            for j, val in enumerate(vals):
                cell_compl = hover_zones.get((i, j), 0.0)
                cell_color = (30, 90, 160) if cell_compl > 0 else row_color
                self.add_small_text_box(cx, ry, val, color=cell_color, font_size=9)
                cw, ch = self._measure_text_box(val, 9)
                if cell_compl > 0:
                    prog_x = cx + int(cw * cell_compl)
                    self.add_rectangle((cx, ry), (prog_x, ry + ch), True, (30, 90, 160))
                hitboxes.append((cx, ry, cx + cw, ry + ch, i, j))
                cx += col_widths[j]

        return hitboxes

    def clearCanvas(self):
        """Clear all shapes from the canvas"""
        self.rectangles.clear()
        self.circles.clear()
        self.arrows.clear()
        self.text_boxes.clear()
        self.update()  # Trigger repaint

    def hideCanvas(self):
        """Hide the canvas window"""
        self.hide()  # Hide the window

    def showCanvas(self):
        """Show the canvas window"""
        self.show()  # Show the window

    def cvToQt(self, tl:tuple[int,int], br:tuple[int,int], img_w:int, img_h:int)->tuple[tuple[int,int],tuple[int,int]]:
        return (
            int((tl[0] / img_w) * SCREEN_SIZE[0]),
            int((tl[1] / img_h) * SCREEN_SIZE[1])
        ), (
            int((br[0] / img_w) * SCREEN_SIZE[0]),
            int((br[1] / img_h) * SCREEN_SIZE[1])
        )


DYDX = []

class ScreenCapture:
    def __init__(self):
        self.running = False
        self.capture_thread = None
        self.last_frame = None
        self.frame_lock = threading.Lock()
        self.process_callback = None
        self.process_interval = 1.0
        self.last_process_time = 0
        self.latest_display_frame = None
        self.latest_process_frame = None
        self.display_lock = threading.Lock()
        self.process_lock = threading.Lock()

    def start_capture(self, process_callback=None, process_interval=1.0):
        if self.running:
            print("Screen capture is already running.")
            return False

        self.process_callback = process_callback
        self.process_interval = process_interval
        self.running = True

        self.capture_thread = threading.Thread(target=self.capturing, daemon=True)
        self.capture_thread.start()
        print("Screen capture started in background.")
        return True

    def stop_capture(self):
        if not self.running:
            return False
        self.running = False
        if self.capture_thread and self.capture_thread.is_alive():
            self.capture_thread.join(timeout=2)
        cv.destroyAllWindows()  # Safe — called from main thread by caller
        print("Screen capture stopped.")
        return True

    def get_latest_display_frame(self):
        """Get latest frame for display (thread-safe)"""
        with self.display_lock:
            frame = self.latest_display_frame
            self.latest_display_frame = None
            return frame

    def capturing(self):
        frame_count = 0
        start_time = time.time()
        last_display_time = 0
        display_interval = 1.0 / 30  # ~30 FPS

        try:
            with mss() as sct:
                monitor = sct.monitors[1]
                print(f"\nStarting background screen capture...")
                print(f"Resolution: {monitor['width']} x {monitor['height']}")

                while self.running:
                    try:
                        screenshot = sct.grab(monitor)
                        frame = np.array(screenshot)
                        frame = cv.cvtColor(frame, cv.COLOR_BGRA2BGR)

                        with self.frame_lock:
                            self.last_frame = frame

                        current_time = time.time()

                        # Update display frame at ~30 FPS (main thread will consume)
                        if current_time - last_display_time > display_interval:
                            with self.display_lock:
                                self.latest_display_frame = frame
                            last_display_time = current_time

                        # Update process frame on interval
                        if self.process_callback and (current_time - self.last_process_time) >= self.process_interval:
                            with self.process_lock:
                                self.latest_process_frame = frame
                            self.last_process_time = current_time

                            # Run callback in a separate thread
                            threading.Thread(
                                target=self.callback,
                                args=(frame,),
                                daemon=True
                            ).start()

                        frame_count += 1
                        time.sleep(0.01)

                    except Exception as e:
                        print(f"Error during capture: {str(e)}")
                        time.sleep(0.1)
                        continue

        except Exception as e:
            print(f"Critical error in capture: {str(e)}")
            if "Permission denied" in str(e) or "screen recording" in str(e).lower():
                print("\nScreen Recording permission denied. Check System Settings > Privacy.")
        finally:
            self.running = False
            elapsed = time.time() - start_time
            print(f"\nCapture thread finished. Captured {frame_count} frames.")
            if elapsed > 0:
                print(f"Total time: {elapsed:.2f}s, Average FPS: {frame_count/elapsed:.2f}")

    def callback(self, frame):
        try:
            self.process_callback(frame)
        except Exception as e:
            print(f"Error in process callback: {str(e)}")

SCREEN_CAP = ScreenCapture()

def screen_capture(process_callback=None, process_interval=1.0):
    """
    Starts screen capture in background with optional processing callback
    """
    return SCREEN_CAP.start_capture(process_callback, process_interval)

def stop_screen_capture():
    """
    Stops the background screen capture
    """
    return SCREEN_CAP.stop_capture()

def get_latest_frame():
    """
    Gets the latest captured frame
    """
    return SCREEN_CAP.get_latest_frame()



def setup_screen(overlay_agent: Overlay) -> dict:
    """
    Init screen cap areas by letting user pick regions via menu, then draw them on the overlay.
    :param overlay_agent: the PyQt Overlay window
    :return: dict of name -> {left, top, width, height}
    """
    color = (random.randint(0,255),random.randint(0,255),random.randint(0,255))
    picker = ScreenRegionPicker(color=color)
    region = picker.pick()
    print(region)
    tl = (region['left'], region['top'])
    br = (region['left'] + region['width'], region['top'] + region['height'])
    overlay_agent.add_rectangle(tl, br, False, color=region.get('color', (255, 0, 0)))

    return region




#READ VALUES

def get_value_crop(feature_crop: np.ndarray, feature_name: str, value_name: str) -> np.ndarray | None:
    """Return the sub-crop for a named value within a feature crop, ready for OCR.

    Args:
        feature_crop: the already-cropped feature image (BGR numpy array)
        feature_name: key in FEATURE_VALUES  e.g. "gamestats"
        value_name:   key within that feature e.g. "timer"
    Returns:
        cropped numpy array, or None if the name isn't found / crop is empty
    """
    spec = FEATURE_VALUES.get(feature_name, {}).get(value_name)
    if spec is None:
        return None
    h, w = feature_crop.shape[:2]
    (rx1, ry1), (rx2, ry2) = spec
    crop = feature_crop[int(ry1 * h):int(ry2 * h), int(rx1 * w):int(rx2 * w)]
    return crop if crop.size > 0 else None


def read_value(feature_crop: np.ndarray, feature_name: str, value_name: str,
               digits_only: bool = False) -> str:
    """OCR a named value from a feature crop.

    Args:
        feature_crop: the already-cropped feature image
        feature_name: key in FEATURE_VALUES
        value_name:   key within that feature
        digits_only:  if True, restrict tesseract to digits (for numeric-only fields)
    Returns:
        stripped OCR string, or "" on failure
    """
    crop = get_value_crop(feature_crop, feature_name, value_name)
    if crop is None:
        return ""
    gray = cv.cvtColor(crop, cv.COLOR_BGR2GRAY)
    config = '--psm 7 --oem 3' + (' -c tessedit_char_whitelist=0123456789:/' if digits_only else '')
    try:
        return pytesseract.image_to_string(gray, config=config).strip()
    except Exception:
        return ""


def read_all_values(feature_crop: np.ndarray, feature_name: str,
                    digits_only: bool = False) -> dict[str, str]:
    """OCR every defined value for a feature at once.

    Returns:
        dict of value_name -> OCR string
    """
    return {
        name: read_value(feature_crop, feature_name, name, digits_only=digits_only)
        for name in FEATURE_VALUES.get(feature_name, {})
    }


