#!/usr/bin/env python3
"""
Screen Region Tagger
=====================
A lightweight desktop utility for selecting rectangular regions OR line
segments anywhere on your screen, tagging each one with a label, and
exporting the full set of tagged coordinates to a file (simple text, JSON,
or CSV).

Usage:
    python3 screen_region_tagger.py

Requirements:
    Python 3.7+ with Tkinter (included in the standard Windows/macOS
    installers; on Debian/Ubuntu you may need `sudo apt install python3-tk`).

Flow:
    1. Click "+ New Rectangle" or "\u2571 New Line" (main window hides).
    2. Your screen dims slightly.
       - Rectangle mode: drag out a box around the area you want.
       - Line mode: drag from the line's start point to its end point.
       - You can switch modes mid-overlay by pressing R (rectangle) or
         L (line) before you start dragging.
    3. Release the mouse -- a small dialog asks for a tag name.
    4. Choose "Add & Select Next" to keep going, or "Add & Finish" to save.
    5. At any point, "Finish & Save File" in the main window exports
       everything tagged so far to .txt / .json / .csv.

Notes:
    - Coordinates are absolute screen pixel coordinates (0,0 = top-left of
      your primary display).
    - Rectangles are always normalized so x_min < x_max and y_min < y_max.
    - Lines keep the direction you drew them in (start point -> end point),
      since that can matter (e.g. for arrows or vectors). Length and angle
      are recorded alongside the two endpoints.
    - On macOS with a Retina display, Tk reports "points" rather than
      physical pixels -- multiply by your display's scale factor (usually 2)
      if you need true pixel coordinates for another tool.
    - Multi-monitor setups: the selection overlay covers the display the
      app window is on. Move the main window to another monitor first if
      you need to tag a region there.
"""

import csv
import io
import json
import math
import tkinter as tk
from dataclasses import dataclass, asdict
from datetime import datetime
from tkinter import messagebox, filedialog


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #

@dataclass
class Region:
    """
    A single tagged shape in absolute screen coordinates.

    shape == "rectangle": (x1,y1) is top-left, (x2,y2) is bottom-right,
        always normalized so x1 < x2 and y1 < y2.
    shape == "line": (x1,y1) is where the drag started, (x2,y2) is where
        it ended -- direction is preserved, not normalized.
    """
    index: int
    tag: str
    shape: str  # "rectangle" or "line"
    x1: int
    y1: int
    x2: int
    y2: int

    @property
    def width(self) -> int:
        return abs(self.x2 - self.x1)

    @property
    def height(self) -> int:
        return abs(self.y2 - self.y1)

    @property
    def length(self) -> float:
        return round(math.hypot(self.x2 - self.x1, self.y2 - self.y1), 1)

    @property
    def angle_deg(self) -> float:
        return round(math.degrees(math.atan2(self.y2 - self.y1, self.x2 - self.x1)), 1)

    @property
    def coords_str(self) -> str:
        if self.shape == "rectangle":
            return f"{self.x1}-{self.x2}*{self.y1}-{self.y2}"
        return f"({self.x1},{self.y1})->({self.x2},{self.y2})"

    @property
    def type_label(self) -> str:
        return "RECT" if self.shape == "rectangle" else "LINE"


# --------------------------------------------------------------------------- #
# Full-screen drag-to-select overlay
# --------------------------------------------------------------------------- #

