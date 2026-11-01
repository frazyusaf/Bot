"""
Clash of Clans Attack-Loop Bot (with GUI controls)
===================================================
Reads click/drag regions from All_Tags.json and replays the attack
sequence: Attack -> Find Match -> Attack -> zoom out -> pan camera ->
Deploy spells/troops -> Wait -> End Battle -> Return Home -> repeat.

A small control panel window opens with Start / Pause / Resume / Stop
buttons so you don't have to touch the terminal.

REQUIREMENTS
------------
    pip install pyautogui
    (tkinter ships with standard Python on Windows/macOS. On Linux:
     sudo apt install python3-tk)

BEFORE YOU RUN THIS
--------------------
1. Put All_Tags.json in the same folder as this script.
2. Set REFERENCE_WIDTH / REFERENCE_HEIGHT to the resolution the
   coordinates in the JSON were captured at (default assumes 2560x1440,
   based on your JSON's max coordinates of ~2464 x ~1090).
3. Keep your emulator window in a fixed position/size every run.
4. Run `python calibrate.py` first to sanity check the click points.
5. Check ZOOM_OUT_POINT / PAN_START / PAN_END below - these aren't in
   your JSON, so they're rough screen-percentage defaults. Adjust to
   taste after watching the bot run once.

HOW THE CONTROLS WORK
----------------------
- Start: begins a 5 second countdown (switch to your emulator window
  during this time), then starts looping attacks.
- Pause / Resume: pauses before the next click/tap (won't cut a
  deploy sequence off mid-tap). The 40s post-deploy wait and 3s
  post-battle-start wait are also pause-aware.
- Stop: stops the bot cleanly after the current action finishes.
- Failsafe: as always, slam your mouse into the screen's top-left
  corner at any time to abort pyautogui instantly.
"""

import json
import math
import queue
import random
import sys
import threading
import time
from pathlib import Path

try:
    import pyautogui
except ImportError:
    sys.exit("pyautogui is not installed. Run: pip install pyautogui")

try:
    import tkinter as tk
    from tkinter import ttk
except ImportError:
    sys.exit(
        "tkinter is not available. On Windows/macOS it ships with Python "
        "by default - try reinstalling Python. On Linux: "
        "sudo apt install python3-tk"
    )

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.1

# --------------------------------------------------------------------------
# CONFIG - edit these to match your setup
# --------------------------------------------------------------------------

TAGS_FILE = (
    Path(sys.executable).parent / "All_Tags.json"
    if getattr(sys, "frozen", False)
    else Path(__file__).parent / "All_Tags.json"
)

REFERENCE_WIDTH = 2560
REFERENCE_HEIGHT = 1440

OFFSET_X = 0
OFFSET_Y = 0

# Spell deploy counts - both spells get 4 taps on each deploy point
SPELL1_DEPLOY1_COUNT = 4
SPELL1_DEPLOY2_COUNT = 4
SPELL2_DEPLOY1_COUNT = 4
SPELL2_DEPLOY2_COUNT = 4

# Valkyrie deploy counts, spread along each line
VALK_AREA01_COUNT = 6
VALK_AREA02_COUNT = 6
VALK_AREA03_COUNT = 30


WAIT_AFTER_BATTLE_START = 3     # seconds, after "03- Attack Button"
WAIT_AFTER_DEPLOY = 40          # seconds, before ending battle
START_DELAY_SECONDS = 5         # countdown after clicking Start, to switch windows

# Camera controls, run right after the WAIT_AFTER_BATTLE_START wait.
# These points aren't in your JSON (they're not "buttons"), so they're
# rough defaults in reference-resolution percentages - eyeball and
# adjust after a test run.
ZOOM_OUT_POINT = (REFERENCE_WIDTH * 0.5, REFERENCE_HEIGHT * 0.5)  # where to scroll
ZOOM_OUT_TICKS = 6              # scroll "clicks" to zoom out
                                 # (flip sign below if your emulator zooms
                                 # the wrong way)

