"""Kispy's face. A small terminal app: one screen, five keys, and a creature
whose expression tells you what is going on without you having to read."""
from __future__ import annotations
import contextlib, io, os, select, sys, termios, time, tty

from rich.align import Align
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import cli, state

WORDMARK = r"""╦╔═ ╦ ╔═╗ ╔═╗ ╦ ╦
╠╩╗ ║ ╚═╗ ╠═╝ ╚╦╝
╩ ╩ ╩ ╚═╝ ╩     ╩"""

# Kispy's moods. Each is a palette plus a face -- the body takes the mood colour,
# the cheeks stay pink, the sparkles stay gold, so he reads at a glance.
FACES = {
    "idle":   dict(body="sky_blue3",       eyes="grey54",        eyeL="–", eyeR="–",
                   mouth="·", left="    ", right="  ᶻ ", spark="",
                   caption="having a nap"),
    "ready":  dict(body="pale_turquoise1", eyes="deep_sky_blue1", eyeL="◕", eyeR="◕",
                   mouth="‿", left="    ", right="    ", spark="✦",
                   caption="ready when you are"),
    "record": dict(body="light_salmon1",   eyes="deep_pink2",    eyeL="◕", eyeR="◕",
                   mouth="○", left="))  ", right="  ((", spark="",
                   caption="listening"),
    "paused": dict(body="khaki1",          eyes="orange3",       eyeL="˘", eyeR="˘",
                   mouth="‿", left="    ", right="    ", spark="",
                   caption="waiting for the rest"),
    "work":   dict(body="plum2",           eyes="medium_purple1", eyeL="◔", eyeR="◔",
                   mouth="~", left="    ", right="  ✎ ", spark="",
                   caption="writing it up"),
    "done":   dict(body="pale_green1",     eyes="spring_green1", eyeL="^", eyeR="^",
                   mouth="‿", left="    ", right="    ", spark="✦",
                   caption="all done!"),
    "sad":    dict(body="grey62",          eyes="indian_red1",   eyeL="×", eyeR="×",
                   mouth="⌒", left="    ", right="    ", spark="",
                   caption="something went wrong"),
}

CHEEK = "hot_pink2"
SPARK = "gold1"

STAGE_FACE = {
    state.IDLE: "idle", state.RECORDING: "record", state.PAUSED: "paused",
    state.DOCUMENTS: "work", state.TRANSCRIBING: "work",
    state.SYNTHESISING: "work", state.BUILDING: "work",
    state.DONE: "done", state.FAILED: "sad",
}


BLINKING = {"ready", "record", "work", "done"}


def creature(kind: str, frame: int = 0) -> Text:
    """Drawn on a fixed grid so nothing shifts between moods: ears at columns 5-6
    and 12-13, body walls at 4 and 14, feet under the legs. `frame` advances four
    times a second -- he blinks, and his sparkles twinkle."""
    f = dict(FACES[kind])
    if kind in BLINKING and frame % 20 == 0:          # a blink every five seconds
        f["eyeL"] = f["eyeR"] = "–"
    if f["spark"] and frame % 6 < 3:                   # twinkle
        f["spark"] = "·"
    if kind == "record":                               # the sound comes and goes
        f["left"], f["right"] = (("))  ", "  ((") if frame % 4 < 2 else ((" )  ", "  ( ")))
    body, eyes, spark = f["body"], f["eyes"], f["spark"]
    t = Text()

    def line(*segments):
        for txt, style in segments:
            t.append(txt, style=style)
        t.append("\n")

    sp = spark or " "
    line(("  " + sp + "  ", SPARK), ("╭╮", body), ("     ", None), ("╭╮", body),
         ("  " + sp, SPARK))
    line(("    ", None), ("╭╯╰─────╯╰╮", body))
    line((f["left"], body),
         ("│  ", body), (f["eyeL"], f"bold {eyes}"), ("   ", None),
         (f["eyeR"], f"bold {eyes}"), ("  │", body),
         (f["right"], body))
    line(("    ", None), ("│ ", body), ("◦", f"bold {CHEEK}"), ("  ", None),
         (f["mouth"], f"bold {eyes}"), ("  ", None), ("◦", f"bold {CHEEK}"),
         (" │", body))
    line(("    ", None), ("╰─┬─────┬─╯", body))
    t.append("      ", style=None)
    t.append("╰╯", style=body)
    t.append("    ", style=None)
    t.append("╰╯", style=body)
    return t


def quiet(fn, *a):
    """Run a cli command without letting its one-liner break the layout."""
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a)


def elapsed_of(job: dict) -> str:
    segs = [p for p in job.get("segments", []) if os.path.exists(p)]
    secs = sum(os.path.getsize(p) for p in segs) / (2 * 16000)
    return f"{int(secs)//60:02d}:{int(secs)%60:02d}"


