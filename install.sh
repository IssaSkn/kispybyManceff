#!/bin/bash
# Kispy — installer. Everything lands under your home directory; nothing needs
# sudo, and uninstall.sh removes exactly what this put there.
set -euo pipefail

LIB="$HOME/.local/lib/kispy"
BIN="$HOME/.local/bin"
SHARE="$HOME/.local/share/kispy"
MODELS="$SHARE/models"
WHISPER="$SHARE/whisper.cpp"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="$HOME/.config/kispy"

MODEL="${KISPY_MODEL:-}"
SKIP_MODEL=0
for arg in "$@"; do
  case "$arg" in
    --model=*) MODEL="${arg#*=}" ;;
    --no-model) SKIP_MODEL=1 ;;
    -h|--help) sed -n '2,4p' "$0"; echo; echo "  --model=<name>   large-v3 (default), large-v3-turbo, medium, small"; echo "  --no-model       skip the speech model download"; exit 0 ;;
  esac
done

bold()  { printf "\033[1m%s\033[0m\n" "$*"; }
step()  { printf "\n\033[1;36m▸\033[0m \033[1m%s\033[0m\n" "$*"; }
ok()    { printf "  \033[32m✓\033[0m %s\n" "$*"; }
warn()  { printf "  \033[33m!\033[0m %s\n" "$*"; }
die()   { printf "  \033[31m✗\033[0m %s\n" "$*" >&2; exit 1; }

printf "\n"
bold "Kispy"
printf "  records your lectures and hands you back the written course.\n"

# --------------------------------------------------------------- preflight ---
step "Checking this Mac"
[ "$(uname -s)" = "Darwin" ] || die "Kispy is macOS only (it uses AVFoundation, EventKit and Vision)."
ok "macOS $(sw_vers -productVersion) on $(uname -m)"

if ! xcode-select -p >/dev/null 2>&1; then
  warn "the Xcode command line tools are needed to build two small helpers"
  xcode-select --install || true
  die "re-run ./install.sh once the tools have finished installing"
fi
ok "Xcode command line tools"

BREW="$(command -v brew || true)"
[ -n "$BREW" ] || for p in /opt/homebrew/bin/brew /usr/local/bin/brew; do [ -x "$p" ] && BREW="$p"; done
[ -n "$BREW" ] || die "Homebrew is required — install it from https://brew.sh then run this again"
ok "Homebrew"

