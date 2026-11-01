"""
Clash of Clans Attack-Loop Bot (with GUI controls)
===================================================
Reads click/drag regions from All_Tags.json and replays the attack
sequence: Attack -> Find Match -> Attack -> zoom out -> pan camera ->
Deploy spells/troops/heroes -> Wait -> End Battle -> Return Home -> repeat.

KEY BINDINGS (emulator hotkeys)
--------------------------------
  Troops : 1  2  3  4   (up to 4 troop slots, configured in GUI)
  Spells : A  S  D  F   (up to 4 spell slots, configured in GUI)
  Heroes : Q  W  E  R   (Hero 01-04, always all 4)

SPEED MODES
-----------
  Only deployment timing (tap, line-tap, move, drag, hero-ability delay)
  changes between Safe / Fast / Extreme Fast.
  Navigation / selection delays are FIXED at 0.20-0.40 s regardless of
  speed mode, so clicking UI buttons always looks human.

BEFORE YOU RUN
--------------
1. All_Tags.json must be in the same folder as this script.
2. Set REFERENCE_WIDTH / REFERENCE_HEIGHT to match the resolution the
   coordinates in the JSON were captured at (default 2560x1440).
3. Keep your emulator window in a fixed position every run.
4. Run `python calibrate.py` first to sanity-check the click points.
5. Failsafe: slam the mouse into the top-left corner to abort instantly.
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
    import pydirectinput
except ImportError:
    sys.exit("pydirectinput is not installed. Run: pip install pydirectinput")

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
pyautogui.PAUSE = 0.05
pydirectinput.PAUSE = 0.0   # we handle our own delays via _nav_delay()

# ==========================================================================
# CONFIG  –  edit these to match your setup
# ==========================================================================

TAGS_FILE = (
    Path(sys.executable).parent / "All_Tags.json"
    if getattr(sys, "frozen", False)
    else Path(__file__).parent / "All_Tags.json"
)

REFERENCE_WIDTH  = 2560
REFERENCE_HEIGHT = 1440

OFFSET_X = 0
OFFSET_Y = 0

# --------------------------------------------------------------------------
# Keyboard shortcuts (emulator hotkeys)
# --------------------------------------------------------------------------
# Troop slots   –  keys 1, 2, 3, 4
TROOP_KEYS = ["1", "2", "3", "4"]

# Spell slots   –  keys A, S, D, F
SPELL_KEYS = ["a", "s", "d", "f"]

# Hero slots    –  keys Q, W, E, R  (Hero 01 → Q, 02 → W, 03 → E, 04 → R)
HERO_KEYS  = ["q", "w", "e", "r"]

# --------------------------------------------------------------------------
# Deploy counts
# --------------------------------------------------------------------------
# Spell taps per deploy point (same count for every selected spell)
SPELL_DEPLOY1_COUNT = 4
SPELL_DEPLOY2_COUNT = 4

# Troop deploys per area (same for every selected troop type)
TROOP_AREA01_COUNT = 6
TROOP_AREA02_COUNT = 6
TROOP_AREA03_COUNT = 32

# --------------------------------------------------------------------------
# Runtime values – set from the GUI before the bot starts
# --------------------------------------------------------------------------
NUM_TROOPS = 1   # how many troop types to spam  (1–4)
NUM_SPELLS = 1   # how many spells to spam        (1–4)

# --------------------------------------------------------------------------
# Timing
# --------------------------------------------------------------------------
WAIT_AFTER_BATTLE_START  = 7    # seconds after "03- Attack Button"
WAIT_AFTER_DEPLOY        = 40   # seconds to let the battle play out
WAIT_BEFORE_RETURN_HOME  = 5    # seconds for results screen to animate
START_DELAY_SECONDS      = 5    # countdown after clicking Start

# --------------------------------------------------------------------------
# Camera
# --------------------------------------------------------------------------
ZOOM_OUT_POINT = (REFERENCE_WIDTH * 0.5, REFERENCE_HEIGHT * 0.5)
ZOOM_OUT_TICKS = 6   # negative = zoom out; flip sign if emulator is reversed

# --------------------------------------------------------------------------
# Click accuracy
# --------------------------------------------------------------------------
RECT_PADDING_RATIO  = 0.15   # inset factor for rectangle regions
LINE_PERP_JITTER_PX = 12     # perpendicular jitter for line deploys

# --------------------------------------------------------------------------
# Fixed navigation / selection delays  (NEVER changed by speed mode)
# --------------------------------------------------------------------------
NAV_MIN = 0.20
NAV_MAX = 0.40

# ==========================================================================
# SPEED PROFILES  –  deployment timing ONLY
# Navigation & selection clicks always use NAV_MIN / NAV_MAX above.
# ==========================================================================
SPEED_PROFILES = {
    "Safe Mode (Current Speed)": {
        # deployment tap delays (spells, heroes, rect-region taps)
        "tap_min": 0.04, "tap_max": 0.08,
        # line-region troop deploy tap delays
        "line_tap_min": 0.04, "line_tap_max": 0.08,
        # mouse-move duration for any tap
        "move_min": 0.04, "move_max": 0.10,
        # camera pan drag duration
        "pan_drag_duration": 0.12,
        # pause between deploying a hero and tapping its ability
        "hero_ability_delay": 0.40,
    },
    "Fast Mode": {
        "tap_min": 0.05, "tap_max": 0.09,
        "line_tap_min": 0.025, "line_tap_max": 0.06,
        "move_min": 0.02, "move_max": 0.05,
        "pan_drag_duration": 0.06,
        "hero_ability_delay": 0.20,
    },
    "Extreme Fast Mode": {
        "tap_min": 0.015, "tap_max": 0.035,
        "line_tap_min": 0.01,  "line_tap_max": 0.025,
        "move_min": 0.01,  "move_max": 0.02,
        "pan_drag_duration": 0.03,
        "hero_ability_delay": 0.10,
    },
}

DEFAULT_SPEED_MODE = "Safe Mode (Current Speed)"


class Speed:
    """Mutable container for the active speed profile's values."""
    pass