# Camera pan drag - uses the "click and drag" line region from the JSON.
# Single continuous motion: mouse button goes down at the start, stays
# held for one smooth move straight to the end point, then releases -
# it never lets go mid-drag. Duration is kept short for a fast swipe.
PAN_DRAG_DURATION = 0.12

# Rectangle regions: click a random point inside the button's full
# clickable area. PADDING_RATIO shrinks the box inward on each edge.
RECT_PADDING_RATIO = 0.15

# Line regions (troop/spell deploy lines): sideways jitter in pixels
# (reference-resolution units) plus per-tap timing.
LINE_PERP_JITTER_PX = 12
LINE_TAP_MIN_DELAY = 0.05
LINE_TAP_MAX_DELAY = 0.12

# Delay after one-off navigation clicks (Attack Button, Find a Match,
# End Battle, etc.) - a bit more deliberate since these aren't time
# critical and there's less need to rush them.
NAV_MIN_DELAY = 0.20
NAV_MAX_DELAY = 0.40

# Delay after rapid repeated taps (spell deploys, hero select/deploy/
# ability). Kept short since these need to land quickly in-game.
TAP_MIN_DELAY = 0.08
TAP_MAX_DELAY = 0.18

# How long to wait after a hero is deployed before tapping its icon
# again to trigger its ability (needs a beat for the hero to actually
# spawn on the field first).
HERO_ABILITY_DELAY = 0.4

# Mouse-move duration range for taps (separate from click delay above)
MOVE_MIN_DURATION = 0.04
MOVE_MAX_DURATION = 0.10

# 0 = run forever, otherwise stop after N attacks
MAX_LOOPS = 0

# --------------------------------------------------------------------------
# LOAD REGIONS
# --------------------------------------------------------------------------

def load_regions(path: Path) -> dict:
    data = json.loads(path.read_text())
    return {r["tag"]: r for r in data["regions"]}


REGIONS = load_regions(TAGS_FILE)

screen_w, screen_h = pyautogui.size()
SCALE_X = screen_w / REFERENCE_WIDTH
SCALE_Y = screen_h / REFERENCE_HEIGHT


def scale_point(x, y):
    return (
        OFFSET_X + int(x * SCALE_X),
        OFFSET_Y + int(y * SCALE_Y),
    )


def random_point_in_rect(r):
    """Random point inside the rectangle, inset by RECT_PADDING_RATIO on
    each edge so we stay well clear of the button's border."""
    w = r["x2"] - r["x1"]
    h = r["y2"] - r["y1"]
    pad_x = w * RECT_PADDING_RATIO
    pad_y = h * RECT_PADDING_RATIO
    x = random.uniform(r["x1"] + pad_x, r["x2"] - pad_x)
    y = random.uniform(r["y1"] + pad_y, r["y2"] - pad_y)
    return x, y


# --------------------------------------------------------------------------
# PAUSE / STOP CONTROL
# --------------------------------------------------------------------------

class StopBot(Exception):
    """Raised internally to unwind out of the attack sequence on Stop."""


class BotController:
    def __init__(self):
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()   # set = currently paused
        self.status_queue = queue.Queue()
        self.thread = None

    def log(self, msg):
        print(msg)
        self.status_queue.put(msg)

    def check_control(self):
        """Block while paused; raise StopBot if a stop was requested."""
        while self.pause_event.is_set():
            if self.stop_event.is_set():
                raise StopBot()
            time.sleep(0.2)
        if self.stop_event.is_set():
            raise StopBot()

    def sleep_checked(self, seconds):
        """Sleep for `seconds`, but stay responsive to pause/stop."""
        end_time = time.time() + seconds
        while time.time() < end_time:
            self.check_control()
            time.sleep(min(0.5, max(0.0, end_time - time.time())))
        self.check_control()


CONTROLLER = BotController()


def human_delay(min_delay=NAV_MIN_DELAY, max_delay=NAV_MAX_DELAY):
    time.sleep(random.uniform(min_delay, max_delay))


# --------------------------------------------------------------------------
# LOW-LEVEL ACTIONS
# --------------------------------------------------------------------------

