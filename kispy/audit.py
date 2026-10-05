"""Checks run on the finished document.

Two kinds, and the distinction matters. The structural and typographic checks are
deterministic: they are free, instant, and never wrong. The coverage check asks a
model what the document left out -- the one job where a second pass genuinely
adds something, because it compares two artefacts instead of re-deriving the same
answer with the same information.

Nothing here rewrites the document. Findings are reported and that is all.
"""
from __future__ import annotations
import re

BOXES = ("defbox", "keybox", "warnbox", "exbox")
DROPPED = {"\u2014": "em dash", "\u2013": "en dash", "\u2026": "ellipsis",
           "\u2018": "opening quote", "\u2019": "closing quote",
           "\u201c": "opening double quote", "\u201d": "closing double quote",
           "\u2212": "minus sign"}


def structure(tex: str) -> list[str]:
    """Things that are wrong regardless of what the lecture was about."""
    out = []
    body = tex.split("\\begin{document}", 1)[-1]

    for env in BOXES + ("equation", "align", "itemize", "enumerate", "tabular", "table"):
        opens = len(re.findall(rf"\\begin\{{{env}\}}", body))
        closes = len(re.findall(rf"\\end\{{{env}\}}", body))
        if opens != closes:
            out.append(f"unbalanced « {env} »: {opens} opened, {closes} closed")

    for env in BOXES:
        for m in re.finditer(rf"\\begin{{{env}}}{{([^}}]*)}}(.*?)\\end{{{env}}}", body, re.S):
            if not m.group(2).strip():
                out.append(f"empty box « {m.group(1)[:40]} »")
            if not m.group(1).strip():
                out.append(f"{env} with no title")

    if body.count("$") % 2:
        out.append("odd number of $: an inline formula is left open")

    found = {c for c in DROPPED if c in body}
    if found:
        names = ", ".join(DROPPED[c] for c in sorted(found))
        out.append(f"characters XeTeX drops silently: {names}")

    sections = re.split(r"\\section\{", body)[1:]
    for s in sections:
        title = s.split("}", 1)[0]
        text = re.sub(r"\\[a-zA-Z]+\*?(\[[^\]]*\])?(\{[^}]*\})?", " ", s.split("}", 1)[-1])
        if len(text.split()) < 40:
            out.append(f"section « {title[:40]} » is nearly empty ({len(text.split())} words)")
    return out


def typography(log: str, threshold_pt: float = 5.0) -> list[str]:
    """What the LaTeX engine already noticed and we would otherwise throw away."""
    out = []
    over = [float(m.group(1)) for m in re.finditer(r"Overfull \\hbox \(([\d.]+)pt", log)]
    bad = [p for p in over if p >= threshold_pt]
    if bad:
        out.append(f"{len(bad)} line(s) run into the margin (up to {max(bad):.0f} pt) "
                   "— usually a URL or an over-wide table")
    if re.search(r"Missing character", log):
        out.append("some characters do not exist in the font and were not printed")
    for m in re.finditer(r"LaTeX Warning: (Reference|Citation) `([^']+)' .* undefined", log):
        out.append(f"unresolved reference: {m.group(2)}")
    return out


def density(tex: str, transcript_words: int, pages: int) -> list[str]:
    """A document far shorter than the session it covers has skipped something."""
    out = []
    body = tex.split("\\begin{document}", 1)[-1]
    words = len(re.sub(r"\\[a-zA-Z]+\*?(\[[^\]]*\])?(\{[^}]*\})?", " ", body).split())
    if transcript_words > 2000 and words < transcript_words * 0.25:
        out.append(f"short for the session: {words} words written "
                   f"for {transcript_words} words of material")
    if pages and transcript_words > 8000 and pages < 8:
        out.append(f"only {pages} pages for a session of this length")
    return out


COVERAGE_PROMPT = """\
Below is the material of one teaching session, then the document written from it.

Your only task: list what the material contained and the document left out — a
notion that was developed, a derivation, a worked example, a warning. Do not judge
the style or the layout. Do not rewrite anything.

Absolute rule: every line must quote an EXACT fragment of the material, copied word
for word, in quotation marks. If you cannot quote it, it is not an omission: say
nothing about it. Never report what a good document ought to contain in general —
only what was actually there and has disappeared.

Ignore what is legitimately absent: digressions, course admin, remarks about the
exam, small talk.

Format, at most five lines:
- "exact fragment copied from the material" -> what is missing from the document

If the document covers the essentials, reply exactly: OK
"""


def _grounded(line: str, transcript: str) -> bool:
    """Keep a finding only if its quotation really appears in the material."""
    m = re.search(r"[«\"']([^»\"']{12,})[»\"']", line)
    if not m:
        return False
    quote = re.sub(r"\s+", " ", m.group(1)).strip().lower()
    hay = re.sub(r"\s+", " ", transcript).lower()
    if quote in hay:
        return True
    words = quote.split()                       # tolerate a slightly loose copy
    return len(words) >= 6 and " ".join(words[:6]) in hay


def coverage(transcript: str, tex: str, cfg: dict) -> list[str]:
    from . import synth
    body = tex.split("\\begin{document}", 1)[-1]
    prompt = (f"{COVERAGE_PROMPT}\n=== Session material ===\n{transcript}"
              f"\n\n=== Document produced ===\n{body}")
    model = cfg["audit"].get("model", "claude-haiku-4-5")
    try:
        reply = synth.via_cli(prompt, model, cfg["audit"].get("timeout_seconds", 900))
    except Exception as exc:
        return [f"coverage check unavailable ({str(exc)[:60]})"]
    if reply.strip().upper().startswith(("OK", "RAS")):
        return []
    lines = [l.strip(" -•") for l in reply.splitlines() if l.strip().startswith(("-", "•"))]
    return [l for l in lines if _grounded(l, transcript)][:5]
