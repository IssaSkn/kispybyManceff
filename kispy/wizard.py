"""`kispy setup` — the one interactive moment in Kispy's life.

Asks the five things it cannot work out on its own (Claude, microphone,
timetable, where documents go, whether to start by itself), checks everything
else, and writes ~/.config/kispy/config.toml. Safe to run again at any time:
every answer is pre-filled with what is already configured.
"""
from __future__ import annotations
import array, os, pathlib, shutil, subprocess, sys, tempfile, time, wave
from . import config, recorder, schedule, timetable, transcribe, ui

PLIST = pathlib.Path.home() / "Library/LaunchAgents/com.kispy.watch.plist"
LABEL = "com.kispy.watch"

BREW_PACKAGES = {
    "ffmpeg": "ffmpeg", "tectonic": "tectonic", "pdftoppm": "poppler",
}


def _have(binary: str) -> bool:
    return shutil.which(binary) is not None


def _brew() -> str | None:
    return shutil.which("brew") or next(
        (p for p in ("/opt/homebrew/bin/brew", "/usr/local/bin/brew") if pathlib.Path(p).exists()),
        None)


# --------------------------------------------------------------------------- 1
def step_tools() -> bool:
    ui.heading("1/6", "The tools Kispy runs on")
    missing = []
    for binary, formula in BREW_PACKAGES.items():
        if _have(binary):
            ui.good(f"{binary}")
        else:
            ui.bad(f"{binary} is missing")
            missing.append(formula)

    try:
        ui.good(f"whisper-cli   {_short(transcribe.whisper_cli())}")
    except Exception:
        ui.bad("whisper-cli is missing — re-run install.sh")
        missing.append("")

    model = pathlib.Path(config.load()["transcribe"]["model"])
    if model.exists():
        ui.good(f"speech model  {model.name}  ({model.stat().st_size / 1e9:.1f} GB)")
    else:
        ui.bad(f"speech model missing: {model}")
        ui.note("install.sh downloads it; it is about 3 GB")

    for helper, what in ((config.BIN / "kispy-cal", "calendar reader"),
                         (config.BIN / "kispy-ocr", "offline OCR")):
        (ui.good if helper.exists() else ui.warn)(f"{what}  {helper.name}")

    if missing and any(missing):
        brew = _brew()
        pkgs = [m for m in missing if m]
        ui.console.print()
        if brew and ui.confirm(f"Install {', '.join(pkgs)} with Homebrew now?"):
            subprocess.run([brew, "install", *pkgs], check=False)
        elif not brew:
            ui.note("Homebrew is not installed — see https://brew.sh")
    return True


def _short(p) -> str:
    s = str(p)
    return s.replace(str(pathlib.Path.home()), "~")


# --------------------------------------------------------------------------- 2
def claude_state() -> str:
    """not-installed | not-logged-in | ok"""
    if not _have("claude"):
        return "not-installed"
    try:
        r = subprocess.run(["claude", "-p", "--output-format", "text", "say ok"],
                           capture_output=True, text=True, timeout=120)
    except Exception:
        return "not-logged-in"
    blob = (r.stdout or "") + (r.stderr or "")
    if "Not logged in" in blob or "Please run /login" in blob or "/login" in blob:
        return "not-logged-in"
    return "ok" if r.returncode == 0 and (r.stdout or "").strip() else "not-logged-in"


def step_claude(cfg: dict) -> dict:
    ui.heading("2/6", "Connect Claude")
    ui.say("Kispy writes your documents with Claude, through the Claude Code CLI.")
    ui.note("your own subscription — Kispy never asks you for a key and never "
            "stores one")
    ui.console.print()

    for _ in range(6):
        with ui.console.status("  [grey62]checking…[/]", spinner="dots"):
            st = claude_state()
        if st == "ok":
            ui.good("Claude is connected")
            cfg["synthesis"]["backend"] = "claude_cli"
            return cfg
        if st == "not-installed":
            ui.bad("the `claude` command is not installed")
            if _have("npm") and ui.confirm("Install it now with npm?"):
                subprocess.run(["npm", "install", "-g", "@anthropic-ai/claude-code"], check=False)
                continue
            ui.note("install Node, then:  npm install -g @anthropic-ai/claude-code")
        else:
            ui.warn("Claude is installed but not signed in")
            ui.console.print()
            ui.say("In another terminal window:", style="bold")
            ui.note("1.  claude")
            ui.note("2.  type  /login  and follow the browser")
            ui.note("3.  come back here")
            ui.console.print()
        if not ui.confirm("Try again?"):
            break

    ui.console.print()
    ui.warn("carrying on without Claude — recording will work, documents will not")
    ui.note("you can also use the API instead: export ANTHROPIC_API_KEY in your shell "
            "and set backend = \"api\" in the config")
    return cfg