# ------------------------------------------------------------ dependencies ---
step "Command line dependencies"
need=()
command -v ffmpeg   >/dev/null || need+=(ffmpeg)     # records the audio
command -v tectonic >/dev/null || need+=(tectonic)   # compiles the PDF
command -v pdftoppm >/dev/null || need+=(poppler)    # lets a model read a scan
command -v cmake    >/dev/null || need+=(cmake)      # builds whisper.cpp
if [ ${#need[@]} -gt 0 ]; then
  printf "  installing: %s\n" "${need[*]}"
  "$BREW" install "${need[@]}"
fi
for b in ffmpeg tectonic pdftoppm cmake; do command -v "$b" >/dev/null && ok "$b"; done

# ------------------------------------------------------------------ python ---
step "Python"
PYTHON=""
for cand in "$("$BREW" --prefix)/bin/python3.14" "$("$BREW" --prefix)/bin/python3.13" \
            "$("$BREW" --prefix)/bin/python3.12" "$("$BREW" --prefix)/bin/python3.11" \
            "$("$BREW" --prefix)/bin/python3" python3; do
  command -v "$cand" >/dev/null 2>&1 || continue
  if "$cand" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'; then
    PYTHON="$cand"; break
  fi
done
if [ -z "$PYTHON" ]; then
  "$BREW" install python@3.12
  PYTHON="$("$BREW" --prefix)/bin/python3.12"
fi
ok "$("$PYTHON" --version) — $PYTHON"

# ------------------------------------------------------------------ kispy ----
step "Installing Kispy"
mkdir -p "$LIB" "$BIN" "$MODELS" "$CONFIG"
rm -rf "$LIB/kispy" "$LIB/style"
cp -R "$SRC/kispy" "$LIB/kispy"
cp -R "$SRC/style" "$LIB/style"
find "$LIB" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
ok "$LIB"

[ -d "$LIB/.venv" ] || "$PYTHON" -m venv "$LIB/.venv"
"$LIB/.venv/bin/pip" install --quiet --upgrade pip
"$LIB/.venv/bin/pip" install --quiet -r "$SRC/requirements.txt"
ok "python packages"

# --------------------------------------------------------- native helpers ----
step "Two small native helpers"
if swiftc -O -o "$BIN/kispy-cal" "$SRC/swift/kispy-cal.swift" 2>"$LIB/swift-cal.log"; then
  ok "kispy-cal   reads the Calendar app (EventKit)"
else
  rm -f "$BIN/kispy-cal"
  warn "kispy-cal could not be built — continuing without native Calendar access"
  warn "your Apple Swift compiler/SDK may be out of sync; details: $LIB/swift-cal.log"
fi
if swiftc -O -o "$BIN/kispy-ocr" "$SRC/swift/kispy-ocr.swift" 2>"$LIB/swift-ocr.log"; then
  ok "kispy-ocr   offline OCR, as a fallback (Vision)"
else
  rm -f "$BIN/kispy-ocr"
  warn "kispy-ocr could not be built — continuing without the offline OCR fallback"
  warn "model-based document reading still works through Codex"
fi

# ---------------------------------------------------------------- whisper ----
step "Speech recognition (whisper.cpp)"
if [ ! -x "$WHISPER/build/bin/whisper-cli" ]; then
  [ -d "$WHISPER/.git" ] || git clone --depth 1 https://github.com/ggml-org/whisper.cpp "$WHISPER"
  ( cd "$WHISPER"
    cmake -B build -DGGML_METAL=ON -DCMAKE_BUILD_TYPE=Release >/dev/null
    cmake --build build -j --config Release >/dev/null )
fi
[ -x "$WHISPER/build/bin/whisper-cli" ] || die "whisper.cpp did not build"
ok "whisper-cli, running on the GPU through Metal"

if [ "$SKIP_MODEL" = "0" ]; then
  if [ -z "$MODEL" ]; then
    if [ -t 0 ]; then
      printf "\n  Which model should listen to your lectures?\n\n"
      printf "    \033[1;36m1\033[0m  large-v3        best, 3.1 GB   — about 8x faster than real time\n"
      printf "    \033[1;36m2\033[0m  large-v3-turbo  1.6 GB         — nearly as good, twice as fast\n"
      printf "    \033[1;36m3\033[0m  medium          1.5 GB         — noticeably weaker on accents\n"
      printf "    \033[1;36m4\033[0m  small           0.5 GB         — only if disk space is tight\n\n"
      printf "  Your choice [1]: "; read -r choice || choice=1
      case "${choice:-1}" in 2) MODEL=large-v3-turbo ;; 3) MODEL=medium ;; 4) MODEL=small ;; *) MODEL=large-v3 ;; esac
    else
      MODEL=large-v3
    fi
  fi
  if [ ! -f "$MODELS/ggml-$MODEL.bin" ]; then
    printf "  downloading ggml-%s.bin — this is the long part\n" "$MODEL"
    bash "$WHISPER/models/download-ggml-model.sh" "$MODEL" "$MODELS" >/dev/null
  fi
  ok "ggml-$MODEL.bin"
  if [ ! -f "$MODELS/ggml-silero-v6.2.0.bin" ]; then
    bash "$WHISPER/models/download-vad-model.sh" silero-v6.2.0 "$MODELS" >/dev/null
  fi
  ok "voice activity detection (silero)"
  if [ ! -f "$CONFIG/config.toml" ] && [ "$MODEL" != "large-v3" ]; then
    printf '[transcribe]\nmodel = "%s"\n' "$MODELS/ggml-$MODEL.bin" > "$CONFIG/config.toml"
  fi
fi

# ------------------------------------------------------------- launchers -----
step "Commands"
cat > "$BIN/kispy" <<EOF
#!/bin/sh
# Kispy — quiet by design. Only status, doctor and setup ever print much.
exec "$LIB/.venv/bin/python" -c '
import sys; sys.path.insert(0, "$LIB")
from kispy import cli; sys.exit(cli.main(sys.argv))
' "\$@"
EOF
cat > "$BIN/kispy-watch" <<EOF
#!/bin/sh
# Kispy's watchdog. It loops itself: launchd throttles jobs that finish in under
# ten seconds, and its StartInterval never fired for one this short.
export PATH="$BIN:$(dirname "$BREW"):/usr/bin:/bin:/usr/sbin:/sbin"
while :; do
  "$BIN/kispy" tick 2>/dev/null
  sleep 60
done
EOF
chmod +x "$BIN/kispy" "$BIN/kispy-watch"
ok "kispy, kispy-watch"

case ":$PATH:" in
  *":$BIN:"*) ok "$BIN is on your PATH" ;;
  *)
    profile="$HOME/.zshrc"; [ -n "${BASH_VERSION:-}" ] && [ ! -f "$profile" ] && profile="$HOME/.bash_profile"
    printf '\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$profile"
    warn "added $BIN to your PATH in $(basename "$profile") — open a new terminal, or run:"
    printf "      export PATH=\"\$HOME/.local/bin:\$PATH\"\n"
    ;;
esac

printf "\n"
bold "Installed."
printf "\n  Now run:  \033[1;36mkispy setup\033[0m\n"
printf "  It asks five questions and takes about five minutes.\n\n"
