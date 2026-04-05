import json
import sys
import threading
import time

import cv2 as cv
import numpy as np

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QInputDialog

import os
import pyautogui

from finalog.resources.gemini import TransactionExtractor
from finalog.resources.sheets import insert_transactions
from finalog.utils import show_imgs, Overlay, SCREEN_SIZE, screen_capture, stop_screen_capture, \
    SCREEN_CAP, MLOG, setup_screen

# Populated after setup_screen — name -> {left, top, width, height}
REGION: dict = {}

# target_region = None
FRAME = None
DELAY_CAP = False
LAST_RESULT = None
STATUS = None  # None | "processing" | "done" | "logging" | "logged"

PROCESSING = False
LOGGING = False
SHOW_VIDEO = False

def process_cap_frame(img: np.ndarray):
    global LAST_RESULT, STATUS, PROCESSING
    if PROCESSING:
        return
    PROCESSING = True
    STATUS = "processing"

    def _run():
        global LAST_RESULT, STATUS, PROCESSING
        try:
            extractor = TransactionExtractor(api_keys=[os.getenv("GEMINI_API_KEY")])
            result = extractor.extract_from_frame(img)
            LAST_RESULT = result.get("transactions", [])
            STATUS = "done"
            print(json.dumps(result, indent=2))
        except Exception as e:
            STATUS = "done"
            print(f"Error processing frame: {e}")
        finally:
            PROCESSING = False

    threading.Thread(target=_run, daemon=True).start()


def log_to_sheets():
    global STATUS, COMPL_SHEETS, LOGGING
    if LOGGING:
        return
    LOGGING = True
    STATUS = "logging"

    def _run():
        global STATUS, COMPL_SHEETS, LOGGING
        try:
            n = insert_transactions(LAST_RESULT)
            print(f"logged {n} transactions to sheets!")
            STATUS = "logged"
        except Exception as e:
            print(f"Error logging to sheets: {e}")
            STATUS = "done"
        finally:
            COMPL_SHEETS = 0.0
            LOGGING = False

    threading.Thread(target=_run, daemon=True).start()



COMPL = 0.0
COMPL_SHEETS = 0.0
TABLE_HOVER: dict = {}  # (row, col) -> completion float
TABLE_HITBOXES: list = []
EDITING = False
# tick is a periodic displayer and also detects wait keys for both cv and pyqt
def tick():
    """Called by QTimer on the main thread — safe for OpenCV GUI."""
    global FRAME, DELAY_CAP, COMPL, COMPL_SHEETS, STATUS, TABLE_HOVER, TABLE_HITBOXES, EDITING, SHOW_VIDEO
    if not SCREEN_CAP.running:
        app.quit()
        return
    frame = SCREEN_CAP.get_latest_display_frame()
    if frame is not None:
        FRAME = frame

    if frame is not None and SHOW_VIDEO:
        cv.imshow('Live Screen Capture', frame)


    x, y = pyautogui.position()

    # Redraw overlay boxes from the fixed selected regions (no re-detection needed)
    if REGION:
        window.clearCanvas()
        tl = (REGION['left'], REGION['top'])
        br = (REGION['left'] + REGION['width'], REGION['top'] + REGION['height'])
        window.add_rectangle(tl, br, False, color=REGION.get('color', (255, 0, 0)))

        (bx1,by1), (bx2,by2) = window.draw_button(COMPL, "Hover to capture!")

        # Sheets button right below capture button
        sheets_y = by2 / SCREEN_SIZE[1] + 0.01
        (sx1,sy1), (sx2,sy2) = window.draw_button(
            COMPL_SHEETS, text="Log in Sheets", x=0.775, y=sheets_y,
            color=(30, 90, 160), progress_color=(23, 121, 232),
        )

        if LAST_RESULT:
            TABLE_HITBOXES = window.draw_transaction_table(LAST_RESULT, hover_zones=TABLE_HOVER)

            # Check table hover interactions
            keys = ["date", "company", "amount"]
            hovered_zone = None
            for hx1, hy1, hx2, hy2, row, col in TABLE_HITBOXES:
                if hx1 <= x <= hx2 and hy1 <= y <= hy2:
                    hovered_zone = (row, col)
                    compl = TABLE_HOVER.get((row, col), 0.0)
                    if int(compl * 100) / 100 == 0.98:
                        if col == -1:
                            # Delete row
                            LAST_RESULT.pop(row)
                            TABLE_HOVER = {k: v for k, v in TABLE_HOVER.items() if k[0] != row}
                        elif not EDITING:
                            # Edit cell
                            EDITING = True
                            field = keys[col]
                            old_val = LAST_RESULT[row].get(field) or ""
                            new_val, ok = QInputDialog.getText(
                                None, f"Edit {field}", f"{field}:", text=old_val
                            )
                            if ok and new_val is not None:
                                LAST_RESULT[row][field] = new_val if new_val else None
                            EDITING = False
                        TABLE_HOVER[(row, col)] = 0.0
                    elif compl <= 1.00:
                        TABLE_HOVER[(row, col)] = compl + 0.019
                    break

            # Reset hover for zones not being hovered
            for key in list(TABLE_HOVER.keys()):
                if key != hovered_zone:
                    TABLE_HOVER[key] = 0.0

        # Status indicator
        if STATUS == "processing":
            window.draw_status("Processing...", color=(200, 120, 0))
        elif STATUS == "done":
            window.draw_status("Results ready", color=(38, 148, 73))
        elif STATUS == "logging":
            window.draw_status("Logging to Sheets...", color=(30, 90, 160))
        elif STATUS == "logged":
            window.draw_status("Logged to Sheets!", color=(38, 148, 73))

        if not PROCESSING and bx1 <= x <= bx2 and by1 <= y <= by2:
            if int(COMPL*100)/100 == 0.98:
                process_cap_frame(FRAME[tl[1]:br[1],tl[0]:br[0]])
            if COMPL <= 1.00:
                COMPL += 0.019
        else:
            COMPL = 0.0

        if LAST_RESULT and not LOGGING and sx1 <= x <= sx2 and sy1 <= y <= sy2:
            if int(COMPL_SHEETS*100)/100 == 0.98:
                log_to_sheets()
            if COMPL_SHEETS <= 1.00:
                COMPL_SHEETS += 0.019
        else:
            COMPL_SHEETS = 0.0


    # cv window lifecycle — must happen on main thread
    if SHOW_VIDEO:
        cv.waitKey(1)
    else:
        cv.destroyAllWindows()
        cv.waitKey(1)