SPEED = Speed()
for _k, _v in SPEED_PROFILES[DEFAULT_SPEED_MODE].items():
    setattr(SPEED, _k, _v)

# 0 = loop forever, N = stop after N attacks
MAX_LOOPS = 0

# ==========================================================================
# LOAD REGIONS
# ==========================================================================

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
    """Random point inside the rectangle, inset by RECT_PADDING_RATIO."""
    w = r["x2"] - r["x1"]
    h = r["y2"] - r["y1"]
    pad_x = w * RECT_PADDING_RATIO
    pad_y = h * RECT_PADDING_RATIO
    x = random.uniform(r["x1"] + pad_x, r["x2"] - pad_x)
    y = random.uniform(r["y1"] + pad_y, r["y2"] - pad_y)
    return x, y


# ==========================================================================
# PAUSE / STOP CONTROL
# ==========================================================================

class StopBot(Exception):
    """Raised to cleanly unwind the attack loop on Stop."""


class BotController:
    def __init__(self):
        self.stop_event  = threading.Event()
        self.pause_event = threading.Event()   # set = paused
        self.status_queue = queue.Queue()
        self.thread = None

    def log(self, msg):
        print(msg)
        self.status_queue.put(msg)

    def check_control(self):
        """Block while paused; raise StopBot if stop was requested."""
        while self.pause_event.is_set():
            if self.stop_event.is_set():
                raise StopBot()
            time.sleep(0.2)
        if self.stop_event.is_set():
            raise StopBot()

    def sleep_checked(self, seconds):
        """Sleep responsively – honours pause/stop every 0.5 s."""
        end = time.time() + seconds
        while time.time() < end:
            self.check_control()
            time.sleep(min(0.5, max(0.0, end - time.time())))
        self.check_control()


CONTROLLER = BotController()