class SelectionOverlay:
    """
    A translucent full-screen window that lets the user drag out either a
    rectangle or a line segment. Calls
    `on_complete(shape, x1, y1, x2, y2)` when a valid shape is drawn, or
    `on_cancel()` if the user presses Escape or right-clicks.

    The active shape mode can be switched at any time before a drag starts
    by pressing R (rectangle) or L (line).
    """

    MIN_SIZE = 4  # px -- smaller than this is treated as an accidental click

    def __init__(self, parent: tk.Tk, on_complete, on_cancel, mode: str = "rectangle"):
        self.on_complete = on_complete
        self.on_cancel = on_cancel
        self.mode = mode  # "rectangle" or "line"
        self.start = None
        self.shape_id = None

        self.win = tk.Toplevel(parent)
        self.win.attributes("-fullscreen", True)
        self.win.attributes("-alpha", 0.28)
        self.win.attributes("-topmost", True)
        self.win.configure(bg="black")
        self.win.focus_force()

        self.hint = tk.Label(
            self.win, fg="#4FD1C5", bg="black", font=("Segoe UI", 12, "bold"),
        )
        self.hint.place(relx=0.5, y=24, anchor="n")

        self.canvas = tk.Canvas(
            self.win, bg="black", cursor="crosshair", highlightthickness=0
        )
        self.canvas.pack(fill="both", expand=True)

        self.coord_label = tk.Label(
            self.win, fg="#4FD1C5", bg="#0B0F14",
            font=("Consolas", 10), bd=1, relief="solid",
        )

        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Motion>", self._on_motion)
        self.win.bind("<Escape>", lambda e: self._cancel())
        self.win.bind("<Button-3>", lambda e: self._cancel())
        self.win.bind("r", lambda e: self._set_mode("rectangle"))
        self.win.bind("R", lambda e: self._set_mode("rectangle"))
        self.win.bind("l", lambda e: self._set_mode("line"))
        self.win.bind("L", lambda e: self._set_mode("line"))

        self._update_hint()

    def _set_mode(self, mode):
        if self.start is not None:
            return  # don't allow switching mid-drag
        self.mode = mode
        self._update_hint()

    def _update_hint(self):
        shape_word = "rectangle" if self.mode == "rectangle" else "line"
        self.hint.config(
            text=f"Mode: {shape_word.upper()}   \u2022   drag to draw   "
                 f"\u2022   R = rectangle   L = line   \u2022   Esc to cancel"
        )

    def _on_motion(self, event):
        self.coord_label.config(text=f" x {event.x_root}, y {event.y_root} ")
        self.coord_label.place(x=event.x + 16, y=event.y + 16)

    def _on_press(self, event):
        self.start = (event.x_root, event.y_root)
        if self.mode == "rectangle":
            self.shape_id = self.canvas.create_rectangle(
                event.x, event.y, event.x, event.y,
                outline="#F2A93B", width=2, dash=(6, 4),
            )
        else:
            self.shape_id = self.canvas.create_line(
                event.x, event.y, event.x, event.y,
                fill="#F2A93B", width=3,
            )
            self._start_dot = self.canvas.create_oval(
                event.x - 4, event.y - 4, event.x + 4, event.y + 4,
                fill="#F2A93B", outline="",
            )

    def _on_drag(self, event):
        if self.start is None or self.shape_id is None:
            return
        x0_root, y0_root = self.start
        x0 = event.x - (event.x_root - x0_root)
        y0 = event.y - (event.y_root - y0_root)
        self.canvas.coords(self.shape_id, x0, y0, event.x, event.y)

    def _on_release(self, event):
        if self.start is None:
            return
        x0, y0 = self.start
        x1, y1 = event.x_root, event.y_root
        self.start = None

        if self.mode == "rectangle":
            x_min, x_max = sorted((x0, x1))
            y_min, y_max = sorted((y0, y1))
            valid = (x_max - x_min) >= self.MIN_SIZE and (y_max - y_min) >= self.MIN_SIZE
            result = (x_min, y_min, x_max, y_max)
        else:
            valid = math.hypot(x1 - x0, y1 - y0) >= self.MIN_SIZE
            result = (x0, y0, x1, y1)

        if not valid:
            # Accidental click / too small -- reset and let them try again
            if self.shape_id:
                self.canvas.delete(self.shape_id)
                self.shape_id = None
            if getattr(self, "_start_dot", None):
                self.canvas.delete(self._start_dot)
                self._start_dot = None
            return

        self._destroy()
        self.on_complete(self.mode, *result)

    def _cancel(self):
        self._destroy()
        self.on_cancel()

    def _destroy(self):
        try:
            self.win.destroy()
        except tk.TclError:
            pass