def click_tag(tag: str, min_delay=NAV_MIN_DELAY, max_delay=NAV_MAX_DELAY):
    """Click a rectangle region at a random point within its bounds."""
    CONTROLLER.check_control()
    r = REGIONS.get(tag)
    if r is None:
        raise KeyError(f"Tag '{tag}' not found in {TAGS_FILE.name}")
    if r["shape"] != "rectangle":
        raise ValueError(f"Tag '{tag}' is not a rectangle, use deploy_on_line() instead")

    rx, ry = random_point_in_rect(r)
    x, y = scale_point(rx, ry)

    CONTROLLER.log(f"[click] {tag} -> ({x}, {y})")
    pyautogui.moveTo(x, y, duration=random.uniform(MOVE_MIN_DURATION, MOVE_MAX_DURATION))
    pyautogui.click()
    human_delay(min_delay, max_delay)


def click_tag_n(tag: str, count: int, min_delay=TAP_MIN_DELAY, max_delay=TAP_MAX_DELAY):
    for _ in range(count):
        click_tag(tag, min_delay=min_delay, max_delay=max_delay)


def deploy_on_line(tag: str, count: int):
    """Deploy `count` troops spread along a line region, each at a random
    point along the line (not the endpoints) with a small sideways jitter
    perpendicular to the line so troops fan out naturally."""
    CONTROLLER.check_control()
    r = REGIONS.get(tag)
    if r is None:
        raise KeyError(f"Tag '{tag}' not found in {TAGS_FILE.name}")
    if r["shape"] != "line":
        raise ValueError(f"Tag '{tag}' is not a line, use click_tag() instead")

    x1, y1, x2, y2 = r["x1"], r["y1"], r["x2"], r["y2"]
    dx, dy = x2 - x1, y2 - y1
    length = math.hypot(dx, dy) or 1.0
    perp_x, perp_y = -dy / length, dx / length

    CONTROLLER.log(f"[deploy] {tag} x{count}")

    for _ in range(count):
        CONTROLLER.check_control()
        t = random.uniform(0.08, 0.92)
        px = x1 + dx * t
        py = y1 + dy * t
        offset = random.uniform(-LINE_PERP_JITTER_PX, LINE_PERP_JITTER_PX)
        px += perp_x * offset
        py += perp_y * offset

        sx, sy = scale_point(px, py)
        pyautogui.moveTo(sx, sy, duration=random.uniform(MOVE_MIN_DURATION * 0.6, MOVE_MAX_DURATION * 0.6))
        pyautogui.click()
        time.sleep(random.uniform(LINE_TAP_MIN_DELAY, LINE_TAP_MAX_DELAY))


def zoom_out():
    """Scroll out on the battlefield to get a wider view."""
    CONTROLLER.check_control()
    x, y = scale_point(*ZOOM_OUT_POINT)
    CONTROLLER.log(f"[zoom] scrolling out {ZOOM_OUT_TICKS} ticks at ({x},{y})")
    pyautogui.moveTo(x, y, duration=0.15)
    pyautogui.scroll(-abs(ZOOM_OUT_TICKS), x=x, y=y)
    human_delay()


def drag_line(tag: str, duration=PAN_DRAG_DURATION):
    """Press down at the start of a line region and drag in one single,
    continuous motion straight to the end point - the mouse button is
    held down for the entire move and only releases once the drag is
    fully complete. Used for camera pans/swipes, not troop deploys."""
    CONTROLLER.check_control()
    r = REGIONS.get(tag)
    if r is None:
        raise KeyError(f"Tag '{tag}' not found in {TAGS_FILE.name}")
    if r["shape"] != "line":
        raise ValueError(f"Tag '{tag}' is not a line, use click_tag() instead")

    sx, sy = scale_point(r["x1"], r["y1"])
    ex, ey = scale_point(r["x2"], r["y2"])
    CONTROLLER.log(f"[drag] {tag}: ({sx},{sy}) -> ({ex},{ey})")

    pyautogui.moveTo(sx, sy, duration=0.08)
    pyautogui.mouseDown()
    pyautogui.moveTo(ex, ey, duration=duration)   # single smooth tween, button held the whole way
    pyautogui.mouseUp()
    human_delay(min_delay=TAP_MIN_DELAY, max_delay=TAP_MAX_DELAY)