app = None
window = None

COMMANDS = {
    "q": "quit",
    "s": "toggle video feed",
    "c": "capture & extract transactions",
    "l": "log results to Google Sheets",
    "x": "clear current results",
    "i": "show status info",
    "?": "show available commands",
}


def _handle_command(cmd: str):
    global SHOW_VIDEO, LAST_RESULT, STATUS
    cmd = cmd.strip().lower()

    if cmd == "q":
        print("Quitting...")
        MLOG.dump(write_file=True)
        stop_screen_capture()
        app.quit()

    elif cmd == "s":
        SHOW_VIDEO = not SHOW_VIDEO
        print(f"Video feed {'ON' if SHOW_VIDEO else 'OFF'}")

    elif cmd == "c":
        if FRAME is not None and REGION:
            tl = (REGION['left'], REGION['top'])
            br = (REGION['left'] + REGION['width'], REGION['top'] + REGION['height'])
            print("Capturing frame...")
            process_cap_frame(FRAME[tl[1]:br[1], tl[0]:br[0]])
        else:
            print("No frame available yet." if FRAME is None else "No region configured.")

    elif cmd == "l":
        if LAST_RESULT:
            log_to_sheets()
        else:
            print("No results to log. Press 'c' first.")

    elif cmd == "i":
        print(f"  Status:  {STATUS or 'idle'}")
        print(f"  Video:   {'ON' if SHOW_VIDEO else 'OFF'}")
        print(f"  Results: {len(LAST_RESULT) if LAST_RESULT else 0} transactions")

    elif cmd == "x":
        LAST_RESULT = None
        STATUS = None
        print("Results cleared.")

    elif cmd == "?":
        print()
        for k, v in COMMANDS.items():
            print(f"  {k}  {v}")
        print()


def _cli_loop():
    """Runs on a daemon thread, reads single keypress and dispatches."""
    import tty, termios
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        while True:
            ch = sys.stdin.read(1)
            if ch:
                _handle_command(ch)
    except (EOFError, OSError):
        pass
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def run():
    """Entry point called by `finalog start`."""
    global app, window, REGION

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
    success = screen_capture()

    if not success:
        print("Failed to start screen capture")
        sys.exit(1)

    print()
    print("─" * 40)
    print("  finalog is running (video OFF)")
    print()
    print("  c  capture    s  video    l  log")
    print("  x  clear      i  status   q  quit")
    print("  ?  help")
    print("─" * 40)
    print()

    # Start CLI input thread
    threading.Thread(target=_cli_loop, daemon=True).start()

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