def screen(job: dict, frame: int = 0, confirming: bool = False) -> Panel:
    stage = job.get("stage", state.IDLE)
    kind = STAGE_FACE.get(stage, "idle")
    if stage == state.IDLE and job.get("subject"):
        kind = "ready"
    colour, caption = FACES[kind]["body"], FACES[kind]["caption"]

    head = Text(WORDMARK, style=f"bold {colour}")
    head.append(f"\n\n Kispy, {caption}", style="italic grey62")

    top = Table.grid(padding=(0, 2))
    top.add_column(width=32, no_wrap=True)     # fixed, so the creature never shifts
    top.add_column(no_wrap=True)
    top.add_row(head, creature(kind, frame))

    lines = Text()
    if job.get("subject"):
        lines.append(f"\n  {job['subject']}\n", style="bold white")
        bits = [f"Lecture {job['number']}"]
        if job.get("lecturer"):
            bits.append(job["lecturer"])
        if job.get("date"):
            bits.append(job["date"])
        lines.append("  " + "  ·  ".join(bits) + "\n", style="grey62")
    else:
        lines.append("\n  No class in memory.\n", style="grey62")

    lines.append("\n  ")
    lines.append("●" if stage == state.RECORDING else "○", style=colour)
    lines.append(f" {state.HUMAN.get(stage, stage)}", style=f"bold {colour}")
    if stage in (state.RECORDING, state.PAUSED):
        lines.append(f"        {elapsed_of(job)}", style="bold white")
        lines.append(f"   segment {len(job.get('segments', []))}", style="grey62")
    lines.append("\n")

    if stage == state.DONE and job.get("pdf"):
        lines.append(f"\n  {os.path.basename(job['pdf'])}\n", style="spring_green3")
        findings = job.get("findings") or []
        if findings:
            lines.append(f"\n  {len(findings)} note(s) from the proofreading\n", style="gold3")
            for f in findings[:4]:
                lines.append(f"   · {f[:64]}\n", style="grey62")
        else:
            lines.append("  Proofreading: nothing to report.\n", style="grey62")
    if stage == state.FAILED and job.get("error"):
        lines.append(f"\n  {job['error'][:70]}\n", style="red3")
        lines.append("  The audio is kept — press T to try again.\n", style="grey62")
    if stage in (state.TRANSCRIBING, state.SYNTHESISING, state.BUILDING, state.DOCUMENTS):
        lines.append("\n  You can close the lid; it resumes on wake.\n", style="grey62")

    if confirming:
        lines.append("\n  Discard this take? ", style="bold red3")
        lines.append("Y", style="bold reverse red3")
        lines.append(" to confirm, any other key to cancel.\n", style="red3")

    keys = Text("\n  ")
    for key, label, on in (("L", "launch", stage in (state.IDLE, state.PAUSED, state.FAILED)),
                           ("P", "pause", stage == state.RECORDING),
                           ("T", "terminate", stage in (state.RECORDING, state.PAUSED)),
                           ("X", "discard", stage != state.IDLE),
                           ("Q", "quit", True)):
        keys.append(f" {key} ", style=("reverse bold" if on else "dim"))
        keys.append(f" {label}   ", style=("white" if on else "dim"))

    return Panel(Group(top, lines, keys), border_style=colour,
                 title="[grey62]your friend for life[/]", title_align="right", padding=(1, 3))


def key_pressed(timeout: float) -> str | None:
    """Read the raw descriptor, never sys.stdin: a buffered reader drains the fd
    into its own buffer, after which select() reports nothing and every later
    keystroke is silently swallowed."""
    if not sys.stdin.isatty():
        time.sleep(timeout)
        return None
    fd = sys.stdin.fileno()
    if select.select([fd], [], [], timeout)[0]:
        data = os.read(fd, 1)
        return data.decode("utf-8", "ignore").lower() if data else ""
    return None


def run() -> int:
    console = Console()
    if not sys.stdin.isatty():
        console.print(screen(state.load(), 3))
        return 0

    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        frame, confirming = 0, False
        with Live(screen(state.load()), console=console, refresh_per_second=4,
                  screen=True) as live:
            while True:
                frame += 1
                job = state.load()
                stage = job.get("stage", state.IDLE)
                k = key_pressed(0.5)
                if confirming:
                    if k is not None:
                        if k in ("y", "o"):
                            quiet(cli.discard)
                        confirming = False
                    live.update(screen(state.load(), frame, confirming))
                    continue
                if k == "q" or k == "":      # q, or stdin closed under us
                    break
                if k == "l" and stage in (state.IDLE, state.PAUSED, state.FAILED):
                    quiet(cli.start)
                elif k == "p" and stage == state.RECORDING:
                    quiet(cli.pause)
                elif k == "t" and stage in (state.RECORDING, state.PAUSED):
                    quiet(cli.stop)
                elif k == "x" and stage != state.IDLE:
                    confirming = True
                live.update(screen(state.load(), frame, confirming))
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)

    job = state.load()
    if job.get("stage") in (state.RECORDING,) + state.RUNNING_STAGES + (state.DOCUMENTS,):
        console.print("[grey62]Kispy keeps going in the background — [/][bold]kispy[/]"
                      "[grey62] brings you back.[/]")
    return 0