def apply_speed_profile(name: str):
    profile = SPEED_PROFILES.get(name, SPEED_PROFILES[DEFAULT_SPEED_MODE])
    for key, value in profile.items():
        setattr(SPEED, key, value)
    CONTROLLER.log(f"Speed mode set: {name}")


def _nav_delay():
    """Fixed navigation delay – independent of speed mode."""
    time.sleep(random.uniform(NAV_MIN, NAV_MAX))


# ==========================================================================
# LOW-LEVEL ACTIONS
# ==========================================================================

# --------------------------------------------------------------------------
# Navigation / Selection  (always fixed NAV_MIN–NAV_MAX delay)
# --------------------------------------------------------------------------

def click_tag(tag: str):
    """Click a rectangle UI button. Uses fixed navigation delay."""
    CONTROLLER.check_control()
    r = REGIONS.get(tag)
    if r is None:
        raise KeyError(f"Tag '{tag}' not found in {TAGS_FILE.name}")
    if r["shape"] != "rectangle":
        raise ValueError(f"Tag '{tag}' is not a rectangle – use deploy_on_line()")

    rx, ry = random_point_in_rect(r)
    x, y   = scale_point(rx, ry)

    CONTROLLER.log(f"[nav-click] {tag} -> ({x}, {y})")
    pyautogui.moveTo(x, y, duration=random.uniform(SPEED.move_min, SPEED.move_max))
    pyautogui.click()
    _nav_delay()   # ← fixed, NOT speed-mode-dependent


def focus_emulator():
    """Click the centre of the screen to give the emulator window focus
    before sending any keyboard shortcuts.  Uses a fast move so it is
    invisible to the player but reliable."""
    cx, cy = scale_point(REFERENCE_WIDTH * 0.5, REFERENCE_HEIGHT * 0.5)
    pyautogui.moveTo(cx, cy, duration=0.05)
    pyautogui.click()
    time.sleep(0.15)   # tiny pause so the OS registers the focus change


def select_by_key(key: str, label: str = ""):
    """Press a keyboard shortcut to select a troop/spell/hero slot.
    Uses pydirectinput (DirectInput/SendInput API) so the key reaches
    the emulator even when it normally ignores WM_KEYDOWN messages.
    Uses fixed navigation delay – NOT affected by speed mode."""
    CONTROLLER.check_control()
    display = label if label else key.upper()
    CONTROLLER.log(f"[nav-key ] selecting '{display}' (key: {key.upper()})")
    pydirectinput.press(key)   # DirectInput – works with BlueStacks/LDPlayer/MEmu
    _nav_delay()   # ← fixed, NOT speed-mode-dependent


# --------------------------------------------------------------------------
# Deployment  (all delays come from the active SPEED profile)
# --------------------------------------------------------------------------

def click_tag_n(tag: str, count: int):
    """Tap a rectangle region N times using deployment speed (tap_min/max)."""
    r = REGIONS.get(tag)
    if r is None:
        raise KeyError(f"Tag '{tag}' not found in {TAGS_FILE.name}")
    if r["shape"] != "rectangle":
        raise ValueError(f"Tag '{tag}' is not a rectangle")

    CONTROLLER.log(f"[deploy  ] {tag} x{count}")
    for _ in range(count):
        CONTROLLER.check_control()
        rx, ry = random_point_in_rect(r)
        x, y   = scale_point(rx, ry)
        pyautogui.moveTo(x, y, duration=random.uniform(SPEED.move_min * 0.6,
                                                        SPEED.move_max * 0.6))
        pyautogui.click()
        time.sleep(random.uniform(SPEED.tap_min, SPEED.tap_max))


