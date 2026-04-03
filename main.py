import json
import sys
import threading
import time

import cv2 as cv
import numpy as np

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

import os
from dotenv import load_dotenv
load_dotenv()

import pyautogui

from resources.gemini import TransactionExtractor
from utils import show_imgs, Overlay, SCREEN_SIZE, screen_capture, stop_screen_capture, \
    SCREEN_CAP, MLOG, setup_screen

# Populated after setup_screen — name -> {left, top, width, height}
REGION: dict = {}

target_region = None
FRAME = None
DELAY_CAP = False


def process_frame(frame):
    """Called periodically on a background thread. Crops each selected region from the full frame."""
    global target_region
    print(f"Processing frame at {time.strftime('%H:%M:%S')} - Shape: {frame.shape}")
    try:
        crops = {}
        for name, region in REGION.items():
            l, t, w, h = region['left'], region['top'], region['width'], region['height']
            crop = frame[t:t + h, l:l + w]
            crops[name] = crop

        target_region = {
            "img_w": frame.shape[1],
            "img_h": frame.shape[0],
            "crops": crops,
        }
        print(f"Captured {len(crops)} region(s): {list(crops.keys())}")
    except Exception as e:
        print(f"Error processing frame: {str(e)}")


def export_state_capture():
    """Save each region crop and the full frame to disk."""
    global FRAME, target_region
    print("capturing!")
    if FRAME is None:
        print("FRAME NOT DETECTED")
        return
    if target_region is None:
        print("overlay_queue NOT DETECTED")
        return
    for name, crop in target_region.get("crops", {}).items():
        if crop.size > 0:
            cv.imwrite(f"cap-box_{name}.png", crop)
    cv.imwrite("cap-main_frame.png", FRAME)


def draw_button(agent: Overlay, completion: float, x:float=0.775, y:float=0.15):
    _tl = int(SCREEN_SIZE[0]*x),int(SCREEN_SIZE[1]*y)
    tb_w,tb_h = agent.add_text_box(_tl[0],_tl[1],"Hover to capture!", (38, 148, 73))

    _br_prog = int(SCREEN_SIZE[0]*x + tb_w * completion),  int(SCREEN_SIZE[1]*y + tb_h)
    agent.add_rectangle(_tl,_br_prog,True, (55, 222, 108))
    return _tl, (int(SCREEN_SIZE[0]*x + tb_w),  int(SCREEN_SIZE[1]*y + tb_h))


def process_cap_frame(img: np.ndarray):
    extractor = TransactionExtractor(api_keys=[os.getenv("GEMINI_API_KEY")])
    result = extractor.extract_from_frame(img)
    print(json.dumps(result, indent=2))



COMPL = 0.0
# tick is a periodic displayer and also detects wait keys for both cv and pyqt
def tick():
    """Called by QTimer on the main thread — safe for OpenCV GUI."""
    global target_region, FRAME, DELAY_CAP, COMPL
    if not SCREEN_CAP.running:
        app.quit()
        return
    frame = SCREEN_CAP.get_latest_display_frame()
    if frame is not None:
        FRAME = frame

    if frame is not None:
        cv.imshow('Live Screen Capture', frame)


    x, y = pyautogui.position()

    # Redraw overlay boxes from the fixed selected regions (no re-detection needed)
    if REGION:
        window.clearCanvas()
        tl = (REGION['left'], REGION['top'])
        br = (REGION['left'] + REGION['width'], REGION['top'] + REGION['height'])
        window.add_rectangle(tl, br, False, color=REGION.get('color', (255, 0, 0)))

        (bx1,by1), (bx2,by2) = draw_button(window, COMPL)

        if bx1 <= x <= bx2 and by1 <= y <= by2:
            if int(COMPL*100)/100 == 0.98:
                print("processing frame...")
                process_cap_frame(FRAME[tl[1]:br[1],tl[0]:br[0]])
                print("processed!!")
            if COMPL <= 1.00:
                COMPL += 0.019
        else:
            COMPL = 0.0


    key = cv.waitKey(1)
    if key & 0xFF == ord('q'):
        print("'q' pressed, stopping...")
        MLOG.dump(write_file=True)
        stop_screen_capture()
        app.quit()
    if key & 0xFF == ord('c'):
        export_state_capture()
        DELAY_CAP = False
    if key & 0xFF == ord('v'):
        if not DELAY_CAP:
            print(" -- DELAY CAP INITIATED -- ")
            DELAY_CAP = True
            threading.Timer(3, export_state_capture).start()


app = QApplication.instance() or QApplication(sys.argv)
window = Overlay()

# Let user pick regions before starting capture
print("Running region setup...")
REGION = setup_screen(window)

if not REGION:
    print("No regions selected, exiting.")
    sys.exit(0)

print(f"Regions configured: {list(REGION.keys())}")
print("Starting screen capture...")
success = screen_capture() #no process_capture used

if not success:
    print("Failed to start screen capture")
    sys.exit(1)

# QTimer fires tick() every 16ms (~60fps) on the main thread inside app.exec()
timer = QTimer()
timer.timeout.connect(tick)
timer.start(16)

try:
    sys.exit(app.exec())
except KeyboardInterrupt:
    print("\nStopping...")
    stop_screen_capture()
    cv.destroyAllWindows()