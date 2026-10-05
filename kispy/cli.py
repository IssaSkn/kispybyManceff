"""The command surface. Everything but `status`, `doctor` and `setup` is quiet."""
from __future__ import annotations
import datetime, pathlib, subprocess, sys, time
from . import config, pipeline, recorder, schedule, state

PY = sys.executable
LIB = str(config.LIB)


def _slots(cfg: dict, want_source: bool = False):
    """The macOS Calendar first -- it follows a change within minutes. Then an
    imported timetable, then the published .ics feed, which Google caches for hours."""
    c = cfg["calendar"]
    source = c.get("source", "auto")
    order = {"auto": ["macos", "local", "ics"], "macos": ["macos"],
             "local": ["local"], "ics": ["ics"]}.get(source, ["macos", "local", "ics"])
    tried: list[str] = []
    for kind in order:
        try:
            if kind == "macos":
                slots = schedule.from_macos(c["exclude"], c["max_hours"], c.get("calendars"))
                return (slots, "macOS Calendar") if want_source else slots
            if kind == "local":
                slots = schedule.from_local(c["exclude"], c["max_hours"])
                return (slots, "imported timetable") if want_source else slots
            if kind == "ics":
                if not c["ics_url"]:
                    raise RuntimeError("no address configured")
                ics = schedule.fetch(c["ics_url"], c["cache_minutes"],
                                     config.state_dir() / "calendar.ics")
                slots = schedule.parse(ics, c["exclude"], c["max_hours"])
                return (slots, ".ics feed") if want_source else slots
        except Exception as exc:
            tried.append(f"{kind}: {str(exc)[:90]}")
    raise RuntimeError("; ".join(tried) or "no timetable configured")


def start() -> int:
    cfg = config.load()
    job = state.load()

    if job["stage"] == state.RECORDING and state.alive(job.get("pid")):
        print("● Already running.")
        return 0
    if job["stage"] in state.RUNNING_STAGES + (state.DOCUMENTS,):
        print(f"● Busy: {state.HUMAN[job['stage']]}.")
        return 0

    work = config.work_dir()
    if job["stage"] == state.PAUSED:                        # another segment
        index = len(job.get("segments", [])) + 1
    else:
        try:
            slot = schedule.current(_slots(cfg), datetime.datetime.now(schedule.TZ),
                                    cfg["calendar"]["lookahead_minutes"],
                                    cfg["calendar"]["grace_minutes"])
        except Exception as exc:
            state.set_stage(state.FAILED, error=str(exc))
            print(f"✗ {exc}")
            return 1
        if slot is None:
            state.set_stage(state.IDLE, error="no class at this hour in your timetable")
            print("No class at this hour in your timetable.")
            print('  `kispy force "course name"` records one anyway.')
            return 1
        folder = pipeline.session_folder(slot, cfg)
        job = state.save({"stage": state.IDLE, "subject": slot.subject,
                          "lecturer": slot.lecturer, "number": slot.number,
                          "date": f"{slot.start:%Y-%m-%d}", "ends_at": slot.end.isoformat(),
                          "folder": str(folder), "segments": [],
                          "started_at": datetime.datetime.now().isoformat(timespec="seconds")})
        index = 1

    try:
        pid = recorder.start(work, index, cfg["audio"]["device"], cfg["audio"]["sample_rate"])
    except RuntimeError as exc:
        state.set_stage(state.FAILED, error=str(exc))
        print(f"✗ {exc}")
        return 1
    segs = job.get("segments", []) + [str(recorder.segment_path(work, index))]
    state.set_stage(state.RECORDING, pid=pid, segments=segs, error=None,
                    segment_started=time.time())
    if index == 1:
        print(f"● {job['subject']} — Lecture {job['number']}. Recording.")
    else:
        print(f"● Resumed — segment {index}.")
    print("  You can close the terminal.")
    return 0


HANDLED = "handled.json"


def _handled() -> set:
    import json
    f = config.state_dir() / HANDLED
    try:
        return set(json.loads(f.read_text()))
    except Exception:
        return set()