def deploy_on_line(tag: str, count: int):
    """Deploy `count` units spread randomly along a line region with
    perpendicular jitter – uses deployment speed (line_tap_min/max)."""
    CONTROLLER.check_control()
    r = REGIONS.get(tag)
    if r is None:
        raise KeyError(f"Tag '{tag}' not found in {TAGS_FILE.name}")
    if r["shape"] != "line":
        raise ValueError(f"Tag '{tag}' is not a line – use click_tag()")

    x1, y1, x2, y2 = r["x1"], r["y1"], r["x2"], r["y2"]
    dx, dy = x2 - x1, y2 - y1
    length = math.hypot(dx, dy) or 1.0
    perp_x, perp_y = -dy / length, dx / length

    CONTROLLER.log(f"[deploy  ] {tag} x{count}")
    for _ in range(count):
        CONTROLLER.check_control()
        t  = random.uniform(0.08, 0.92)
        px = x1 + dx * t + perp_x * random.uniform(-LINE_PERP_JITTER_PX, LINE_PERP_JITTER_PX)
        py = y1 + dy * t + perp_y * random.uniform(-LINE_PERP_JITTER_PX, LINE_PERP_JITTER_PX)
        sx, sy = scale_point(px, py)
        pyautogui.moveTo(sx, sy, duration=random.uniform(SPEED.move_min * 0.6,
                                                          SPEED.move_max * 0.6))
        pyautogui.click()
        time.sleep(random.uniform(SPEED.line_tap_min, SPEED.line_tap_max))


def zoom_out():
    """Scroll-zoom out on the battlefield."""
    CONTROLLER.check_control()
    x, y = scale_point(*ZOOM_OUT_POINT)
    CONTROLLER.log(f"[camera  ] zoom out {ZOOM_OUT_TICKS} ticks at ({x},{y})")
    pyautogui.moveTo(x, y, duration=0.15)
    pyautogui.scroll(-abs(ZOOM_OUT_TICKS), x=x, y=y)
    _nav_delay()


def drag_line(tag: str):
    """Single continuous mouse drag from the start to the end of a line region.
    Used for camera pans. Pan duration comes from the active speed profile."""
    CONTROLLER.check_control()
    r = REGIONS.get(tag)
    if r is None:
        raise KeyError(f"Tag '{tag}' not found in {TAGS_FILE.name}")
    if r["shape"] != "line":
        raise ValueError(f"Tag '{tag}' is not a line")

    sx, sy = scale_point(r["x1"], r["y1"])
    ex, ey = scale_point(r["x2"], r["y2"])
    CONTROLLER.log(f"[camera  ] pan drag {tag}: ({sx},{sy}) -> ({ex},{ey})")

    pyautogui.moveTo(sx, sy, duration=0.08)
    pyautogui.mouseDown()
    pyautogui.moveTo(ex, ey, duration=SPEED.pan_drag_duration)
    pyautogui.mouseUp()
    time.sleep(random.uniform(SPEED.tap_min, SPEED.tap_max))


# ==========================================================================
# ATTACK SEQUENCE
# ==========================================================================