# --------------------------------------------------------------------------
# ATTACK SEQUENCE
# --------------------------------------------------------------------------

def run_attack_sequence(attack_num: int):
    CONTROLLER.log(f"=== Attack #{attack_num} ===")

    # 1. Open attack menu
    click_tag("Attack Button")

    # 2. Find a match
    click_tag("Find a Match")

    # 3. Start the battle (opponent found)
    click_tag("03- Attack Button")
    CONTROLLER.log(f"Waiting {WAIT_AFTER_BATTLE_START}s for battle to load...")
    CONTROLLER.sleep_checked(WAIT_AFTER_BATTLE_START)

    # Camera setup: zoom out, then pan so the base sits on the right side
    zoom_out()
    drag_line("click and drag")

    # 4. Spell 01 -> deploy both spell points
    click_tag("Spell 01 Select")
    click_tag_n("Spell Deploy 01", SPELL1_DEPLOY1_COUNT)
    click_tag_n("spell deploy 02", SPELL1_DEPLOY2_COUNT)

    # 5. Spell 02 -> deploy once each
    click_tag("Spell 02 select")
    click_tag_n("Spell Deploy 01", SPELL2_DEPLOY1_COUNT)
    click_tag_n("spell deploy 02", SPELL2_DEPLOY2_COUNT)

    # 8. Select Valkyries
    click_tag("04- Valk Select")

    # 9-11. Deploy Valkyries: 6 on area 01, 6 on area 02, 30 on area 03
    deploy_on_line("Deploy area 01", VALK_AREA01_COUNT)
    deploy_on_line("Deploy area 02", VALK_AREA02_COUNT)
    deploy_on_line("Deploy area 03", VALK_AREA03_COUNT)

    # 12. Heroes: select -> deploy -> tap again for ability, one at a time
    #     (selecting the next hero cancels the previous selection, so each
    #     hero has to be fully placed before moving to the next one)
    for hero_tag in ("Hero 01 Select", "Hero 02 Select", "Hero 03 select", "Hero 04 select"):
        click_tag(hero_tag, min_delay=TAP_MIN_DELAY, max_delay=TAP_MAX_DELAY)   # select
        deploy_on_line("Deploy area 03", 1)                                     # place on field
        CONTROLLER.sleep_checked(HERO_ABILITY_DELAY)                            # let it spawn
        click_tag(hero_tag, min_delay=TAP_MIN_DELAY, max_delay=TAP_MAX_DELAY)   # trigger ability

    CONTROLLER.log(f"Waiting {WAIT_AFTER_DEPLOY}s for battle to play out...")
    CONTROLLER.sleep_checked(WAIT_AFTER_DEPLOY)

    # 13. End battle
    click_tag("End Battle")

    # 14. Confirm
    click_tag("Okay Button")

    # 15. Return home
    click_tag("Return Home Button")

    CONTROLLER.log(f"=== Attack #{attack_num} complete ===")


# --------------------------------------------------------------------------
# BOT WORKER THREAD
# --------------------------------------------------------------------------

def bot_worker():
    try:
        CONTROLLER.log(f"Starting in {START_DELAY_SECONDS}s - switch to your emulator now...")
        for i in range(START_DELAY_SECONDS, 0, -1):
            CONTROLLER.check_control()
            CONTROLLER.log(f"Starting in {i}...")
            time.sleep(1)

        attack_num = 0
        while MAX_LOOPS == 0 or attack_num < MAX_LOOPS:
            CONTROLLER.check_control()
            attack_num += 1
            run_attack_sequence(attack_num)
            CONTROLLER.log(f"__ATTACK_DONE__{attack_num}")
            CONTROLLER.sleep_checked(random.uniform(1.5, 3.0))
    except StopBot:
        CONTROLLER.log("Bot stopped.")
    except pyautogui.FailSafeException:
        CONTROLLER.log("Failsafe triggered - bot stopped.")
    except Exception as e:
        CONTROLLER.log(f"Error: {e}")
    finally:
        CONTROLLER.log("__DONE__")