def mark_handled(key: str) -> None:
    """Remember a session we already recorded, stopped or threw away, so the
    watchdog never starts it a second time."""
    import json
    f = config.state_dir() / HANDLED
    seen = _handled() | {key}
    try:
        f.write_text(json.dumps(sorted(seen)[-200:]))
    except OSError:
        pass


def _key(slot) -> str:
    return f"{slot.start:%Y-%m-%d %H:%M}|{slot.subject}"


def audit_folders() -> int:
    """Check that every recorded session sits in the right subject folder, and that
    nothing drifted. Reports only -- it never moves your files."""
    import collections, re
    cfg = config.load()
    root = pathlib.Path(cfg["output"]["root"])
    if not root.exists():
        print(f"{root} does not exist yet.")
        return 1
    try:
        slots = _slots(cfg)
    except Exception as exc:
        print(f"timetable unreadable: {exc}")
        return 1
    by_date = collections.defaultdict(list)
    for sl in slots:
        by_date[sl.start.date().isoformat()].append(sl)

    SESSION = re.compile(r"^(\d{4}-\d{2}-\d{2}) — (.+)$")
    problems, n = [], 0
    for subj in sorted(x for x in root.iterdir() if x.is_dir() and not x.name.startswith(".")):
        if (subj / ".git").exists():
            continue                                  # a code repository, out of scope
        for d in sorted(x for x in subj.iterdir() if x.is_dir()):
            m = SESSION.match(d.name)
            if not m:
                continue
            n += 1
            date, label = m.groups()
            if "off timetable" in label.lower():
                continue
            here = [sl for sl in by_date.get(date, [])
                    if schedule.folder_for(sl.subject, root).name == subj.name]
            if not here:
                others = sorted({sl.subject for sl in by_date.get(date, [])})
                problems.append(f"{subj.name}/{d.name}\n      the timetable has no such "
                                "class that day"
                                + (f" (it had: {', '.join(others)[:60]})" if others else ""))
            elif f"Lecture {here[0].number}" != label:
                problems.append(f"{subj.name}/{d.name}\n      the timetable says "
                                f"« Lecture {here[0].number} »")
            for f in d.iterdir():
                if f.suffix in (".tex", ".pdf") and "—" in f.stem and not f.stem.startswith(subj.name):
                    problems.append(f"{subj.name}/{d.name}/{f.name}\n      file name does not "
                                    "match the subject")
        for x in subj.rglob("*"):
            if x.is_dir() and not any(y for y in x.iterdir() if y.name != ".DS_Store"):
                problems.append(f"{x.relative_to(root)}\n      empty folder")

    print(f"Kispy — {n} session(s) filed, {len(problems)} anomaly(ies)")
    for pb in problems:
        print(f"  · {pb}")
    if not problems:
        print("  Everything is where it should be.")
    return 0


def notify(title: str, text: str) -> None:
    subprocess.run(["osascript", "-e",
                    f'display notification "{text}" with title "{title}"'], check=False)


def watch_capture() -> str | None:
    """A recording that stopped producing sound is worse than no recording: it looks
    fine and yields nothing. Closing the lid does exactly that -- the Mac sleeps and
    the microphone dies with it. Restart on a fresh segment and say so."""
    job = state.load()
    if job.get("stage") != state.RECORDING or not job.get("segments"):
        return None
    if subprocess.run(["pgrep", "-f", "cli.work()"], capture_output=True).returncode == 0:
        return None                                 # the pipeline owns the workspace
    seg = pathlib.Path(job["segments"][-1])
    size = seg.stat().st_size if seg.exists() else 0
    now = time.time()
    last, at = job.get("watch_size"), job.get("watch_at")
    if not state.update_fields(state.RECORDING, watch_size=size, watch_at=now):
        return None                                 # the stage moved under us

    if last is None or at is None:
        return None
    if size > last or now - at < 120:
        return None                                 # still growing, or too soon to judge
    if state.load().get("stage") != state.RECORDING:
        return None                                 # last check before acting

    quiet(pause)                                    # the capture is dead: revive it
    if quiet(start) != 0:
        notify("Kispy stopped", "The capture will not restart — check the microphone.")
        return "capture dead, could not restart"
    notify("Kispy resumed", "The capture had stopped (lid closed?). "
                            "Keep the Mac open during class.")
    return "capture frozen -> restarted on a new segment"