def run_attack_sequence(attack_num: int):
    CONTROLLER.log(f"=== Attack #{attack_num} ===")

    # ------------------------------------------------------------------
    # 1. Navigate to battle
    # ------------------------------------------------------------------
    click_tag("Attack Button")
    click_tag("Find a Match")
    click_tag("03- Attack Button")

    CONTROLLER.log(f"Waiting {WAIT_AFTER_BATTLE_START}s for battle to load...")
    CONTROLLER.sleep_checked(WAIT_AFTER_BATTLE_START)

    # ------------------------------------------------------------------
    # 2. Camera setup
    # ------------------------------------------------------------------
    zoom_out()
    drag_line("click and drag")

    # ------------------------------------------------------------------
    # 3. Focus emulator so keyboard shortcuts reach it, not the GUI
    # ------------------------------------------------------------------
    CONTROLLER.log("[focus   ] clicking emulator to grab keyboard focus...")
    focus_emulator()

    # ------------------------------------------------------------------
    # 4. Spells  (keys A → S → D → F, up to NUM_SPELLS)
    #    Each spell is selected, then tapped on both deploy points.
    # ------------------------------------------------------------------
    spell_names = ["Spell 1", "Spell 2", "Spell 3", "Spell 4"]
    for i in range(NUM_SPELLS):
        select_by_key(SPELL_KEYS[i], label=spell_names[i])
        click_tag_n("Spell Deploy 01", SPELL_DEPLOY1_COUNT)
        click_tag_n("spell deploy 02", SPELL_DEPLOY2_COUNT)

    # ------------------------------------------------------------------
    # 4. Troops  (keys 1 → 2 → 3 → 4, up to NUM_TROOPS)
    #    Each troop type is selected, then deployed on the three areas.
    # ------------------------------------------------------------------
    troop_names = ["Troop 1", "Troop 2", "Troop 3", "Troop 4"]
    for i in range(NUM_TROOPS):
        select_by_key(TROOP_KEYS[i], label=troop_names[i])
        deploy_on_line("Deploy area 01", TROOP_AREA01_COUNT)
        deploy_on_line("Deploy area 02", TROOP_AREA02_COUNT)
        deploy_on_line("Deploy area 03", TROOP_AREA03_COUNT)

    # ------------------------------------------------------------------
    # 5. Heroes  (keys Q → W → E → R, always all 4)
    #    Select → deploy one unit → wait → press again for ability
    # ------------------------------------------------------------------
    hero_labels = [
        "Hero 1 (Q)", "Hero 2 (W)", "Hero 3 (E)", "Hero 4 (R)"
    ]
    for i, key in enumerate(HERO_KEYS):
        select_by_key(key, label=hero_labels[i])            # select hero
        deploy_on_line("Deploy area 03", 1)                  # place on field
        CONTROLLER.sleep_checked(SPEED.hero_ability_delay)   # let it spawn
        select_by_key(key, label=f"{hero_labels[i]} ability")# trigger ability

    # ------------------------------------------------------------------
    # 6. Wait for battle
    # ------------------------------------------------------------------
    CONTROLLER.log(f"Waiting {WAIT_AFTER_DEPLOY}s for battle to play out...")
    CONTROLLER.sleep_checked(WAIT_AFTER_DEPLOY)

    # ------------------------------------------------------------------
    # 7. End battle and return home
    # ------------------------------------------------------------------
    click_tag("End Battle")
    click_tag("Okay Button")

    CONTROLLER.log(f"Waiting {WAIT_BEFORE_RETURN_HOME}s for results screen...")
    CONTROLLER.sleep_checked(WAIT_BEFORE_RETURN_HOME)

    click_tag("Return Home Button")
    CONTROLLER.log(f"=== Attack #{attack_num} complete ===")


# ==========================================================================
# BOT WORKER THREAD
# ==========================================================================

def bot_worker():
    try:
        CONTROLLER.log(
            f"Starting in {START_DELAY_SECONDS}s – switch to your emulator now..."
        )
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
            CONTROLLER.log("Waiting 5s before next attack...")
            CONTROLLER.sleep_checked(5)

    except StopBot:
        CONTROLLER.log("Bot stopped.")
    except pyautogui.FailSafeException:
        CONTROLLER.log("Failsafe triggered – bot stopped.")
    except Exception as e:
        CONTROLLER.log(f"Error: {e}")
    finally:
        CONTROLLER.log("__DONE__")


# ==========================================================================
# GUI
# ==========================================================================