# --------------------------------------------------------------------------
# GUI
# --------------------------------------------------------------------------

class BotGUI:
    def __init__(self, root):
        self.root = root
        root.title("CoC Attack Bot")
        root.geometry("400x230")
        root.resizable(False, False)

        ttk.Label(
            root, text="Clash of Clans Attack Bot", font=("Segoe UI", 13, "bold")
        ).pack(pady=(12, 4))

        self.status_var = tk.StringVar(value="Idle - click Start")
        self.attack_var = tk.StringVar(value="Attacks completed: 0")

        ttk.Label(root, textvariable=self.status_var, font=("Segoe UI", 10), wraplength=370).pack(pady=2)
        ttk.Label(root, textvariable=self.attack_var, font=("Segoe UI", 10)).pack(pady=2)

        btn_frame = ttk.Frame(root)
        btn_frame.pack(pady=14)

        self.start_btn = ttk.Button(btn_frame, text="Start", width=10, command=self.on_start)
        self.start_btn.grid(row=0, column=0, padx=5)

        self.pause_btn = ttk.Button(btn_frame, text="Pause", width=10, command=self.on_pause, state="disabled")
        self.pause_btn.grid(row=0, column=1, padx=5)

        self.stop_btn = ttk.Button(btn_frame, text="Stop", width=10, command=self.on_stop, state="disabled")
        self.stop_btn.grid(row=0, column=2, padx=5)

        ttk.Label(
            root,
            text="Failsafe: drag your mouse to the screen's top-left\ncorner at any time to abort instantly.",
            font=("Segoe UI", 8),
            foreground="gray",
            justify="center",
        ).pack(pady=(10, 0))

        self.poll_queue()

    def on_start(self):
        CONTROLLER.stop_event.clear()
        CONTROLLER.pause_event.clear()
        self.attack_var.set("Attacks completed: 0")
        self.start_btn.config(state="disabled")
        self.pause_btn.config(state="normal", text="Pause")
        self.stop_btn.config(state="normal")
        CONTROLLER.thread = threading.Thread(target=bot_worker, daemon=True)
        CONTROLLER.thread.start()

    def on_pause(self):
        if CONTROLLER.pause_event.is_set():
            CONTROLLER.pause_event.clear()
            self.pause_btn.config(text="Pause")
            CONTROLLER.log("Resumed.")
        else:
            CONTROLLER.pause_event.set()
            self.pause_btn.config(text="Resume")
            CONTROLLER.log("Paused - click Resume to continue.")

    def on_stop(self):
        CONTROLLER.stop_event.set()
        CONTROLLER.pause_event.clear()  # unblock a paused wait so it can see the stop
        CONTROLLER.log("Stop requested - finishing current action...")
        self.stop_btn.config(state="disabled")
        self.pause_btn.config(state="disabled")

    def poll_queue(self):
        try:
            while True:
                msg = CONTROLLER.status_queue.get_nowait()
                if msg.startswith("__ATTACK_DONE__"):
                    n = msg.replace("__ATTACK_DONE__", "")
                    self.attack_var.set(f"Attacks completed: {n}")
                elif msg == "__DONE__":
                    self.start_btn.config(state="normal")
                    self.pause_btn.config(state="disabled")
                    self.stop_btn.config(state="disabled")
                    self.status_var.set("Idle - click Start")
                else:
                    self.status_var.set(msg)
        except queue.Empty:
            pass
        self.root.after(200, self.poll_queue)


def main():
    print(f"Screen resolution detected: {screen_w}x{screen_h}")
    print(f"Reference resolution:       {REFERENCE_WIDTH}x{REFERENCE_HEIGHT}")
    print(f"Scale factor:               x={SCALE_X:.3f}  y={SCALE_Y:.3f}")
    print(f"Loaded {len(REGIONS)} regions from {TAGS_FILE.name}")

    root = tk.Tk()
    gui = BotGUI(root)

    def on_close():
        CONTROLLER.stop_event.set()
        CONTROLLER.pause_event.clear()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