def quiet(fn):
    import contextlib, io
    with contextlib.redirect_stdout(io.StringIO()):
        return fn()


def tick() -> int:
    """One beat of the watchdog: close what is over, open what has begun,
    and make sure what is running is actually capturing sound."""
    out = []
    autostop()                                      # close a finished session first
    stalled = watch_capture()
    if stalled:
        out.append(stalled)
    try:
        _, src = _slots(config.load(), want_source=True)
        if src != "macOS Calendar":
            out.append(f"timetable source: {src}")
    except Exception as exc:
        out.append(f"timetable unreadable: {str(exc)[:50]}")
    job_before = state.load().get("stage", state.IDLE)
    import contextlib, io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        autostart()
    if state.load().get("stage") != job_before:
        first = buf.getvalue().strip().splitlines()
        out.append(first[0] if first else "started")
    for line in out:
        print(f"{datetime.datetime.now():%d/%m %H:%M}  {line}")
    return 0


def source() -> int:
    """Which timetable answered. Printed so the watchdog can log a fallback."""
    try:
        _, src = _slots(config.load(), want_source=True)
        print(src)
        return 0
    except Exception as exc:
        print(f"none ({exc})"[:70])
        return 1


OFF_SWITCH = "autostart-off"


def auto(argv: list[str]) -> int:
    """`kispy auto off` silences the watchdog; `kispy auto install` wires it in."""
    from . import wizard
    f = config.state_dir() / OFF_SWITCH
    want = (argv[0].lower() if argv else "")
    if want == "off":
        f.touch(); print("Automatic start disabled. `kispy auto on` brings it back.")
    elif want == "on":
        f.unlink(missing_ok=True); print("Automatic start enabled.")
    elif want == "install":
        ok = wizard.install_watchdog()
        print("Watchdog installed." if ok else "launchd refused it.")
        return 0 if ok else 1
    elif want in ("remove", "uninstall"):
        wizard.remove_watchdog(); print("Watchdog removed.")
    else:
        print("automatic start: " + ("disabled" if f.exists() else "on"))
        print("watchdog: " + ("running" if wizard.watchdog_running() else "not running"))
        print("  kispy auto on | off | install | remove")
    return 0


def autostart() -> int:
    """Run by the watchdog every minute. Starts the lecture you are sitting in,
    once, and never touches a session you already handled yourself."""
    if (config.state_dir() / OFF_SWITCH).exists():
        return 0
    cfg = config.load()
    job = state.load()
    if job.get("stage", state.IDLE) != state.IDLE:
        return 0                                   # something is already under way
    try:
        slots = _slots(cfg)
    except Exception:
        return 1                                   # no timetable reachable: stay silent
    now = datetime.datetime.now(schedule.TZ)
    slot = schedule.current(slots, now, 0, 0)      # strictly inside the slot, no grace
    if slot is None or _key(slot) in _handled():
        return 0

    folder = pipeline.session_folder(slot, cfg)
    state.save({"stage": state.PAUSED, "subject": slot.subject, "lecturer": slot.lecturer,
                "number": slot.number, "date": f"{slot.start:%Y-%m-%d}",
                "ends_at": slot.end.isoformat(), "folder": str(folder), "segments": [],
                "auto": True, "key": _key(slot),
                "started_at": datetime.datetime.now().isoformat(timespec="seconds")})
    if start() != 0:
        return 1
    mark_handled(_key(slot))
    subprocess.run(["osascript", "-e",
                    f'display notification "{slot.subject} · Lecture {slot.number}" '
                    f'with title "Kispy is recording" subtitle "T to finish, X to cancel"'],
                   check=False)
    return 0