class BotGUI:
    def __init__(self, root):
        self.root = root
        root.title("CoC Attack Bot")
        root.geometry("470x460")
        root.resizable(False, False)

        # ------------------------------------------------------------------
        # Title
        # ------------------------------------------------------------------
        ttk.Label(
            root, text="Clash of Clans Attack Bot",
            font=("Segoe UI", 13, "bold")
        ).pack(pady=(12, 2))

        # Status / counter
        self.status_var = tk.StringVar(value="Idle – click Start")
        self.attack_var = tk.StringVar(value="Attacks completed: 0")
        ttk.Label(root, textvariable=self.status_var,
                  font=("Segoe UI", 9), wraplength=440).pack(pady=1)
        ttk.Label(root, textvariable=self.attack_var,
                  font=("Segoe UI", 9)).pack(pady=1)

        # ------------------------------------------------------------------
        # Deployment config frame
        # ------------------------------------------------------------------
        cfg = ttk.LabelFrame(root, text="Deployment Config", padding=(10, 6))
        cfg.pack(fill="x", padx=14, pady=(8, 4))

        # Troops row
        tr = ttk.Frame(cfg)
        tr.pack(fill="x", pady=3)
        ttk.Label(tr, text="Troops to spam (1–4):",
                  font=("Segoe UI", 9), width=22, anchor="w").pack(side="left")
        self.troop_var = tk.IntVar(value=1)
        self.troop_spin = ttk.Spinbox(
            tr, from_=1, to=4, textvariable=self.troop_var,
            width=4, state="readonly"
        )
        self.troop_spin.pack(side="left", padx=(4, 8))
        self.troop_keys_lbl = ttk.Label(
            tr, text="Keys: [1]", font=("Segoe UI", 9), foreground="#444"
        )
        self.troop_keys_lbl.pack(side="left")
        self.troop_var.trace_add("write", self._refresh_key_labels)

        # Spells row
        sp = ttk.Frame(cfg)
        sp.pack(fill="x", pady=3)
        ttk.Label(sp, text="Spells to spam (1–4):",
                  font=("Segoe UI", 9), width=22, anchor="w").pack(side="left")
        self.spell_var = tk.IntVar(value=1)
        self.spell_spin = ttk.Spinbox(
            sp, from_=1, to=4, textvariable=self.spell_var,
            width=4, state="readonly"
        )
        self.spell_spin.pack(side="left", padx=(4, 8))
        self.spell_keys_lbl = ttk.Label(
            sp, text="Keys: [A]", font=("Segoe UI", 9), foreground="#444"
        )
        self.spell_keys_lbl.pack(side="left")
        self.spell_var.trace_add("write", self._refresh_key_labels)

        # Heroes row (always 4, display only)
        hr = ttk.Frame(cfg)
        hr.pack(fill="x", pady=3)
        ttk.Label(hr, text="Heroes (always 4):",
                  font=("Segoe UI", 9), width=22, anchor="w").pack(side="left")
        ttk.Label(
            hr, text="Keys: [Q] [W] [E] [R]",
            font=("Segoe UI", 9), foreground="#444"
        ).pack(side="left", padx=(4, 0))

        # Note about nav delays
        ttk.Label(
            cfg,
            text="ℹ  Nav/selection delays are fixed (0.20–0.40 s) in all speed modes.",
            font=("Segoe UI", 8), foreground="#666", wraplength=420
        ).pack(anchor="w", pady=(6, 0))

        # ------------------------------------------------------------------
        # Speed selector
        # ------------------------------------------------------------------
        sp_row = ttk.Frame(root)
        sp_row.pack(pady=(4, 2))
        ttk.Label(sp_row, text="Deploy Speed:",
                  font=("Segoe UI", 10)).pack(side="left", padx=(0, 8))
        self.speed_var = tk.StringVar(value=DEFAULT_SPEED_MODE)
        self.speed_combo = ttk.Combobox(
            sp_row,
            textvariable=self.speed_var,
            values=list(SPEED_PROFILES.keys()),
            state="readonly",
            width=28,
        )
        self.speed_combo.pack(side="left")

        # ------------------------------------------------------------------
        # Control buttons
        # ------------------------------------------------------------------
        btn_frame = ttk.Frame(root)
        btn_frame.pack(pady=12)

        self.start_btn = ttk.Button(
            btn_frame, text="Start", width=10, command=self.on_start)
        self.start_btn.grid(row=0, column=0, padx=5)

        self.pause_btn = ttk.Button(
            btn_frame, text="Pause", width=10,
            command=self.on_pause, state="disabled")
        self.pause_btn.grid(row=0, column=1, padx=5)

        self.stop_btn = ttk.Button(
            btn_frame, text="Stop", width=10,
            command=self.on_stop, state="disabled")
        self.stop_btn.grid(row=0, column=2, padx=5)

        # Failsafe reminder
        ttk.Label(
            root,
            text="Failsafe: drag mouse to the top-left corner at any time to abort instantly.",
            font=("Segoe UI", 8), foreground="gray", justify="center",
        ).pack(pady=(4, 10))

        # Init label text and start polling
        self._refresh_key_labels()
        self.poll_queue()

    # ----------------------------------------------------------------------
    # Helpers
    # ----------------------------------------------------------------------

    def _refresh_key_labels(self, *_):
        """Update the key-hint labels next to each spinbox."""
        n_t = self.troop_var.get()
        self.troop_keys_lbl.config(
            text="Keys: " + "  ".join(f"[{TROOP_KEYS[i].upper()}]" for i in range(n_t))
        )
        n_s = self.spell_var.get()
        self.spell_keys_lbl.config(
            text="Keys: " + "  ".join(f"[{SPELL_KEYS[i].upper()}]" for i in range(n_s))
        )

    def _set_controls_running(self, running: bool):
        state_lock   = "disabled" if running else "readonly"
        state_start  = "disabled" if running else "normal"
        state_action = "normal"   if running else "disabled"
        self.troop_spin.config(state=state_lock)
        self.spell_spin.config(state=state_lock)
        self.speed_combo.config(state=state_lock)
        self.start_btn.config(state=state_start)
        self.pause_btn.config(state=state_action, text="Pause")
        self.stop_btn.config(state=state_action)

    # ----------------------------------------------------------------------
    # Button handlers
    # ----------------------------------------------------------------------

    def on_start(self):
        global NUM_TROOPS, NUM_SPELLS
        NUM_TROOPS = self.troop_var.get()
        NUM_SPELLS = self.spell_var.get()
        apply_speed_profile(self.speed_var.get())
        CONTROLLER.stop_event.clear()
        CONTROLLER.pause_event.clear()
        self.attack_var.set("Attacks completed: 0")
        self._set_controls_running(True)
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
            CONTROLLER.log("Paused – click Resume to continue.")

    def on_stop(self):
        CONTROLLER.stop_event.set()
        CONTROLLER.pause_event.clear()   # unblock a paused wait
        CONTROLLER.log("Stop requested – finishing current action...")
        self.stop_btn.config(state="disabled")
        self.pause_btn.config(state="disabled")

    # ----------------------------------------------------------------------
    # Queue polling (runs on the Tk thread every 200 ms)
    # ----------------------------------------------------------------------

    def poll_queue(self):
        try:
            while True:
                msg = CONTROLLER.status_queue.get_nowait()
                if msg.startswith("__ATTACK_DONE__"):
                    n = msg.replace("__ATTACK_DONE__", "")
                    self.attack_var.set(f"Attacks completed: {n}")
                elif msg == "__DONE__":
                    self._set_controls_running(False)
                    self.status_var.set("Idle – click Start")
                else:
                    self.status_var.set(msg)
        except queue.Empty:
            pass
        self.root.after(200, self.poll_queue)


# ==========================================================================
# ENTRY POINT
# ==========================================================================

def main():
    print(f"Screen resolution detected: {screen_w}x{screen_h}")
    print(f"Reference resolution:       {REFERENCE_WIDTH}x{REFERENCE_HEIGHT}")
    print(f"Scale factor:               x={SCALE_X:.3f}  y={SCALE_Y:.3f}")
    print(f"Loaded {len(REGIONS)} regions from {TAGS_FILE.name}")
    print(f"Fixed nav delay:            {NAV_MIN}–{NAV_MAX} s  (all speed modes)")

    root = tk.Tk()
    BotGUI(root)

    def on_close():
        CONTROLLER.stop_event.set()
        CONTROLLER.pause_event.clear()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
