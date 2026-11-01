"""
Calibration helper.

Moves the mouse to every tagged region, one at a time, so you can
visually confirm each click/drag point actually lands on the right
button in your game window BEFORE trusting the real bot with a match.

Run this with your emulator/game open and visible, then watch where
the cursor lands for each tag.

    python calibrate.py
"""

import time
import pyautogui

from coc_bot import REGIONS, scale_point, random_point_in_rect, TAGS_FILE, \
    screen_w, screen_h, REFERENCE_WIDTH, REFERENCE_HEIGHT, SCALE_X, SCALE_Y

pyautogui.FAILSAFE = True


def main():
    print(f"Screen resolution detected: {screen_w}x{screen_h}")
    print(f"Reference resolution:       {REFERENCE_WIDTH}x{REFERENCE_HEIGHT}")
    print(f"Scale factor:               x={SCALE_X:.3f}  y={SCALE_Y:.3f}")
    print(f"Loaded {len(REGIONS)} regions from {TAGS_FILE.name}\n")
    print("Switch to your emulator window now. Starting in 5 seconds...")
    time.sleep(5)

    for tag, r in REGIONS.items():
        if r["shape"] == "rectangle":
            # Show 3 random sample points from within the clickable area,
            # since the real bot picks a random point every time.
            print(f"[{tag}]  rectangle - sampling 3 random points in its area")
            for _ in range(3):
                rx, ry = random_point_in_rect(r)
                x, y = scale_point(rx, ry)
                pyautogui.moveTo(x, y, duration=0.25)
                time.sleep(0.4)
        else:
            sx, sy = scale_point(r["x1"], r["y1"])
            ex, ey = scale_point(r["x2"], r["y2"])
            print(f"[{tag}]  line start -> ({sx},{sy}), end -> ({ex},{ey})")
            pyautogui.moveTo(sx, sy, duration=0.3)
            time.sleep(0.6)
            pyautogui.moveTo(ex, ey, duration=0.3)

        time.sleep(1.2)  # pause so you can look at where the cursor is

    print("\nCalibration pass complete. If any point looked wrong, adjust")
    print("REFERENCE_WIDTH/REFERENCE_HEIGHT or OFFSET_X/OFFSET_Y in coc_bot.py")


if __name__ == "__main__":
    main()