def autostop() -> int:
    """Also run by the watchdog: close a session that the timetable says is over."""
    job = state.load()
    if job.get("stage") not in (state.RECORDING, state.PAUSED) or not job.get("auto"):
        return 0
    ends = job.get("ends_at")
    if not ends:
        return 0
    if datetime.datetime.now(schedule.TZ) < datetime.datetime.fromisoformat(ends) + \
            datetime.timedelta(minutes=int(config.load()["calendar"]["grace_minutes"])):
        return 0
    return stop()


def force(argv: list[str]) -> int:
    """Record a session the timetable does not know about -- added late, mistyped
    in a shared feed, or simply missing. The lecture number and the destination
    folder still come from the calendar when the subject is recognised there."""
    if not argv:
        print('Give the course name:  kispy force "Technology of blockchain"')
        return 2
    wanted = argv[0]
    cfg = config.load()
    job = state.load()
    if job.get("stage") in (state.RECORDING,) + state.RUNNING_STAGES + (state.DOCUMENTS,):
        print(f"● Busy: {state.HUMAN[job['stage']]}.")
        return 1

    subject, number, lecturer = wanted, 1, ""
    try:                                   # align with the timetable if we can
        slots = _slots(cfg)
        now = datetime.datetime.now(schedule.TZ)
        year = schedule.academic_year(now)
        # Only this year's courses: a feed can carry every cohort since 2019, and
        # an old lecturer's version of the same title would win on name alone.
        current = [s for s in slots if schedule.academic_year(s.start) == year]
        best, score = None, 0.0
        import difflib
        w = schedule._fold(wanted)
        for sl in current:
            have = schedule._fold(sl.subject)
            r = difflib.SequenceMatcher(None, w, have).ratio()
            if w and (w in have or have in w):     # "blockchain" names a long title
                r = max(r, 0.85)
            if r > score:
                best, score = sl, r
        if best and score >= 0.6:
            subject, lecturer = best.subject, best.lecturer
            past = [s for s in current
                    if schedule._fold(s.subject) == schedule._fold(subject)
                    and schedule.academic_year(s.start) == year and s.start < now]
            number = len(past) + 1
    except Exception:
        pass                               # no network, no calendar: keep the typed name

    slot = schedule.Slot(datetime.datetime.now(schedule.TZ),
                         datetime.datetime.now(schedule.TZ) + datetime.timedelta(hours=4),
                         subject, lecturer, "", wanted, number)
    folder = pipeline.session_folder(slot, cfg)
    # PAUSED makes start() take the "new segment" branch and skip the timetable check
    state.save({"stage": state.PAUSED, "subject": subject, "lecturer": lecturer,
                "number": number, "date": f"{slot.start:%Y-%m-%d}",
                "folder": str(folder), "segments": [],
                "started_at": datetime.datetime.now().isoformat(timespec="seconds")})
    rc = start()
    if rc == 0:
        print(f"  (off timetable — {subject}, Lecture {number})")
    return rc


def pause() -> int:
    job = state.load()
    if job["stage"] != state.RECORDING:
        return 1
    recorder.stop(job.get("pid"))
    state.set_stage(state.PAUSED, pid=None)
    print("⏸ Paused.  `kispy start` resumes.")
    return 0


