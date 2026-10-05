"""Terminal prompts shared by the setup wizard and the timetable importer.

Kispy is silent in normal use; these are only for the moments where it has to
ask a person something.
"""
from __future__ import annotations
import sys, textwrap
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

console = Console()

OK, BAD, WARN, DIM = "✓", "✗", "!", "grey62"


def heading(step: str, title: str) -> None:
    console.print()
    console.print(Text(f"  {step}  ", style="bold reverse deep_sky_blue1")
                  + Text(f"  {title}", style="bold"))
    console.print()


def _wrap(text: str, indent: int) -> str:
    """Rich wraps to the console width but not to our indent, which leaves a
    continuation line hanging against the left edge."""
    width = max(40, min(96, console.width) - indent - 2)
    pad = " " * indent
    return "\n".join(textwrap.fill(line, width, initial_indent=pad,
                                   subsequent_indent=pad, break_long_words=False,
                                   break_on_hyphens=False) if line.strip() else ""
                     for line in text.splitlines())


def say(text: str, style: str = "") -> None:
    console.print(_wrap(text, 2), style=style or None, highlight=False)


def good(text: str) -> None:
    console.print(f"  [green]{OK}[/] {text}", highlight=False)


def bad(text: str) -> None:
    console.print(f"  [red]{BAD}[/] {text}", highlight=False)


def warn(text: str) -> None:
    console.print(f"  [yellow]{WARN}[/] {text}", highlight=False)


def note(text: str) -> None:
    console.print(f"[grey62]{_wrap(text, 4)}[/]", highlight=False)


def panel(body: str, title: str = "", style: str = "deep_sky_blue1") -> None:
    console.print(Panel(body, title=title, border_style=style, padding=(1, 3)))


def ask(question: str, default: str = "", secret: bool = False) -> str:
    suffix = f" [grey62]({default})[/]" if default else ""
    try:
        console.print(f"  [bold]{question}[/]{suffix}", end=" ", highlight=False)
        answer = input().strip()
    except (EOFError, KeyboardInterrupt):
        console.print()
        raise SystemExit(130)
    return answer or default


def confirm(question: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    while True:
        a = ask(f"{question} [grey62][{hint}][/]").lower()
        if not a:
            return default
        if a in ("y", "yes", "o", "oui"):
            return True
        if a in ("n", "no", "non"):
            return False


def choose(question: str, options: list[tuple[str, str]], default: int = 1) -> int:
    """options: (label, one-line explanation). Returns a 1-based index."""
    console.print(f"  [bold]{question}[/]")
    console.print()
    for i, (label, detail) in enumerate(options, 1):
        console.print(f"    [bold deep_sky_blue1]{i}[/]  {label}", highlight=False)
        if detail:
            console.print(f"       [grey62]{detail}[/]", highlight=False)
    console.print()
    while True:
        a = ask("Your choice", str(default))
        if a.isdigit() and 1 <= int(a) <= len(options):
            return int(a)


def pick_many(question: str, items: list[str], preselected: list[str] | None = None) -> list[str]:
    """Tick several from a list: '1 3 4', 'all', or empty for none."""
    chosen = set(preselected or [])
    console.print(f"  [bold]{question}[/]")
    console.print()
    for i, it in enumerate(items, 1):
        mark = "[green]●[/]" if it in chosen else "[grey35]○[/]"
        console.print(f"    {mark} [bold deep_sky_blue1]{i:>2}[/]  {it}", highlight=False)
    console.print()
    note("numbers separated by spaces, 'all', or empty for none")
    a = ask("Your choice", " ".join(str(i) for i, it in enumerate(items, 1) if it in chosen))
    if a.lower() in ("all", "tout", "*"):
        return list(items)
    out = []
    for tok in a.replace(",", " ").split():
        if tok.isdigit() and 1 <= int(tok) <= len(items):
            out.append(items[int(tok) - 1])
    return out


def is_tty() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()
