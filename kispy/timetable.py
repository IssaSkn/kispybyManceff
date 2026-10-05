"""Import a timetable that is not in any calendar app.

Not everyone's school publishes an .ics feed, and not everyone keeps their classes
in the macOS Calendar. The fallback is to let a model read whatever the school did
give you -- a screenshot of a portal, a PDF, a photo of a wall planner, or three
lines typed from memory -- and turn it into the one structure Kispy needs.

What is saved is a small JSON file you can open and fix by hand; nothing about
this step is magic, and nothing leaves the Mac except the timetable itself.
"""
from __future__ import annotations
import datetime, json, pathlib, re, subprocess, tempfile
from . import config, schedule, ui

PATH = config.CONFIG_DIR / "timetable.json"
READABLE = {".png", ".jpg", ".jpeg", ".heic", ".webp", ".pdf", ".txt", ".md", ".csv"}

PROMPT = """\
You are reading a university timetable and turning it into JSON. Nothing else.

Return ONLY a JSON object, with no commentary and no code fence, shaped exactly
like this:

{{
  "term": {{"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}},
  "courses": [
    {{"subject": "Econometrics 1", "lecturer": "A. Rossi", "location": "Room S2",
      "weekday": "monday", "start": "09:45", "end": "12:45",
      "from": null, "to": null}}
  ],
  "sessions": [
    {{"subject": "Machine Learning", "lecturer": null, "location": null,
      "date": "2025-10-03", "start": "14:00", "end": "17:00"}}
  ]
}}

Rules:
  - "courses" is for anything that repeats every week at the same time.
  - "sessions" is for one-off dates. If the timetable lists explicit dates for
    every session instead of a weekly pattern, put them all in "sessions" and
    leave "courses" empty.
  - "from" and "to" bound a weekly course that only runs over part of the term;
    use null when it runs throughout.
  - 24-hour times. Weekdays in English, lower case.
  - Keep the course name as written. The teacher goes in "lecturer", never inside
    "subject".
  - Leave out anything that is not a taught session: exams, holidays, self-study,
    reading weeks, deadlines, welcome events, sports.
  - If a value is not visible, use null. Never invent a date, a time or a room.
  - If what you are given is not a timetable, return {{"error": "<what it is>"}}.

{source}
"""

TODAY = datetime.date.today()


def _default_term() -> tuple[str, str]:
    m = schedule.term_start_month()
    y = TODAY.year if TODAY.month >= m else TODAY.year - 1
    return f"{y}-{m:02d}-01", f"{y + 1}-06-30"


def _json_from(reply: str) -> dict:
    reply = re.sub(r"^```(?:json)?|```$", "", reply.strip(), flags=re.M).strip()
    a, b = reply.find("{"), reply.rfind("}")
    if a < 0 or b <= a:
        raise ValueError("the model did not return JSON")
    return json.loads(reply[a:b + 1])


def _read(source: str, files: list[pathlib.Path], cfg: dict) -> dict:
    """Ask the model. Reading a file needs the Read tool and the folder allowed."""
    cmd = ["claude", "-p", "--output-format", "text",
           "--model", cfg["documents"].get("model", "claude-haiku-4-5")]
    dirs = {str(f.parent.resolve()) for f in files}
    if dirs:
        cmd += ["--allowedTools", "Read"]
        for d in dirs:
            cmd += ["--add-dir", d]
    else:
        cmd += ["--disallowedTools", "Bash,Read,Write,Edit,WebFetch,WebSearch"]
    with tempfile.TemporaryDirectory() as sandbox:
        r = subprocess.run(cmd, input=PROMPT.format(source=source), capture_output=True,
                           text=True, cwd=sandbox,
                           timeout=cfg["documents"].get("timeout_seconds", 900))
    out = (r.stdout or "").strip()
    if "Not logged in" in out or "Please run /login" in out:
        raise RuntimeError("Claude is not connected — run `claude` once, then /login")
    if r.returncode != 0 or not out:
        raise RuntimeError((r.stderr or out or "the model returned nothing")[:300])
    return _json_from(out)


