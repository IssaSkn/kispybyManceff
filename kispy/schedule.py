"""Read the timetable, work out which lecture is happening, and where it belongs.

A university calendar is never clean: the same course is spelled several ways,
non-teaching slots are mixed in, and shared feeds duplicate events. Everything
here exists to turn that into one unambiguous answer.

Three sources, in order of preference:
  macos  what the Calendar app has synced (no OAuth, follows a change in minutes)
  ics    a published .ics URL (complete, but Google caches its exports for hours)
  local  a timetable you imported once, from a screenshot or by hand
"""
from __future__ import annotations
import dataclasses, datetime, difflib, json, pathlib, re, unicodedata, zoneinfo
from . import config


def system_tz() -> datetime.tzinfo:
    p = pathlib.Path("/etc/localtime")
    if p.is_symlink():
        name = str(p.readlink()).split("zoneinfo/")[-1]
        try:
            return zoneinfo.ZoneInfo(name)
        except Exception:
            pass
    return datetime.datetime.now().astimezone().tzinfo


def _configured_tz() -> datetime.tzinfo:
    try:
        name = config.load()["calendar"].get("timezone", "")
        if name:
            return zoneinfo.ZoneInfo(name)
    except Exception:
        pass
    return system_tz()


TZ = _configured_tz()

# " - Anna Rossi" / " / M. Duarte" / " - Clara Mendes, Jonas Weber"
LECTURER = re.compile(
    r"\s+[-/]\s*("
    r"[A-ZÉÈÀÂÎÔÛÇ][\w'’.-]*"
    r"(?:(?:[\s,]+|\s*&\s*|\s+and\s+)[A-ZÉÈÀÂÎÔÛÇ][\w'’.-]*)*"
    r")\s*[-/]?\s*$")

WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
            "friday": 4, "saturday": 5, "sunday": 6,
            "lundi": 0, "mardi": 1, "mercredi": 2, "jeudi": 3,
            "vendredi": 4, "samedi": 5, "dimanche": 6,
            "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


@dataclasses.dataclass
class Slot:
    start: datetime.datetime
    end: datetime.datetime
    subject: str          # canonical, human-readable
    lecturer: str
    location: str
    raw: str
    number: int = 0       # nth teaching session of that subject in the year

    @property
    def hours(self) -> float:
        return (self.end - self.start).total_seconds() / 3600


def fetch(url: str, cache_minutes: int, cache: pathlib.Path) -> bytes:
    """Fetch the feed, falling back to the cache when the network is not there."""
    import requests
    fresh = (cache.exists()
             and datetime.datetime.now().timestamp() - cache.stat().st_mtime < cache_minutes * 60)
    if fresh:
        return cache.read_bytes()
    try:
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(r.content)
        return r.content
    except Exception:
        if cache.exists():
            return cache.read_bytes()      # stale beats nothing
        raise


def _as_dt(value) -> datetime.datetime:
    d = value.dt
    if isinstance(d, datetime.datetime):
        return d.astimezone(TZ)
    return datetime.datetime(d.year, d.month, d.day, tzinfo=TZ)


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^\w\s]", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def split_title(raw: str) -> tuple[str, str]:
    """Separate the course from whoever teaches it."""
    title = re.sub(r"\s+", " ", raw).strip().rstrip("-/ ").strip()
    m = LECTURER.search(title)
    if m:
        return title[: m.start()].strip(" -/"), m.group(1).strip(" ,")
    return title, ""


def build(events, exclude: list[str], max_hours: float) -> list[Slot]:
    """Turn raw (start, end, title, location) tuples into teaching slots:
    non-courses dropped, duplicates removed, spellings merged, lectures numbered."""
    drop = re.compile("|".join(exclude), re.I) if exclude else None
    slots, seen = [], set()
    for start, end, title, location in events:
        raw = re.sub(r"\s+", " ", title).strip()
        if not raw or (drop and drop.search(raw)):
            continue
        if not 0 < (end - start).total_seconds() / 3600 <= max_hours:
            continue                                     # all-day markers, zero-length entries
        subject, lecturer = split_title(raw)
        key = (start, _fold(subject))
        if key in seen:
            continue                                     # feeds contain duplicates
        seen.add(key)
        slots.append(Slot(start, end, subject, lecturer, (location or "").strip(), raw))

    slots.sort(key=lambda s: s.start)
    _canonicalise(slots)
    _number(slots)
    return slots


