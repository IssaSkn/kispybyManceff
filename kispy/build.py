"""Assemble the document and compile it. The preamble is fixed and known-good,
so the model only ever writes the body -- which removes most compile failures."""
from __future__ import annotations
import datetime, pathlib, re, subprocess
from . import config

STYLE = config.LIB / "style/preamble.tex"

# XeTeX (what tectonic runs) silently drops these; LaTeX spellings survive.
PUNCTUATION = {
    "\u2014": "---", "\u2013": "--", "\u2026": "\\ldots{}",
    "\u2018": "`", "\u2019": "'", "\u201c": "``", "\u201d": "''",
    "\u00a0": "~", "\u2212": "$-$",
}
SMALL_WORDS = {"and", "or", "of", "in", "on", "the", "a", "an", "to", "for", "with", "at", "by"}


def latexise(text: str) -> str:
    for bad, good in PUNCTUATION.items():
        text = text.replace(bad, good)
    return text


def title_case(subject: str) -> str:
    words = subject.split()
    return " ".join(w if i and w.lower() in SMALL_WORDS else
                    (w if w[:1].isupper() else w.capitalize())
                    for i, w in enumerate(words))


def academic_label(when: datetime.date | None = None, start_month: int = 9) -> str:
    d = when or datetime.date.today()
    y = d.year if d.month >= start_month else d.year - 1
    return f"{y}-{y + 1}"

# Nothing in the finished document may hint at how it was produced. These are
# phrases, not bare words: "he recorded the outcomes of a roulette wheel" is
# ordinary statistics prose, and flagging it once cost a pointless regeneration.
FORBIDDEN = re.compile(
    r"\b(?:"
    r"(?:this|the|that|our)\s+(?:recording|transcript|audio\s+(?:file|track|recording))"
    r"|transcript(?:ion)?s?\b"
    r"|was\s+recorded|recording\s+of\s+(?:this|the)"
    r"|(?:the|our)\s+(?:lecturer|speaker|professor)\s+(?:said|says|explained|mentioned|noted|told)"
    r"|as\s+(?:we\s+)?(?:heard|were\s+told)"
    r"|in\s+(?:this|today.s)\s+(?:session|lecture|class|recording)"
    r"|during\s+(?:the|this)\s+(?:session|lecture|class)"
    r"|microphone|whisper\.cpp|kispy"
    r")\b", re.I)


def title_block(subject: str, number: int, lecturer: str, subtitle: str,
                program: str, year: str) -> str:
    subject, lecturer = title_case(subject), latexise(lecturer)
    subtitle, program, year = latexise(subtitle), latexise(program), latexise(year)
    who = f"Course notes of Prof.~{lecturer}" if lecturer else "Course notes"
    sub = f"\\\\[3pt] {subtitle}" if subtitle else ""
    strap = " --- ".join(x for x in (program, year) if x.strip())
    head = (f"{{\\color{{grayish}}\\small\\sffamily {strap}}}\\\\[10pt]\n" if strap else "")
    return (
        f"\\renewcommand{{\\kispyrunning}}{{{subject} --- Lecture {number}}}\n"
        "\\begin{center}\n"
        f"{head}"
        f"{{\\color{{slate}}\\Huge\\bfseries {subject}}}\\\\[14pt]\n"
        f"{{\\color{{ink}}\\Large Lecture {number}{sub}}}\\\\[16pt]\n"
        "{\\color{rule}\\rule{0.5\\textwidth}{0.8pt}}\\\\[12pt]\n"
        f"{{\\small\\color{{grayish}}{who}}}\n"
        "\\end{center}\n\\vspace{6pt}\n\n"
        "\\setcounter{tocdepth}{2}\n\\tableofcontents\n"
        "\\vspace{6pt}\n{\\color{slate}\\hrule height 0.8pt}\n\\clearpage\n"
    )


def leaks(body: str) -> list[str]:
    """Words that would betray where the material came from."""
    return sorted({m.group(0) for m in FORBIDDEN.finditer(body)})


def assemble(body: str, header: str, tex_path: pathlib.Path) -> pathlib.Path:
    preamble = STYLE.read_text(encoding="utf-8")
    body = body.strip()
    # the model is asked for a body, but sometimes returns a whole file anyway
    if "\\begin{document}" in body:
        body = body.split("\\begin{document}", 1)[1]
    body = body.split("\\end{document}")[0].strip()
    body = re.sub(r"^```(?:latex|tex)?\s*|\s*```$", "", body).strip()
    body = latexise(body)
    tex_path.write_text(
        f"{preamble}\n\\begin{{document}}\n\n{header}\n{body}\n\n\\end{{document}}\n",
        encoding="utf-8")
    return tex_path


def compile_pdf(tex: pathlib.Path, outdir: pathlib.Path) -> tuple[pathlib.Path | None, str, str]:
    r = subprocess.run(
        ["tectonic", "-X", "compile", str(tex), "--outdir", str(outdir), "--keep-logs"],
        capture_output=True, text=True)
    pdf = outdir / (tex.stem + ".pdf")
    logfile = outdir / (tex.stem + ".log")
    log = logfile.read_text(errors="replace") if logfile.exists() else ""
    logfile.unlink(missing_ok=True)                 # the folder keeps only .tex and .pdf
    if r.returncode == 0 and pdf.exists():
        return pdf, "", log
    errors = "\n".join(l for l in r.stderr.splitlines()
                       if re.search(r"error|undefined|missing|runaway", l, re.I))
    return None, (errors or r.stderr)[-4000:], log