def stop() -> int:
    job = state.load()
    if job["stage"] not in (state.RECORDING, state.PAUSED):
        return 1
    if job["stage"] == state.RECORDING:
        recorder.stop(job.get("pid"))
    if job.get("key"):
        mark_handled(job["key"])
    state.set_stage(state.DOCUMENTS, pid=None)
    subprocess.Popen([PY, "-c", f"import sys; sys.path.insert(0, {LIB!r}); "
                                "from kispy import cli; cli.work()"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     stdin=subprocess.DEVNULL, start_new_session=True)
    print("■ Recording finished. The document is being prepared.")
    print("  You can close the terminal, and your Mac.")
    return 0


def discard() -> int:
    """Throw the current take away: a test, or a bad start. Never touches a PDF
    that already exists, nor any document you put in the session folder."""
    import shutil
    from . import shred
    job = state.load()
    if job.get("stage", state.IDLE) == state.IDLE:
        print("Nothing to discard.")
        return 1
    if job.get("stage") == state.RECORDING:
        recorder.stop(job.get("pid"))
    if job.get("key"):
        mark_handled(job["key"])
    subprocess.run(["pkill", "-f", "cli.work()"], capture_output=True)
    shred.clean(config.work_dir())
    folder = job.get("folder")
    if folder and pathlib.Path(folder).is_dir() and not any(pathlib.Path(folder).iterdir()):
        shutil.rmtree(folder, ignore_errors=True)          # only if we left it empty
    state.clear()
    print("✗ Take discarded.")
    return 0


def work() -> int:
    pipeline.run(state.load())
    return 0


def selftest() -> int:
    """Record a few seconds and say what came back. The point is to trigger the
    microphone prompt, and prove the chain works, outside any lecture slot."""
    import tempfile
    cfg = config.load()
    work = pathlib.Path(tempfile.mkdtemp())
    try:
        name = recorder.resolve(cfg["audio"]["device"])
    except RuntimeError as exc:
        print(f"✗ {exc}"); return 1
    print(f"Kispy — testing « {name} ». Speak for 8 seconds…")
    pid = recorder.start(work, 1, cfg["audio"]["device"], cfg["audio"]["sample_rate"])
    time.sleep(8)
    recorder.stop(pid)
    wav = recorder.segment_path(work, 1)
    if not wav.exists() or wav.stat().st_size < 32000:
        print("✗ nothing was captured.")
        print("  System Settings ▸ Privacy & Security ▸ Microphone,")
        print("  and allow the terminal you run kispy from.")
        return 1
    secs = wav.stat().st_size / (2 * cfg["audio"]["sample_rate"])
    print(f"✓ {secs:.0f} s captured. Transcribing…")
    from . import transcribe
    try:
        text = transcribe.run(wav, "", cfg, work / "t")
    except Exception as exc:
        print(f"✗ transcription failed: {exc}")
        return 1
    print(f"\n  « {text.strip()[:300] or '(silence)'} »\n")
    print("All set." if text.strip() else "The microphone works but heard nothing.")
    for f in work.iterdir():
        f.unlink(missing_ok=True)
    work.rmdir()
    return 0


def status() -> int:
    job = state.load()
    stage = job.get("stage", state.IDLE)
    label = state.HUMAN.get(stage, stage)

    if stage == state.IDLE:
        print(f"Kispy — {label}." + (f"\n{job['error']}." if job.get("error") else ""))
        return 0
    head = f"{job.get('subject','?')} · Lecture {job.get('number','?')} · {job.get('date','')}"
    print(f"Kispy — {label}\n{head}")

    if stage in (state.RECORDING, state.PAUSED):
        segs = [pathlib.Path(s) for s in job.get("segments", [])]
        mb = sum(s.stat().st_size for s in segs if s.exists()) / 1e6
        mins = mb * 1e6 / (2 * 16000) / 60
        print(f"segment {len(segs)} · {mins:.0f} min captured")
        if stage == state.RECORDING and not state.alive(job.get("pid")):
            print("⚠ the capture is not running — restart with `kispy start`")
        elif stage == state.RECORDING and job.get("segment_started"):
            elapsed = time.time() - job["segment_started"]
            captured = (segs[-1].stat().st_size if segs and segs[-1].exists() else 0) / (2 * 16000)
            if elapsed > 90 and captured < elapsed / 3:
                print("⚠ the microphone is producing almost nothing — check the input")
            else:
                print("All OK.")
        else:
            print("All OK.")
    elif stage == state.DONE:
        print(job.get("pdf", ""))
        findings = job.get("findings") or []
        if findings:
            print(f"\n{len(findings)} note(s) from the proofreading:")
            for f in findings:
                print(f"  · {f}")
        else:
            print("Proofreading: nothing to report.")
    elif stage == state.FAILED:
        print(f"⚠ {job.get('error','')}\nThe audio is kept: `kispy stop` runs it again.")
    else:
        print("All OK. You can close your Mac, it resumes on wake.")
    return 0


def doctor() -> int:
    from . import wizard
    cfg = config.load()
    ok = True

    def check(name, good, detail=""):
        nonlocal ok
        ok &= bool(good)
        print(f"  {'✓' if good else '✗'} {name}" + (f"  {detail}" if detail else ""))

    print("Kispy")
    check("configuration", config.exists(),
          "" if config.exists() else "never set up — run `kispy setup`")
    try:
        sl, src = _slots(cfg, want_source=True)
        check("timetable", len(sl) > 0, f"{len(sl)} sessions · source: {src}")
        now = datetime.datetime.now(schedule.TZ)
        today = [s for s in sl if s.start.date() == now.date()]
        if today:
            print("    today:")
            for s in today:
                print(f"      {s.start:%H:%M}–{s.end:%H:%M}  {s.subject[:38]} "
                      f"· Lecture {s.number}")
    except Exception as exc:
        detail = str(exc)
        check("timetable", False, detail if len(detail) <= 60 else "")
        if len(detail) > 60:
            for part in detail.split("; "):
                print(f"      {part}")
    check("speech model", pathlib.Path(cfg["transcribe"]["model"]).exists(),
          pathlib.Path(cfg["transcribe"]["model"]).name)
    try:
        check("microphone", True, recorder.resolve(cfg["audio"]["device"]))
    except RuntimeError as exc:
        check("microphone", False, str(exc)[:90])
    for binary, hint in (("ffmpeg", "brew install ffmpeg"),
                         ("tectonic", "brew install tectonic"),
                         ("pdftoppm", "brew install poppler")):
        found = subprocess.run(["which", binary], capture_output=True).returncode == 0
        check(binary, found, "" if found else hint)
    st = wizard.claude_state()
    check("Claude", st == "ok",
          {"ok": "", "not-installed": "npm install -g @anthropic-ai/claude-code",
           "not-logged-in": "run `claude` once, then /login"}[st])
    check("output folder", pathlib.Path(cfg["output"]["root"]).exists(),
          cfg["output"]["root"].replace(str(pathlib.Path.home()), "~"))
    running = wizard.watchdog_running()          # optional: never a reason to fail
    print(f"  {'✓' if running else '·'} automatic start"
          + ("" if running else "  off — `kispy auto install` turns it on"))
    return 0 if ok else 1


HELP = """\
Kispy — records your lectures and hands you the written course.

  kispy                 open the app
  kispy setup           connect Claude, your microphone and your timetable
  kispy doctor          check everything is wired

  kispy start           start recording now (needs a class in your timetable)
  kispy force "name"    record a class the timetable does not know about
  kispy pause           pause; `start` resumes on a new segment
  kispy stop            finish, and write the document
  kispy discard         throw the current take away
  kispy status          what it is doing right now

  kispy timetable       import or review your timetable
  kispy audit           check your course folders are tidy
  kispy auto on|off     silence the automatic start, or bring it back

Settings:  ~/.config/kispy/config.toml
"""


def app() -> int:
    from . import tui                      # imported here: tui imports this module
    return tui.run()


def main(argv: list[str]) -> int:
    cmd = (argv[1] if len(argv) > 1 else "").lower()
    if cmd in ("setup", "install"):
        from . import wizard
        return wizard.run(argv[2:])
    if cmd in ("timetable", "schedule"):
        from . import timetable
        return timetable.main(argv[2:])
    if cmd == "force":
        return force(argv[2:])
    if cmd == "auto":
        return auto(argv[2:])
    if cmd in ("help", "-h", "--help"):
        print(HELP)
        return 0
    if cmd in ("", "start", "go", "resume") and not config.exists():
        print("Kispy is not set up yet.\n\n  kispy setup\n")
        return 1
    fn = {"": app, "start": start, "go": start, "resume": start,
          "pause": pause, "stop": stop, "status": status,
          "doctor": doctor, "test": selftest, "discard": discard,
          "autostart": autostart, "autostop": autostop, "source": source, "tick": tick,
          "audit": audit_folders, "__work": work}.get(cmd)
    if fn is None:
        print(f"Unknown command: {cmd}\n")
        print(HELP)
        return 2
    return fn()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