def _time(v) -> str | None:
    t = schedule._time(v)
    return f"{t.hour:02d}:{t.minute:02d}" if t else None


def _date(v) -> str | None:
    d = schedule._date(v)
    return d.isoformat() if d else None


def clean(data: dict) -> tuple[dict, list[str]]:
    """Keep what is usable, say what was dropped. A model that invents a room is
    harmless; one that invents a time would start recording in an empty corridor."""
    dropped: list[str] = []
    term = data.get("term") or {}
    out = {"term": {"start": _date(term.get("start")), "end": _date(term.get("end"))},
           "courses": [], "sessions": []}

    for c in data.get("courses") or []:
        subject = str(c.get("subject") or "").strip()
        day = schedule.WEEKDAYS.get(str(c.get("weekday") or "").strip().lower())
        start, end = _time(c.get("start")), _time(c.get("end"))
        if not subject or day is None or not start or not end or start >= end:
            dropped.append(f"weekly: {subject or '(no name)'} {c.get('weekday')} {c.get('start')}")
            continue
        out["courses"].append({
            "subject": subject,
            "lecturer": (str(c.get("lecturer")).strip() if c.get("lecturer") else ""),
            "location": (str(c.get("location")).strip() if c.get("location") else ""),
            "weekday": [k for k, v in schedule.WEEKDAYS.items()
                        if v == day and len(k) > 3][0],
            "start": start, "end": end,
            "from": _date(c.get("from")), "to": _date(c.get("to")),
        })

    for s in data.get("sessions") or []:
        subject = str(s.get("subject") or "").strip()
        date, start, end = _date(s.get("date")), _time(s.get("start")), _time(s.get("end"))
        if not subject or not date or not start or not end or start >= end:
            dropped.append(f"one-off: {subject or '(no name)'} {s.get('date')}")
            continue
        out["sessions"].append({
            "subject": subject,
            "lecturer": (str(s.get("lecturer")).strip() if s.get("lecturer") else ""),
            "location": (str(s.get("location")).strip() if s.get("location") else ""),
            "date": date, "start": start, "end": end,
        })
    return out, dropped


ORDER = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def show(data: dict) -> None:
    term = data.get("term") or {}
    if term.get("start") or term.get("end"):
        ui.note(f"term: {term.get('start') or '?'} → {term.get('end') or '?'}")
        ui.console.print()
    by_day: dict[str, list[dict]] = {}
    for c in data.get("courses", []):
        by_day.setdefault(c["weekday"], []).append(c)
    for day in ORDER:
        rows = sorted(by_day.get(day, []), key=lambda c: c["start"])
        if not rows:
            continue
        ui.console.print(f"  [bold]{day.capitalize()}[/]")
        for c in rows:
            extra = "  ".join(x for x in (c.get("lecturer"), c.get("location")) if x)
            window = ""
            if c.get("from") or c.get("to"):
                window = f"   [grey62]({c.get('from') or '…'} → {c.get('to') or '…'})[/]"
            ui.console.print(f"     [deep_sky_blue1]{c['start']}–{c['end']}[/]  {c['subject']}"
                             + (f"   [grey62]{extra}[/]" if extra else "") + window,
                             highlight=False)
    if data.get("sessions"):
        ui.console.print()
        ui.console.print("  [bold]One-off sessions[/]")
        for s in sorted(data["sessions"], key=lambda s: (s["date"], s["start"])):
            ui.console.print(f"     [deep_sky_blue1]{s['date']} {s['start']}–{s['end']}[/]  "
                             f"{s['subject']}", highlight=False)


def save(data: dict) -> pathlib.Path:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return PATH


def load() -> dict | None:
    if not PATH.exists():
        return None
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _as_path(raw: str) -> pathlib.Path | None:
    """A file dragged into Terminal arrives quoted and backslash-escaped."""
    s = raw.strip().strip("'\"").replace("\\ ", " ").replace("\\~", "~")
    p = pathlib.Path(s).expanduser()
    return p if p.exists() and p.is_file() else None