# --------------------------------------------------------------------------- 3
def _loudness(wav: pathlib.Path) -> float:
    """Peak level of the test clip, 0..1. Silence means the wrong input."""
    try:
        with wave.open(str(wav)) as w:
            frames = w.readframes(w.getnframes())
        a = array.array("h")
        a.frombytes(frames[: len(frames) // 2 * 2])
        return (max(abs(x) for x in a) / 32768) if a else 0.0
    except Exception:
        return 0.0


def step_microphone(cfg: dict) -> dict:
    ui.heading("3/6", "The microphone")
    try:
        devices = recorder.devices()
    except Exception as exc:
        ui.bad(f"cannot list the inputs: {exc}")
        return cfg
    if not devices:
        ui.bad("macOS reports no audio input at all")
        return cfg

    ui.note("chosen by name, never by number: indices shift as soon as an iPhone "
            "or a pair of AirPods appears")
    ui.console.print()
    for i, d in enumerate(devices, 1):
        ui.console.print(f"    [bold deep_sky_blue1]{i}[/]  {d}", highlight=False)
    ui.console.print()
    current = cfg["audio"]["device"]
    default = str(devices.index(current) + 1) if current in devices else "1"
    while True:
        a = ui.ask("Which one is your Mac's own microphone?", default)
        if a.isdigit() and 1 <= int(a) <= len(devices):
            cfg["audio"]["device"] = devices[int(a) - 1]
            break
    ui.good(cfg["audio"]["device"])

    ui.console.print()
    if not ui.confirm("Record four seconds to check it works?"):
        return cfg
    work = pathlib.Path(tempfile.mkdtemp())
    try:
        ui.say("[bold]Say something…[/]")
        pid = recorder.start(work, 1, cfg["audio"]["device"], cfg["audio"]["sample_rate"])
        time.sleep(4.5)
        recorder.stop(pid)
        wav = recorder.segment_path(work, 1)
        peak = _loudness(wav) if wav.exists() else 0.0
        if peak < 0.01:
            ui.bad("nothing was captured")
            ui.note("System Settings ▸ Privacy & Security ▸ Microphone, and allow "
                    "the terminal you are running kispy from")
        else:
            bars = "▁▂▃▄▅▆▇█"[: max(1, int(peak * 8))]
            ui.good(f"heard you  [green]{bars}[/]")
    except Exception as exc:
        ui.bad(str(exc)[:120])
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return cfg


# --------------------------------------------------------------------------- 4
def _preview(cfg: dict) -> None:
    """Show what Kispy now believes your week looks like. This is the only honest
    way to tell a working timetable from a plausible-looking one."""
    import datetime
    from . import cli
    try:
        slots = cli._slots(cfg)
    except Exception as exc:
        ui.bad(f"the timetable could not be read: {str(exc)[:120]}")
        return
    now = datetime.datetime.now(schedule.TZ)
    soon = [s for s in slots if now - datetime.timedelta(days=1) <= s.start
            <= now + datetime.timedelta(days=8)]
    ui.good(f"{len(slots)} session(s) read")
    if not soon:
        ui.note("nothing in the next week — check the dates if that is a surprise")
        return
    ui.console.print()
    ui.say("Next few days:", style="bold")
    for s in soon[:8]:
        ui.console.print(f"     [deep_sky_blue1]{s.start:%a %d/%m %H:%M}[/]  {s.subject[:40]}"
                         f"   [grey62]Lecture {s.number}[/]", highlight=False)


def step_timetable(cfg: dict) -> dict:
    ui.heading("4/6", "Your timetable")
    ui.say("Kispy starts on its own because it knows when your classes are.")
    ui.console.print()
    choice = ui.choose("Where should it read them from?", [
        ("The Calendar app on this Mac",
         "best: no login, and it follows a change within minutes"),
        ("A calendar URL (.ics)",
         "a link your school publishes, or Google Calendar's secret address"),
        ("Neither — read my timetable from a screenshot",
         "a photo, a PDF, or you type it; nothing to connect"),
    ], default=1)

    if choice == 1:
        cfg = _timetable_macos(cfg)
    elif choice == 2:
        cfg["calendar"]["source"] = "ics"
        url = ui.ask("Paste the .ics address", cfg["calendar"]["ics_url"])
        cfg["calendar"]["ics_url"] = url.strip()
        ui.note("Google Calendar ▸ settings for the calendar ▸ 'Secret address in "
                "iCal format'. Google caches it for hours, so a class added this "
                "morning may take a while to appear.")
    else:
        cfg["calendar"]["source"] = "local"
        timetable.import_flow(cfg)

    ui.console.print()
    _preview(cfg)
    return cfg


def _timetable_macos(cfg: dict) -> dict:
    cfg["calendar"]["source"] = "macos"
    if not schedule.EVENTKIT.exists():
        ui.bad("kispy-cal is not installed — re-run install.sh")
        return cfg
    ui.console.print()
    ui.say("macOS will ask for permission to read your calendars.")
    ui.note("the first time only; grant it in the dialog that appears")
    try:
        with ui.console.status("  [grey62]waiting for the permission dialog…[/]", spinner="dots"):
            cals = schedule.macos_calendars()
    except Exception as exc:
        ui.bad(str(exc)[:160])
        ui.note("System Settings ▸ Privacy & Security ▸ Calendars, allow your terminal, "
                "then run `kispy setup` again")
        return cfg
    if not cals:
        ui.bad("no calendar found in the Calendar app")
        ui.note("add your school calendar there first — in Calendar ▸ File ▸ New "
                "Calendar Subscription, or by signing into the account that holds it")
        return cfg

    ui.console.print()
    names = [c[0] for c in cals]
    labels = [f"{n}   ({acct})" for n, acct in cals]
    picked = ui.pick_many("Which calendars hold your classes?", labels,
                          [l for l, (n, _) in zip(labels, cals)
                           if n in cfg["calendar"]["calendars"]])
    cfg["calendar"]["calendars"] = [names[labels.index(l)] for l in picked]
    if not cfg["calendar"]["calendars"]:
        ui.warn("none selected — Kispy will read every calendar, dentist included")
    else:
        ui.good(", ".join(cfg["calendar"]["calendars"]))
    return cfg


# --------------------------------------------------------------------------- 5
def step_output(cfg: dict) -> dict:
    ui.heading("5/6", "Where your documents go")
    root = ui.ask("Folder for your courses", _short(cfg["output"]["root"]))
    path = pathlib.Path(root).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    cfg["output"]["root"] = str(path)
    ui.good(_short(path))
    ui.note("one folder per subject, one folder per session inside it")
    ui.console.print()

    cfg["output"]["program"] = ui.ask("Printed under the title (optional)",
                                      cfg["output"]["program"])
    lang = ui.ask("Language spoken in your lectures (en, fr, de, es…)",
                  cfg["transcribe"]["language"])
    cfg["transcribe"]["language"] = (lang or "en").strip().lower()[:5]
    return cfg


# --------------------------------------------------------------------------- 6
def watchdog_installed() -> bool:
    return PLIST.exists()


def watchdog_running() -> bool:
    r = subprocess.run(["launchctl", "list"], capture_output=True, text=True)
    return LABEL in r.stdout


def install_watchdog() -> bool:
    home = pathlib.Path.home()
    log = pathlib.Path(config.load()["runtime"]["state_dir"])
    log.mkdir(parents=True, exist_ok=True)
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    PLIST.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{LABEL}</string>
  <key>ProgramArguments</key><array><string>{home}/.local/bin/kispy-watch</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>ProcessType</key><string>Background</string>
  <key>StandardOutPath</key><string>{log}/watch.log</string>
  <key>StandardErrorPath</key><string>{log}/watch.log</string>
</dict></plist>
""", encoding="utf-8")
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}/{LABEL}"], capture_output=True)
    r = subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(PLIST)],
                       capture_output=True, text=True)
    return r.returncode == 0 or watchdog_running()


def remove_watchdog() -> bool:
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"], capture_output=True)
    PLIST.unlink(missing_ok=True)
    return True


def step_autostart() -> None:
    ui.heading("6/6", "Start by itself")
    ui.say("A small background job looks at your timetable once a minute. When a "
           "class begins it starts recording, and it stops when the class is over.")
    ui.note("nothing on screen, no sound, no menu bar icon — `kispy` shows you "
            "what it is doing, `kispy auto off` silences it for a while")
    ui.console.print()
    if ui.confirm("Let Kispy start on its own?"):
        ui.good("installed" if install_watchdog() else "launchd refused it — see `kispy doctor`")
    else:
        remove_watchdog()
        ui.note("you can turn it on later with `kispy auto install`")


# --------------------------------------------------------------------------- run
WELCOME = """[bold]Kispy records your lectures and hands you the written course.[/]

It starts on its own when a class begins, listens, and once the class is over it
transcribes the audio, writes it up as a proper document and compiles a PDF into
your course folder. The sound and the transcript are destroyed at that point;
what stays on your disk is the .tex and the .pdf, and nothing in them says how
they were made.

This takes about five minutes, once."""


def run(argv: list[str] | None = None) -> int:
    if not ui.is_tty():
        ui.bad("`kispy setup` needs a terminal")
        return 1
    ui.console.print()
    ui.panel(WELCOME, title="[bold]Kispy[/]")
    cfg = config.load()
    try:
        step_tools()
        cfg = step_claude(cfg)
        cfg = step_microphone(cfg)
        cfg = step_timetable(cfg)
        cfg = step_output(cfg)
        path = config.write(cfg)
        step_autostart()
    except SystemExit:
        ui.console.print()
        ui.note("stopped. Nothing was changed.")
        return 130

    ui.console.print()
    ui.panel(f"""[bold]You are set up.[/]

  [bold deep_sky_blue1]kispy[/]            open the app and see what is going on
  [bold deep_sky_blue1]kispy doctor[/]     check everything is still wired
  [bold deep_sky_blue1]kispy force "…"[/]  record a class your timetable does not know about

Settings live in [grey62]{_short(path)}[/] and are yours to edit.""",
             title="[bold]Done[/]", style="green")
    return 0