# --------------------------------------------------------------------------- #
# Tag prompt dialog
# --------------------------------------------------------------------------- #

class TagDialog:
    """
    Small always-on-top dialog shown right after a shape is drawn. Asks
    for a tag name, then lets the user choose to keep tagging or finish up.
    """

    def __init__(self, parent, shape, coords, on_add_next, on_add_finish, on_discard):
        self.on_add_next = on_add_next
        self.on_add_finish = on_add_finish
        self.on_discard = on_discard
        x1, y1, x2, y2 = coords

        self.win = tk.Toplevel(parent)
        self.win.title("Tag this region")
        self.win.attributes("-topmost", True)
        self.win.resizable(False, False)
        self.win.configure(bg="#121820", padx=18, pady=16)
        self.win.protocol("WM_DELETE_WINDOW", self._discard)

        badge_text = "\u25a2 RECTANGLE" if shape == "rectangle" else "\u2571 LINE SEGMENT"
        tk.Label(
            self.win, text=badge_text, fg="#4FD1C5", bg="#121820",
            font=("Segoe UI", 9, "bold"),
        ).pack(anchor="w")

        if shape == "rectangle":
            x_min, x_max = sorted((x1, x2))
            y_min, y_max = sorted((y1, y2))
            coord_text = (
                f"x: {x_min}\u2013{x_max}   y: {y_min}\u2013{y_max}   "
                f"({x_max-x_min}\u00d7{y_max-y_min}px)"
            )
        else:
            length = round(math.hypot(x2 - x1, y2 - y1), 1)
            angle = round(math.degrees(math.atan2(y2 - y1, x2 - x1)), 1)
            coord_text = (
                f"({x1}, {y1})  \u2192  ({x2}, {y2})\n"
                f"length: {length}px   angle: {angle}\u00b0"
            )

        tk.Label(
            self.win, text=coord_text, fg="#9FB0BE", bg="#0B0F14",
            font=("Consolas", 10), padx=8, pady=6, anchor="w", justify="left",
        ).pack(fill="x", pady=(8, 12))

        tk.Label(
            self.win, text="Label / tag name", fg="#5C6B7A", bg="#121820",
            font=("Segoe UI", 9),
        ).pack(anchor="w")

        self.entry = tk.Entry(
            self.win, font=("Segoe UI", 12), bg="#0B0F14", fg="#E7EEF4",
            insertbackground="#E7EEF4", relief="flat",
        )
        self.entry.pack(fill="x", ipady=6, pady=(4, 14))
        self.entry.focus_set()
        self.entry.bind("<Return>", lambda e: self._add_next())
        self.entry.bind("<Escape>", lambda e: self._discard())

        btn_row = tk.Frame(self.win, bg="#121820")
        btn_row.pack(fill="x")

        self._make_button(btn_row, "Discard", self._discard, "#1B2530", "#9FB0BE").pack(
            side="left", padx=(0, 6)
        )
        self._make_button(btn_row, "Add & Next", self._add_next, "#4FD1C5", "#052825").pack(
            side="left", padx=6
        )
        self._make_button(btn_row, "Add & Finish", self._add_finish, "#F2A93B", "#2B1B03").pack(
            side="left", padx=(6, 0)
        )

        tk.Label(
            self.win, text="Enter = Add & Next    Esc = Discard",
            fg="#5C6B7A", bg="#121820", font=("Segoe UI", 8),
        ).pack(pady=(10, 0))

        self.win.update_idletasks()
        w, h = self.win.winfo_width(), self.win.winfo_height()
        sw, sh = self.win.winfo_screenwidth(), self.win.winfo_screenheight()
        self.win.geometry(f"+{(sw - w) // 2}+{(sh - h) // 2}")

    @staticmethod
    def _make_button(parent, text, command, bg, fg):
        return tk.Button(
            parent, text=text, command=command, bg=bg, fg=fg,
            font=("Segoe UI", 9, "bold"), relief="flat", padx=10, pady=6,
            activebackground=bg, cursor="hand2",
        )

    def _tag_or_warn(self):
        tag = self.entry.get().strip()
        if not tag:
            self.entry.config(bg="#3A1414")
            self.win.after(500, lambda: self.entry.config(bg="#0B0F14"))
            return None
        return tag

    def _add_next(self):
        tag = self._tag_or_warn()
        if tag is None:
            return
        self.win.destroy()
        self.on_add_next(tag)

    def _add_finish(self):
        tag = self._tag_or_warn()
        if tag is None:
            return
        self.win.destroy()
        self.on_add_finish(tag)

    def _discard(self):
        self.win.destroy()
        self.on_discard()