def parse(ics: bytes, exclude: list[str], max_hours: float) -> list[Slot]:
    """A published .ics feed."""
    import icalendar
    cal = icalendar.Calendar.from_ical(ics)
    events = []
    for ev in cal.walk("VEVENT"):
        if "DTSTART" not in ev:
            continue
        start = _as_dt(ev["DTSTART"])
        end = _as_dt(ev["DTEND"]) if "DTEND" in ev else start
        events.append((start, end, str(ev.get("SUMMARY", "")),
                       str(ev.get("LOCATION", ""))))
    return build(events, exclude, max_hours)


EVENTKIT = config.BIN / "kispy-cal"


def macos_calendars() -> list[tuple[str, str]]:
    """Every calendar the Calendar app knows about, as (name, account)."""
    import subprocess
    if not EVENTKIT.exists():
        raise RuntimeError("kispy-cal is not installed")
    r = subprocess.run([str(EVENTKIT), "--list"], capture_output=True, text=True, timeout=90)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or "the macOS calendar is unreachable").strip())
    out = []
    for line in r.stdout.splitlines():
        if "\t" in line:
            name, account = line.split("\t", 1)
            out.append((name.strip(), account.strip()))
    return out


def from_macos(exclude: list[str], max_hours: float,
               calendars: list[str] | None = None) -> list[Slot]:
    """What the Calendar app has synced. No OAuth, no token to refresh, and it
    follows a change within minutes instead of hours. Raises if unavailable."""
    import subprocess
    if not EVENTKIT.exists():
        raise RuntimeError("kispy-cal is not installed")
    since = datetime.date(academic_year(datetime.datetime.now(TZ)), term_start_month(), 1)
    r = subprocess.run([str(EVENTKIT), "--since", since.isoformat()],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or "the macOS calendar is unreachable").strip())
    wanted = {c.strip().lower() for c in (calendars or []) if c.strip()}
    events = []
    for e in json.loads(r.stdout or "[]"):
        if wanted and e.get("calendar", "").strip().lower() not in wanted:
            continue
        events.append((datetime.datetime.fromisoformat(e["start"]).astimezone(TZ),
                       datetime.datetime.fromisoformat(e["end"]).astimezone(TZ),
                       e.get("summary", ""), e.get("location", "")))
    if not events:
        raise RuntimeError("no events in the selected calendars")
    return build(events, exclude, max_hours)


TIMETABLE = config.CONFIG_DIR / "timetable.json"


def from_local(exclude: list[str], max_hours: float,
               path: pathlib.Path | None = None) -> list[Slot]:
    """A timetable imported once -- from a screenshot, a PDF or by hand -- and
    expanded here into the individual sessions of the year."""
    p = path or TIMETABLE
    if not p.exists():
        raise RuntimeError("nothing imported")
    data = json.loads(p.read_text(encoding="utf-8"))
    term = data.get("term", {})
    t0 = _date(term.get("start")) or datetime.date(datetime.date.today().year, 9, 1)
    t1 = _date(term.get("end")) or (t0 + datetime.timedelta(days=300))

    events = []
    for c in data.get("courses", []):
        day = WEEKDAYS.get(str(c.get("weekday", "")).strip().lower())
        start_t, end_t = _time(c.get("start")), _time(c.get("end"))
        if day is None or not start_t or not end_t:
            continue
        a = _date(c.get("from")) or t0
        b = _date(c.get("to")) or t1
        skip = {_date(x) for x in c.get("except", []) if _date(x)}
        d = a + datetime.timedelta(days=(day - a.weekday()) % 7)
        while d <= b:
            if d not in skip:
                events.append(_event(c, d, start_t, end_t))
            d += datetime.timedelta(days=7)
    for s in data.get("sessions", []):
        d = _date(s.get("date"))
        start_t, end_t = _time(s.get("start")), _time(s.get("end"))
        if d and start_t and end_t:
            events.append(_event(s, d, start_t, end_t))
    if not events:
        raise RuntimeError("the imported timetable has no usable session")
    return build(events, exclude, max_hours)


def _event(c: dict, d: datetime.date, start_t, end_t):
    title = str(c.get("subject", "")).strip()
    if c.get("lecturer"):
        title = f"{title} - {str(c['lecturer']).strip()}"
    return (datetime.datetime.combine(d, start_t, TZ),
            datetime.datetime.combine(d, end_t, TZ),
            title, str(c.get("location", "")))


def _date(v) -> datetime.date | None:
    try:
        return datetime.date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def _time(v):
    m = re.match(r"\s*(\d{1,2})\s*[:hH.]\s*(\d{2})?", str(v or ""))
    if not m:
        return None
    return datetime.time(int(m.group(1)), int(m.group(2) or 0))


