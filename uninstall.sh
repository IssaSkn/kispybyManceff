#!/bin/bash
# Removes Kispy. Your course documents are never touched: they live in the folder
# you chose, and this script does not know where that is and does not ask.
set -euo pipefail
LIB="$HOME/.local/lib/kispy"; BIN="$HOME/.local/bin"
SHARE="$HOME/.local/share/kispy"; CONFIG="$HOME/.config/kispy"
STATE="$HOME/.local/state/kispy"; PLIST="$HOME/Library/LaunchAgents/com.kispy.watch.plist"

ask() { printf "  %s [y/N] " "$1"; read -r a || a=n; [ "$a" = "y" ] || [ "$a" = "Y" ]; }

printf "\nRemoving Kispy.\n\n"
launchctl bootout "gui/$(id -u)/com.kispy.watch" 2>/dev/null || true
rm -f "$PLIST"; printf "  ✓ watchdog stopped\n"
pkill -f "kispy-watch" 2>/dev/null || true
rm -f "$BIN/kispy" "$BIN/kispy-watch" "$BIN/kispy-cal" "$BIN/kispy-ocr"
printf "  ✓ commands removed\n"
rm -rf "$LIB" "$STATE"; printf "  ✓ program and working files removed\n"

if [ -d "$SHARE" ]; then
  size=$(du -sh "$SHARE" 2>/dev/null | cut -f1)
  ask "Remove the speech model and whisper.cpp too ($size)?" && rm -rf "$SHARE" \
    && printf "  ✓ models removed\n" || printf "  · kept: %s\n" "$SHARE"
fi
if [ -d "$CONFIG" ]; then
  ask "Remove your settings (timetable, folder table, glossaries)?" \
    && rm -rf "$CONFIG" && printf "  ✓ settings removed\n" \
    || printf "  · kept: %s\n" "$CONFIG"
fi
printf "\nDone. Your course folders were not touched.\n\n"
