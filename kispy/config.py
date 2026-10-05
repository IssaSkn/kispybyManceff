"""Configuration: defaults, overridable from ~/.config/kispy/config.toml.

Nothing here is specific to one school, one timetable or one Mac. Everything a
new user has to decide is asked once by `kispy setup`, which writes the answers
to the TOML file; the defaults below are what applies until it does.
"""
from __future__ import annotations
import os, pathlib, tomllib

HOME = pathlib.Path.home()
CONFIG_DIR = HOME / ".config/kispy"
CONFIG_PATH = CONFIG_DIR / "config.toml"
LIB = pathlib.Path(__file__).resolve().parent.parent
SHARE = HOME / ".local/share/kispy"          # models and other large installed data
MODELS = SHARE / "models"
BIN = HOME / ".local/bin"

DEFAULTS: dict = {
    "calendar": {
        "source": "auto",      # auto | macos | ics | local
        "ics_url": "",
        # Which macOS calendars hold your courses. Empty means every calendar,
        # which on a personal Mac also means dentist appointments and birthdays.
        "calendars": [],
        "timezone": "",        # empty = whatever this Mac is set to
        "term_start_month": 9, # the month a new academic year begins
        "cache_minutes": 60,
        # A slot counts as a course unless its title matches one of these.
        "exclude": [
            r"study\s*time", r"holiday", r"vacan", r"self\s*study",
            r"^\s*\(exam", r"^\s*exam\b", r"^\s*deadline\s*:", r"^\s*presentation\s*:",
            r"^\s*\(project", r"welcome", r"induction", r"orientation",
        ],
        "max_hours": 8,            # anything longer is an all-day marker, not a lecture
        "lookahead_minutes": 20,   # you may start slightly before the bell
        "grace_minutes": 20,       # ...and the lecturer always overruns
    },
    "audio": {
        "device": "",          # avfoundation input NAME; empty = ask at setup
        "sample_rate": 16000,
    },
    "transcribe": {
        "model": str(MODELS / "ggml-large-v3.bin"),
        "vad_model": str(MODELS / "ggml-silero-v6.2.0.bin"),
        "language": "en",      # the language spoken in your lectures
        "threads": 0,          # 0 = one per performance core
    },
    "synthesis": {
        "backend": "claude_cli",     # claude_cli (subscription) | api (ANTHROPIC_API_KEY)
        "model": "claude-opus-5",
        "timeout_seconds": 2400,
    },
    "documents": {
        "model": "claude-haiku-4-5",   # reads scans and handwritten notes
        "timeout_seconds": 900,
    },
    "audit": {
        "enabled": True,       # structural checks: free, instant, never wrong
        "coverage": True,      # a second model reads the document against the material
        "model": "claude-haiku-4-5",
        "timeout_seconds": 900,
    },
    "output": {
        "root": str(HOME / "Documents/Courses"),
        "program": "",         # printed under the title, e.g. "MSc Finance — LSE"
        "year": "",            # e.g. "2025-2026"; empty = worked out from the date
    },
    "runtime": {
        "state_dir": str(HOME / ".local/state/kispy"),
    },
}


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


def load() -> dict:
    cfg = DEFAULTS
    if CONFIG_PATH.exists():
        try:
            cfg = _merge(cfg, tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except tomllib.TOMLDecodeError as exc:
            raise SystemExit(f"{CONFIG_PATH} is not valid TOML: {exc}")
    for section in ("output", "runtime", "transcribe"):
        for key, val in cfg[section].items():
            if isinstance(val, str) and val.startswith("~"):
                cfg[section][key] = str(pathlib.Path(val).expanduser())
    if not cfg["transcribe"]["threads"]:
        cfg["transcribe"]["threads"] = max(4, (os.cpu_count() or 8) - 2)
    return cfg


def exists() -> bool:
    return CONFIG_PATH.exists()


def state_dir() -> pathlib.Path:
    d = pathlib.Path(load()["runtime"]["state_dir"])
    d.mkdir(parents=True, exist_ok=True)
    return d


def work_dir() -> pathlib.Path:
    """Where audio and transcripts live while the job runs. Never the output folder."""
    d = state_dir() / "work"
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    return d


def write(cfg: dict) -> pathlib.Path:
    """Write the TOML by hand: no dependency, and the comments are the point --
    this file is meant to be opened and edited."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    c, a, t, s, o = (cfg["calendar"], cfg["audio"], cfg["transcribe"],
                     cfg["synthesis"], cfg["output"])

    def tilde(v: str) -> str:
        return str(v).replace(str(HOME), "~", 1) if str(v).startswith(str(HOME)) else str(v)

    def q(v) -> str:
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, list):
            return "[" + ", ".join(f'"{x}"' for x in v) + "]"
        if isinstance(v, (int, float)):
            return str(v)
        return '"' + str(v).replace('"', '\\"') + '"'

    text = f"""# Kispy — written by `kispy setup`, yours to edit.
# Anything left out falls back to the built-in default.

[calendar]
source    = {q(c["source"])}   # auto | macos | ics | local
ics_url   = {q(c["ics_url"])}
calendars = {q(c["calendars"])}   # macOS calendars to read; [] means all of them

[audio]
# By NAME, never by index: indices shift as soon as an iPhone or a pair of
# AirPods shows up, and Kispy refuses to start rather than record the wrong one.
device = {q(a["device"])}

[transcribe]
language = {q(t["language"])}   # the language spoken in your lectures
model    = {q(tilde(t["model"]))}

[synthesis]
backend = {q(s["backend"])}   # claude_cli = your Claude subscription
model   = {q(s["model"])}

[output]
root    = {q(tilde(o["root"]))}
program = {q(o["program"])}
year    = {q(o["year"])}

[audit]
enabled  = {q(cfg["audit"]["enabled"])}    # structural checks: free, instant, never wrong
coverage = {q(cfg["audit"]["coverage"])}   # a second model checks nothing was dropped
"""
    CONFIG_PATH.write_text(text, encoding="utf-8")
    return CONFIG_PATH
