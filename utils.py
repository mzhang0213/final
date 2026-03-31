import cv2 as cv
import numpy as np
import os
import pytesseract
from matplotlib import pyplot as plt
import sys
from PyQt6.QtWidgets import QApplication, QMainWindow, QWidget
from PyQt6.QtCore import Qt, QRect
from PyQt6.QtGui import QPainter, QPen, QColor, QBrush
from mss import mss
import time
import platform
import threading
from queue import Queue

import logging

from server.resources.region_menu import RegionMenu
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

def find_outliers_iqr(data: list) -> tuple[list, list]:
    """
    Find outliers using the Interquartile Range (IQR) method.

    :param data: List of numeric values
    :return: (filtered_data, indices)
    """
    data = np.array(data)
    if len(data) == 0:
        return [], []

    # Convert to numpy array
    arr_data = np.array(data)

    # Handle both scalar and coordinate data
    if arr_data.ndim == 1:  # 1D array (scalar values)
        Q1 = np.percentile(arr_data, 25)
        Q3 = np.percentile(arr_data, 75)
        IQR = Q3 - Q1
        lower_bound = Q1 - 1.5 * IQR
        upper_bound = Q3 + 1.5 * IQR

        filtered_data = []
        indices = []
        for i, val in enumerate(arr_data):
            if lower_bound <= val <= upper_bound:
                filtered_data.append(data[i])  # Use original data to preserve type
                indices.append(i)
    else:  # 2D array (coordinates or multi-dimensional data)
        # Process each dimension separately
        filtered_data = []
        indices = []
        for i, coord in enumerate(arr_data):
            coord_valid = True
            for dim_idx in range(len(coord)):
                dim_data = arr_data[:, dim_idx]
                Q1 = np.percentile(dim_data, 25)
                Q3 = np.percentile(dim_data, 75)
                IQR = Q3 - Q1
                lower_bound = Q1 - 1.5 * IQR
                upper_bound = Q3 + 1.5 * IQR

                if not (lower_bound <= coord[dim_idx] <= upper_bound):
                    coord_valid = False
                    break

            if coord_valid:
                filtered_data.append(data[i])  # Use original data to preserve type
                indices.append(i)

    return filtered_data, indices

def ensure_precision(data_x: list, data_y: list) -> bool:
    thresh_factor = 0.2
    x_thresh = SCREEN_SIZE[0]*thresh_factor
    y_thresh = SCREEN_SIZE[1]*thresh_factor
    '''
    notes on threshold:
    - not applying a hard pixel cap for std dev
    - thresholds are based on screen size (ie x_thresh = within deviation 5% len of screen size)
    images come in based on screen size of the user's screen, and thus the feature target images are also scaled, thus requiring this threshold to scale based on screen size.
    '''
    for pts in data_x:
        dev = np.std(pts)
        MLOG.log_msg("x: "+str(dev))
        if dev > x_thresh:
            return False
    for pts in data_y:
        dev = np.std(pts)
        MLOG.log_msg("y: "+str(dev))
        if dev > y_thresh:
            return False
    return True