def _collect_files() -> list[pathlib.Path]:
    ui.say("Drag the file into this window and press return.")
    ui.note("a screenshot, a photo, a PDF — several in a row if your timetable "
            "comes in pieces. Empty line when you are done.")
    ui.console.print()
    files: list[pathlib.Path] = []
    while True:
        raw = ui.ask("File" if not files else "Another file (or press return)")
        if not raw:
            break
        p = _as_path(raw)
        if not p:
            ui.bad("no file at that path")
            continue
        if p.suffix.lower() not in READABLE:
            ui.bad(f"{p.suffix} cannot be read — use an image, a PDF or a text file")
            continue
        files.append(p)
        ui.good(p.name)
    return files


def _typed() -> str:
    ui.say("Type your week, one line per class. Anything readable will do:")
    ui.note('e.g.  Monday 9:45-12:45  Blockchain  with Rossi  room S2')
    ui.note("empty line when you are done")
    ui.console.print()
    lines = []
    while True:
        try:
            line = input("    ")
        except (EOFError, KeyboardInterrupt):
            break
        if not line.strip():
            break
        lines.append(line.strip())
    return "\n".join(lines)


def ask_term(data: dict) -> dict:
    """Dates bound the expansion: without them a weekly course is a course forever."""
    term = data.get("term") or {}
    d0, d1 = _default_term()
    if not term.get("start"):
        term["start"] = _date(ui.ask("First day of term (YYYY-MM-DD)", d0)) or d0
    if not term.get("end"):
        term["end"] = _date(ui.ask("Last day of term (YYYY-MM-DD)", d1)) or d1
    data["term"] = term
    return data


def import_flow(cfg: dict, files: list[pathlib.Path] | None = None) -> dict | None:
    """Read a timetable, show what was understood, save it once confirmed."""
    text = ""
    if files is None:
        kind = ui.choose("Where is your timetable?", [
            ("A screenshot, a photo or a PDF", "the portal, an email, a page of your diary"),
            ("I will type it", "one line per class, in your own words"),
        ])
        if kind == 1:
            files = _collect_files()
            if not files:
                return None
        else:
            files, text = [], _typed()
            if not text.strip():
                return None

    source = ("Read these files:\n" + "\n".join(f"  {f.resolve()}" for f in files)
              if files else f"The timetable, as the student typed it:\n\n{text}")
    ui.console.print()
    with ui.console.status("  [grey62]reading your timetable…[/]", spinner="dots"):
        try:
            raw = _read(source, files, cfg)
        except Exception as exc:
            ui.bad(str(exc)[:200])
            return None
    if raw.get("error"):
        ui.bad(f"that does not look like a timetable: {str(raw['error'])[:120]}")
        return None

    data, dropped = clean(raw)
    if not data["courses"] and not data["sessions"]:
        ui.bad("nothing usable was found in there")
        return None

    ui.console.print()
    show(data)
    ui.console.print()
    for d in dropped[:5]:
        ui.warn(f"skipped, incomplete — {d}")

    if not ui.confirm("Is that your timetable?"):
        ui.note(f"nothing saved. You can also write it yourself: {PATH}")
        return None
    data = ask_term(data)
    save(data)
    ui.good(f"saved to {PATH}")
    return data


def edit() -> int:
    if not PATH.exists():
        ui.bad("no timetable yet — run `kispy timetable import`")
        return 1
    subprocess.run(["open", "-t", str(PATH)], check=False)
    ui.good(f"opened {PATH}")
    return 0


def main(argv: list[str]) -> int:
    cfg = config.load()
    cmd = (argv[0].lower() if argv else "")
    if cmd == "edit":
        return edit()
    if cmd == "show":
        data = load()
        if not data:
            ui.bad("no timetable imported")
            return 1
        show(data)
        return 0
    if cmd == "import":
        files = [p for p in (_as_path(a) for a in argv[1:]) if p]
        return 0 if import_flow(cfg, files or None) else 1

    data = load()
    if data:
        show(data)
        ui.console.print()
        if not ui.confirm("Replace it?", default=False):
            return 0
    return 0 if import_flow(cfg) else 1