# --------------------------------------------------------------------------- #
# Main application window
# --------------------------------------------------------------------------- #

class ScreenRegionTaggerApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Screen Region Tagger")
        self.root.geometry("440x540")
        self.root.configure(bg="#0B0F14")
        self.root.minsize(380, 440)

        self.regions: list[Region] = []
        self._next_index = 1

        self._build_ui()

    # ---------------- UI construction ---------------- #

    def _build_ui(self):
        header = tk.Frame(self.root, bg="#121820", pady=14, padx=16)
        header.pack(fill="x")
        tk.Label(
            header, text="Screen Region Tagger", fg="#E7EEF4", bg="#121820",
            font=("Segoe UI", 14, "bold"),
        ).pack(anchor="w")
        tk.Label(
            header, text="Select a rectangle or line on screen \u2192 tag it \u2192 export",
            fg="#5C6B7A", bg="#121820", font=("Segoe UI", 9),
        ).pack(anchor="w", pady=(2, 0))

        body = tk.Frame(self.root, bg="#0B0F14", padx=16, pady=14)
        body.pack(fill="both", expand=True)

        select_row = tk.Frame(body, bg="#0B0F14")
        select_row.pack(fill="x")
        tk.Button(
            select_row, text="\u25a2  New Rectangle",
            command=lambda: self.start_selection("rectangle"),
            bg="#4FD1C5", fg="#052825", font=("Segoe UI", 11, "bold"),
            relief="flat", pady=10, cursor="hand2",
        ).pack(side="left", fill="x", expand=True, padx=(0, 6))
        tk.Button(
            select_row, text="\u2571  New Line",
            command=lambda: self.start_selection("line"),
            bg="#8B7FF0", fg="#1A1333", font=("Segoe UI", 11, "bold"),
            relief="flat", pady=10, cursor="hand2",
        ).pack(side="left", fill="x", expand=True, padx=(6, 0))

        tk.Label(
            body, text="Tip: once the overlay is open, press R or L to switch shape",
            fg="#3E4956", bg="#0B0F14", font=("Segoe UI", 8),
        ).pack(anchor="w", pady=(6, 0))

        list_frame = tk.Frame(body, bg="#0B0F14")
        list_frame.pack(fill="both", expand=True, pady=(14, 10))

        tk.Label(
            list_frame, text="TAGGED REGIONS", fg="#5C6B7A", bg="#0B0F14",
            font=("Segoe UI", 8, "bold"),
        ).pack(anchor="w")

        list_container = tk.Frame(list_frame, bg="#121820")
        list_container.pack(fill="both", expand=True, pady=(6, 0))

        scrollbar = tk.Scrollbar(list_container)
        scrollbar.pack(side="right", fill="y")

        self.listbox = tk.Listbox(
            list_container, bg="#121820", fg="#E7EEF4",
            font=("Consolas", 10), relief="flat", selectbackground="#1B2530",
            highlightthickness=0, yscrollcommand=scrollbar.set,
        )
        self.listbox.pack(side="left", fill="both", expand=True, padx=8, pady=6)
        scrollbar.config(command=self.listbox.yview)
        self.listbox.bind("<Delete>", lambda e: self.delete_selected())
        self.listbox.bind("<BackSpace>", lambda e: self.delete_selected())

        hint = tk.Label(
            list_frame, text="Select a row and press Delete to remove it",
            fg="#3E4956", bg="#0B0F14", font=("Segoe UI", 8),
        )
        hint.pack(anchor="w", pady=(4, 0))

        self.count_label = tk.Label(
            body, text="0 regions tagged", fg="#9FB0BE", bg="#0B0F14",
            font=("Segoe UI", 9),
        )
        self.count_label.pack(anchor="w", pady=(4, 10))

        btn_row = tk.Frame(body, bg="#0B0F14")
        btn_row.pack(fill="x")
        tk.Button(
            btn_row, text="Clear All", command=self.clear_all,
            bg="#121820", fg="#F0665A", font=("Segoe UI", 9, "bold"),
            relief="flat", pady=8, cursor="hand2",
        ).pack(side="left", fill="x", expand=True, padx=(0, 6))
        tk.Button(
            btn_row, text="Finish & Save File", command=self.finish_and_save,
            bg="#F2A93B", fg="#2B1B03", font=("Segoe UI", 9, "bold"),
            relief="flat", pady=8, cursor="hand2",
        ).pack(side="left", fill="x", expand=True, padx=(6, 0))

    # ---------------- Selection flow ---------------- #

    def start_selection(self, mode: str = "rectangle"):
        self._pending_mode = mode
        self.root.withdraw()
        # Small delay so the main window is fully hidden before the
        # screen-dim overlay appears (avoids a visual flash on some OSes).
        self.root.after(120, self._launch_overlay)

    def _launch_overlay(self):
        SelectionOverlay(
            self.root,
            on_complete=self._on_shape_drawn,
            on_cancel=self._on_selection_cancelled,
            mode=getattr(self, "_pending_mode", "rectangle"),
        )

    def _on_selection_cancelled(self):
        self.root.deiconify()

    def _on_shape_drawn(self, shape, x1, y1, x2, y2):
        self._pending_mode = shape  # remember last-used mode for "Add & Next"
        TagDialog(
            self.root,
            shape=shape,
            coords=(x1, y1, x2, y2),
            on_add_next=lambda tag: self._commit_region(
                tag, shape, x1, y1, x2, y2, then="next"
            ),
            on_add_finish=lambda tag: self._commit_region(
                tag, shape, x1, y1, x2, y2, then="finish"
            ),
            on_discard=self._on_selection_cancelled,
        )

    def _commit_region(self, tag, shape, x1, y1, x2, y2, then):
        if shape == "rectangle":
            x1, x2 = sorted((x1, x2))
            y1, y2 = sorted((y1, y2))
        region = Region(
            index=self._next_index, tag=tag, shape=shape,
            x1=x1, y1=y1, x2=x2, y2=y2,
        )
        self._next_index += 1
        self.regions.append(region)
        self._refresh_list()

        if then == "next":
            self.root.after(80, self._launch_overlay)
        else:
            self.root.deiconify()
            self.finish_and_save()

    # ---------------- List management ---------------- #

    def _refresh_list(self):
        self.listbox.delete(0, tk.END)
        for r in self.regions:
            self.listbox.insert(
                tk.END, f"{r.index:>2}. [{r.type_label}] {r.tag:<16} {r.coords_str}"
            )
        n = len(self.regions)
        self.count_label.config(text=f"{n} region{'s' if n != 1 else ''} tagged")

    def delete_selected(self):
        sel = self.listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        del self.regions[idx]
        self._refresh_list()

    def clear_all(self):
        if not self.regions:
            return
        if messagebox.askyesno("Clear all?", "Remove every tagged region?"):
            self.regions.clear()
            self._refresh_list()

    # ---------------- Export ---------------- #

    def finish_and_save(self):
        if not self.regions:
            messagebox.showinfo(
                "Nothing to save", "Tag at least one region before exporting."
            )
            return

        fmt = self._ask_format()
        if fmt is None:
            return

        ext = {"simple": "txt", "json": "json", "csv": "csv"}[fmt]
        default_name = f"tagged-regions_{datetime.now().strftime('%Y%m%d-%H%M%S')}.{ext}"
        path = filedialog.asksaveasfilename(
            defaultextension=f".{ext}",
            initialfile=default_name,
            filetypes=[(ext.upper(), f"*.{ext}"), ("All files", "*.*")],
        )
        if not path:
            return

        content = self._build_export(fmt)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)

        messagebox.showinfo("Saved", f"Saved {len(self.regions)} tagged region(s) to:\n{path}")

    def _ask_format(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("Export format")
        dialog.configure(bg="#121820", padx=20, pady=18)
        dialog.attributes("-topmost", True)
        dialog.resizable(False, False)
        dialog.grab_set()

        tk.Label(
            dialog, text="Choose export format", fg="#E7EEF4", bg="#121820",
            font=("Segoe UI", 11, "bold"),
        ).pack(anchor="w", pady=(0, 12))

        choice = tk.StringVar(value="simple")
        options = [
            ("simple", "Simple text  (rect: x1-x2*y1-y2 / line: (x1,y1)->(x2,y2))"),
            ("json", "JSON  (structured, with width/height/length/angle)"),
            ("csv", "CSV  (spreadsheet-friendly)"),
        ]
        for val, label in options:
            tk.Radiobutton(
                dialog, text=label, variable=choice, value=val,
                fg="#E7EEF4", bg="#121820", selectcolor="#0B0F14",
                activebackground="#121820", activeforeground="#E7EEF4",
                font=("Segoe UI", 10), anchor="w", wraplength=360, justify="left",
            ).pack(fill="x", pady=3)

        result = {"value": None}

        def confirm():
            result["value"] = choice.get()
            dialog.destroy()

        def cancel():
            dialog.destroy()

        btn_row = tk.Frame(dialog, bg="#121820")
        btn_row.pack(fill="x", pady=(16, 0))
        tk.Button(
            btn_row, text="Cancel", command=cancel, bg="#1B2530", fg="#9FB0BE",
            relief="flat", padx=12, pady=6, cursor="hand2",
        ).pack(side="left", expand=True, fill="x", padx=(0, 6))
        tk.Button(
            btn_row, text="Continue", command=confirm, bg="#4FD1C5", fg="#052825",
            font=("Segoe UI", 9, "bold"), relief="flat", padx=12, pady=6, cursor="hand2",
        ).pack(side="left", expand=True, fill="x", padx=(6, 0))

        dialog.update_idletasks()
        w, h = dialog.winfo_width(), dialog.winfo_height()
        sw, sh = dialog.winfo_screenwidth(), dialog.winfo_screenheight()
        dialog.geometry(f"+{(sw - w) // 2}+{(sh - h) // 2}")

        self.root.wait_window(dialog)
        return result["value"]

    def _build_export(self, fmt: str) -> str:
        if fmt == "simple":
            lines = [f"{r.tag} [{r.type_label}]: {r.coords_str}" for r in self.regions]
            return "\n".join(lines) + "\n"

        if fmt == "json":
            payload = {
                "exported_at": datetime.now().isoformat(timespec="seconds"),
                "rectangle_format": "x1-x2*y1-y2 (normalized min-max)",
                "line_format": "(x1,y1)->(x2,y2) (start->end, direction preserved)",
                "regions": [],
            }
            for r in self.regions:
                entry = {**asdict(r), "coords": r.coords_str}
                if r.shape == "rectangle":
                    entry["width"] = r.width
                    entry["height"] = r.height
                else:
                    entry["length"] = r.length
                    entry["angle_deg"] = r.angle_deg
                payload["regions"].append(entry)
            return json.dumps(payload, indent=2)

        if fmt == "csv":
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow([
                "index", "tag", "shape", "x1", "y1", "x2", "y2",
                "width", "height", "length", "angle_deg", "coords",
            ])
            for r in self.regions:
                is_rect = r.shape == "rectangle"
                writer.writerow([
                    r.index, r.tag, r.shape, r.x1, r.y1, r.x2, r.y2,
                    r.width if is_rect else "",
                    r.height if is_rect else "",
                    "" if is_rect else r.length,
                    "" if is_rect else r.angle_deg,
                    r.coords_str,
                ])
            return buf.getvalue()

        raise ValueError(f"Unknown format: {fmt}")

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    ScreenRegionTaggerApp().run()