def ensure_fit(tl, br, target_w, target_h, img_w, img_h):
    """
    Expands the search area if it's smaller than the target image,
    clamped to the actual image bounds.
    """
    x1, y1 = tl
    x2, y2 = br

    area_w = x2 - x1
    area_h = y2 - y1

    # Expand symmetrically if too small
    if area_w < target_w:
        diff = target_w - area_w + 2
        if x2+diff>=img_w:
            #if it were to send over the border, add first this side then add rest to other side
            diff-=img_w-x2
            x2=img_w
            x1=max(0,x1-diff)
        elif x1-diff<=0:
            diff-=x1
            x1=0
            x2=min(img_w,x2+diff)
        else:
            #in the middle not touching borders
            x1 = max(0, x1 - diff // 2)
            x2 = min(img_w, x2 + diff // 2 + diff % 2)

    if area_h < target_h:
        diff = target_h - area_h + 2
        if y2+diff>=img_h:
            diff-=img_h-y2
            y2=img_h
            y1=max(0,y1-diff)
        elif y1-diff<=0:
            diff-=y1
            y1=0
            y2=min(img_h,y2+diff)
        else:
            y1 = max(0, y1 - diff // 2)
            y2 = min(img_h, y2 + diff // 2 + diff % 2)

    return (x1, y1), (x2, y2)


def get_screen_coords(w:int, h:int, scale:tuple[tuple[float,float],tuple[float,float]])-> tuple[tuple[int, int], tuple[int, int]]:
    return (int(scale[0][0]*w),int(scale[0][1]*h)),(int(scale[1][0]*w),int(scale[1][1]*h))

class GameState:

    def __init__(self, img):
        self.img = img #the frame of the game state to analyze
        self.key_images = {}
        #todo: rework this cuz shouldn't be duplicated for each game state
        keys_dir = os.path.join(os.path.dirname(__file__), 'keys')
        for key in FEATURES:
            path = os.path.join(keys_dir, f"{key}.png")
            if os.path.exists(path):
                self.key_images[key] = cv.imread(path)

    def get_box(self, target, tl:tuple[int,int], br:tuple[int,int]) -> tuple[tuple[int, int], tuple[int, int]]:
        """
        Retrieves the bounding box on the screen containing target (key image).
        :param br:
        :param tl:
        :param target: target image as np.array image
        :return: bounding box - (top left coordinate, bottom right coordinate)
        """
        #Source: https://docs.opencv.org/4.x/d4/dc6/tutorial_py_template_matching.html
        #(note minor edits made)
        assert self.img is not None, "this game state's img could not be read"
        assert target is not None, "target image is None"
        if tl == (-1, -1):
            tl = (0,0)
        if br == (-1, -1):
            br = tuple(self.img.shape[::-1])

        # Store original coordinates offset
        offset_x, offset_y = tl[0], tl[1]

        # Template matching works best in grayscale or with matched channels
        #also ensure that the bounding box we are searching in will guarantee to fit the tgt

        img = self.img[tl[1]:br[1],tl[0]:br[0]]
        img = cv.cvtColor(img, cv.COLOR_BGR2GRAY)
        if len(target.shape) == 3:
            target = cv.cvtColor(target, cv.COLOR_BGR2GRAY)

        w, h = target.shape[::-1]

        # cv.imwrite(f"img{tl[0]}.png", img)
        # cv.imwrite(f"target{target.shape[1]}.png", target)

        # All the 6 methods for comparison in a list
        methods = ['TM_CCOEFF', 'TM_CCOEFF_NORMED', 'TM_CCORR',
                   'TM_CCORR_NORMED', 'TM_SQDIFF', 'TM_SQDIFF_NORMED']

        results = []

        for m in methods:
            method = getattr(cv, m)
            # Apply template Matching
            res = cv.matchTemplate(img,target,method)
            min_val, max_val, min_loc, max_loc = cv.minMaxLoc(res)

            # If the method is TM_SQDIFF or TM_SQDIFF_NORMED, take minimum
            if method in [cv.TM_SQDIFF, cv.TM_SQDIFF_NORMED]:
                top_left = int(min_loc[0]), int(min_loc[1])
            else:
                top_left = int(max_loc[0]), int(max_loc[1])
            bottom_right = int(top_left[0] + w), int(top_left[1] + h)

            # Adjust coordinates back to original image space
            top_left = (top_left[0] + offset_x, top_left[1] + offset_y)
            bottom_right = (bottom_right[0] + offset_x, bottom_right[1] + offset_y)

            curr_box = top_left,bottom_right
            results.append(curr_box)

            # cv.rectangle(img,top_left, bottom_right, 255, 2)

            # plt.subplot(121),plt.imshow(res,cmap = 'gray')
            # plt.title('Matching Result'), plt.xticks([]), plt.yticks([])
            # plt.subplot(122),plt.imshow(img,cmap = 'gray')
            # plt.title('Detected Point'), plt.xticks([]), plt.yticks([])
            # plt.suptitle(meth)
            #
            # plt.show()

        # Extract x and y coordinates separately for outlier detection
        tl_x_coords = [r[0][0] for r in results]
        tl_y_coords = [r[0][1] for r in results]
        br_x_coords = [r[1][0] for r in results]
        br_y_coords = [r[1][1] for r in results]

        if not ensure_precision([tl_x_coords,br_x_coords],[br_y_coords,tl_y_coords]):
            MLOG.log_error("failed precision test")
            return (0,0),(0,0)

        # Find outliers for each coordinate separately
        _, tl_x_outlier_indices = find_outliers_iqr(tl_x_coords)
        _, tl_y_outlier_indices = find_outliers_iqr(tl_y_coords)
        _, br_x_outlier_indices = find_outliers_iqr(br_x_coords)
        _, br_y_outlier_indices = find_outliers_iqr(br_y_coords)

        pruned = [] #this is just the results tuples pruned for outliers
        outlier_indicies = set(tl_x_outlier_indices + tl_y_outlier_indices +
                               br_x_outlier_indices + br_y_outlier_indices)
        for i in range(len(results)):
            if i not in outlier_indicies:
                pruned.append(results[i])

        if not pruned:
            #fallback if too few results
            return results[0] #TODO: always chooses the first matching

        # Calculate mean of pruned results
        final_tl = (round(np.mean([r[0][0] for r in pruned])),
                   int(np.mean([r[0][1] for r in pruned])))
        final_br = (int(np.mean([r[1][0] for r in pruned])),
                   int(np.mean([r[1][1] for r in pruned])))

        return final_tl, final_br


    # def get_boxes(self):
    #     return {
    #
    #     }

    def get_boxes(self):
        """
        Analyzes the current game screen and returns structured data based on detected keys.
        """
        data = {}
        img_h,img_w = self.img.shape[:2]
        scale_x = SCREEN_SIZE[0] / BASE_RESOLUTION[0]
        scale_y = SCREEN_SIZE[1] / BASE_RESOLUTION[1]

        for key_name in FEATURES:
            if key_name not in self.key_images:
                continue

            curr_feature = self.key_images[key_name]

            kh, kw = curr_feature.shape[:2]
            new_kw = max(1, int(kw * scale_x))
            new_kh = max(1, int(kh * scale_y))
            scaled_feature = cv.resize(curr_feature, (new_kw, new_kh))
            searcharea_tl,searcharea_br = get_screen_coords(img_w,img_h,FEATURES[key_name])
            searcharea_tl, searcharea_br = ensure_fit(
                searcharea_tl, searcharea_br, new_kw, new_kh, img_w, img_h
            )

            tl, br = self.get_box(scaled_feature, searcharea_tl, searcharea_br)
            feature = self.img[tl[1]:br[1], tl[0]:br[0]]
            fh,fw = feature.shape[:2]

            if key_name == "gamestats":
                # KDA: approx 250 to 400
                kda_box = (int(0.42 * fw),(0.68 * fw)),(0,fh)
                # CS: approx 450 to 520
                cs_box = (int(0.76 * fw),int(0.88 * fw)),(0,fh)
                # Clock: approx 530 to 590
                clock_box = (int(0.9 * fw),fw),(0,fh)
                # Score: approx 0 to 150
                score_box = (0,int(0.25 * fw)),(0,fh)

                data["gamestats"] = {
                    "full": (tl,br),
                    "sections": {
                        "score": score_box,
                        "kda": kda_box,
                        "cs": cs_box,
                        "clock": clock_box
                    }
                }
            else:
                data[key_name] = {
                    "full": (tl,br)
                }

        return data

    def extract_box(self,tl:tuple[int,int],br:tuple[int,int]):
        return self.img[tl[1]:br[1],tl[0]:br[0]]

    def display_boxes(self):
        boxes = self.get_boxes()
        for key in FEATURES:
            if boxes[key] is None:
                continue
            og = self.img.copy()
            cv.rectangle(og, boxes[key]["full"][0], boxes[key]["full"][1], (0,255,0))
            cv.imshow(key, og)

    def export_state(self):
        return self.get_boxes()

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

        self.resize(SCREEN_SIZE[0], SCREEN_SIZE[1])
        self.show()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

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

    def add_rectangle(self, top_left, bottom_right, filled=True, color=(255, 0, 0)):
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

    def clearCanvas(self):
        """Clear all shapes from the canvas"""
        self.rectangles.clear()
        self.circles.clear()
        self.arrows.clear()
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
    regions = RegionMenu(list(FEATURES.keys())).run()

    # Draw each selected region on the overlay in its assigned color
    overlay_agent.clearCanvas()
    for region in regions.values():
        tl = (region['left'], region['top'])
        br = (region['left'] + region['width'], region['top'] + region['height'])
        overlay_agent.add_rectangle(tl, br, False, color=region.get('color', (255, 0, 0)))

    return regions




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


