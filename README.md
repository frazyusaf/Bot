# 🏰 Clash of Clans Attack Bot

An automated attack-loop bot for Clash of Clans running inside an Android emulator (BlueStacks / LDPlayer / MEmu).

---

## ✨ Features

- **Keyboard-based selection** — troops via `1`–`4`, spells via `A`/`S`/`D`/`F`, heroes via `Q`/`W`/`E`/`R`
- **Configurable deploy counts** — choose 1–4 troop types and 1–4 spell types from the GUI
- **3 speed modes** — Safe / Fast / Extreme Fast (only affects deployment timing, never navigation)
- **Auto-loop** — Attack → End Battle → Return Home → repeat
- **Pause / Resume / Stop** controls
- **Failsafe** — slam mouse to top-left corner to abort instantly

---

## 📋 Requirements

- Windows 10/11
- Python 3.7+
- Android emulator with keyboard shortcut support (BlueStacks / LDPlayer / MEmu)

---

## 🚀 Quick Start

1. **Install Python** from [python.org](https://www.python.org/downloads/) — check *"Add python.exe to PATH"*
2. **Map your emulator hotkeys** so that:
   - Keys `1` `2` `3` `4` select troop slots
   - Keys `A` `S` `D` `F` select spell slots
   - Keys `Q` `W` `E` `R` select hero slots
3. **Tag your screen regions** using the Pointer Tool (see `HOW_TO_USE.txt`)
4. **Double-click `Start_Bot.bat`** — it installs dependencies and launches the GUI

---

## 🗂️ File Structure

```
Coc Bot Simple v1/
├── coc_bot.py          ← Main bot (run this)
├── calibrate.py        ← Test your tagged coordinates
├── Start_Bot.bat       ← One-click launcher
├── requirements.txt    ← Python dependencies
├── HOW_TO_USE.txt      ← Full setup guide (read this first)
├── All_Tags.json       ← YOUR coordinates (not included — create with Pointer Tool)
└── Pointer/
    └── screen_region_tagger.py  ← Tool to tag screen regions
```

> **`All_Tags.json` is not included** — you must create it yourself using the Pointer Tool.  
> See `HOW_TO_USE.txt` for the exact pointer names and order.

---

## 🎮 Controls

| Button | Action |
|--------|--------|
| **Start** | Begin attack loop |
| **Pause** | Freeze bot mid-sequence |
| **Resume** | Continue from where it paused |
| **Stop** | Finish current action then stop |

---

## ⚠️ Disclaimer

This bot is for educational purposes. Use at your own risk — automating gameplay may violate the game's Terms of Service.
