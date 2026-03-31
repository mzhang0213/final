import sys
import threading
import time

import cv2 as cv
import numpy as np
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from server.utils import show_imgs, Overlay, SCREEN_SIZE, screen_capture, stop_screen_capture, \
    SCREEN_CAP, MLOG, setup_screen

# Populated after setup_screen — name -> {left, top, width, height}
REGIONS: dict = {}

overlay_queue = None
FRAME = None
DELAY_CAP = False


def process_frame(frame):
    """Called periodically on a background thread. Crops each selected region from the full frame."""
    global overlay_queue
    print(f"Processing frame at {time.strftime('%H:%M:%S')} - Shape: {frame.shape}")
    try:
        crops = {}
        for name, region in REGIONS.items():
            l, t, w, h = region['left'], region['top'], region['width'], region['height']
            crop = frame[t:t + h, l:l + w]
            crops[name] = crop

        overlay_queue = {
            "img_w": frame.shape[1],
            "img_h": frame.shape[0],
            "crops": crops,
        }
        print(f"Captured {len(crops)} region(s): {list(crops.keys())}")
    except Exception as e:
        print(f"Error processing frame: {str(e)}")


def export_state_capture():
    """Save each region crop and the full frame to disk."""
    global FRAME, overlay_queue
    print("capturing!")
    if FRAME is None:
        print("FRAME NOT DETECTED")
        return
    if overlay_queue is None:
        print("overlay_queue NOT DETECTED")
        return
    for name, crop in overlay_queue.get("crops", {}).items():
        if crop.size > 0:
            cv.imwrite(f"cap-box_{name}.png", crop)
    cv.imwrite("cap-main_frame.png", FRAME)


# tick is a periodic displayer and also detects wait keys for both cv and pyqt
def tick():
    """Called by QTimer on the main thread — safe for OpenCV GUI."""
    global overlay_queue, FRAME, DELAY_CAP
    if not SCREEN_CAP.running:
        app.quit()
        return

    frame = SCREEN_CAP.get_latest_display_frame()
    if frame is not None:
        FRAME = frame

    # Redraw overlay boxes from the fixed selected regions (no re-detection needed)
    if REGIONS:
        window.clearCanvas()
        for region in REGIONS.values():
            tl = (region['left'], region['top'])
            br = (region['left'] + region['width'], region['top'] + region['height'])
            window.add_rectangle(tl, br, False, color=region.get('color', (255, 0, 0)))

    if frame is not None:
        cv.imshow('Live Screen Capture', frame)

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
REGIONS = setup_screen(window)

if not REGIONS:
    print("No regions selected, exiting.")
    sys.exit(0)

print(f"Regions configured: {list(REGIONS.keys())}")
print("Starting screen capture...")
success = screen_capture(process_callback=process_frame, process_interval=1.0)

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