def _canonicalise(slots: list[Slot]) -> None:
    """Group spellings of the same course and give the group one name.

    'Fintech Research Seminar', 'FinTech Research Seminar' and the double-spaced
    variant are one course; a trailing module number ('Econometrics 1') is kept,
    because it marks a genuinely different module.
    """
    groups: list[tuple[str, list[Slot]]] = []          # (folded key, members)
    for s in slots:
        k = _fold(s.subject)
        for gk, members in groups:
            if k == gk or difflib.SequenceMatcher(None, k, gk).ratio() >= 0.90:
                members.append(s)
                break
        else:
            groups.append((k, [s]))

    for _, members in groups:
        # the most frequent spelling wins, ties broken by the longest
        counts: dict[str, int] = {}
        for s in members:
            counts[s.subject] = counts.get(s.subject, 0) + 1
        best = max(counts, key=lambda t: (counts[t], len(t)))
        for s in members:
            s.subject = best


def term_start_month() -> int:
    try:
        return int(config.load()["calendar"].get("term_start_month", 9)) or 9
    except Exception:
        return 9


def academic_year(d: datetime.datetime) -> int:
    """A new year starts in September (or whenever you said it does); a feed can
    carry every cohort since 2019, and 'Lecture 1' must mean this year's first."""
    m = term_start_month()
    return d.year if d.month >= m else d.year - 1


def _number(slots: list[Slot]) -> None:
    seen: dict[tuple[int, str], int] = {}
    for s in slots:
        k = (academic_year(s.start), _fold(s.subject))
        seen[k] = seen.get(k, 0) + 1
        s.number = seen[k]


def current(slots: list[Slot], now: datetime.datetime,
            lookahead_minutes: int, grace_minutes: int) -> Slot | None:
    """The lecture you are sitting in, or the one about to start."""
    lo = datetime.timedelta(minutes=lookahead_minutes)
    hi = datetime.timedelta(minutes=grace_minutes)
    live = [s for s in slots if s.start - lo <= now <= s.end + hi]
    return min(live, key=lambda s: abs((s.start - now).total_seconds())) if live else None


ALIASES = config.CONFIG_DIR / "folders.toml"


def _aliases() -> dict[str, str]:
    if not ALIASES.exists():
        return {}
    import tomllib
    try:
        return {_fold(k): v for k, v in tomllib.loads(ALIASES.read_text(encoding="utf-8")).items()}
    except Exception:
        return {}


def remember(subject: str, folder_name: str) -> None:
    """Record where a subject was filed, so the next session goes to the same place."""
    try:
        ALIASES.parent.mkdir(parents=True, exist_ok=True)
        existing = ALIASES.read_text(encoding="utf-8") if ALIASES.exists() else ""
        if f'"{subject}"' in existing:
            return
        with ALIASES.open("a", encoding="utf-8") as fh:
            if not existing:
                fh.write("# Which course goes in which folder. This table wins over\n"
                         "# name matching, and Kispy appends to it as it learns.\n\n")
            elif not existing.endswith("\n"):
                fh.write("\n")
            fh.write(f'"{subject}" = "{folder_name}"\n')
    except OSError:
        pass


def folder_for(subject: str, root: pathlib.Path, learn: bool = False) -> pathlib.Path:
    """Where this subject's sessions live.

    The alias table decides first. Guessing by name is only a last resort: it once
    filed « Thesis Seminar » under « Thesis » because one title is a prefix of the
    other, and it cannot know that a folder called « SL » holds « Statistical
    Learning ». Whatever is decided here is written back to the table, so a course
    never moves between folders from one session to the next.
    """
    alias = _aliases().get(_fold(subject))
    if alias:
        return root / alias
    if not root.exists():
        return root / subject

    want = _fold(subject)
    initials = "".join(w[0] for w in want.split()
                       if w.isalpha() and w not in
                       {"and", "of", "in", "the", "for", "to", "a"})
    best, score = None, 0.0
    for d in root.iterdir():
        if not d.is_dir() or d.name.startswith("."):
            continue
        if any((d / marker).exists() for marker in (".git", ".venv", "requirements.txt")):
            continue                       # a code project, not a notes folder
        have = _fold(d.name)
        if not have:
            continue
        r = difflib.SequenceMatcher(None, want, have).ratio()
        if have == initials:                          # QMF = quantitative methods in finance
            r = max(r, 0.95)
        if r > score:
            best, score = d, r

    chosen = best if score >= 0.86 else root / subject
    if learn:
        remember(subject, chosen.name)
    return chosen
