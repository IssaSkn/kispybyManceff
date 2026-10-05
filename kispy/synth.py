"""Turn the session material into the body of a LaTeX document.

Runs through OpenAI Codex CLI by default, using the user's ChatGPT sign-in and
plan allowance without asking Kispy for an API key. If the CLI is unavailable
and OPENAI_API_KEY is set, Kispy can fall back to the OpenAI Responses API.
"""
from __future__ import annotations
import os, pathlib, re, shutil, subprocess, tempfile
from . import config

MAX_DOC_CHARS = 120_000

RULES = """\
You are writing a course document for a Master's student, in English.

Return ONLY the body of a LaTeX document: what sits between \\begin{document} and
\\end{document}. No preamble, no \\documentclass, no \\begin{document}, no code fence.
The title block and table of contents are added for you -- do not write them.

The preamble already defines these, and nothing else:
  \\begin{defbox}{TITLE} ... \\end{defbox}    a definition
  \\begin{keybox}{TITLE} ... \\end{keybox}    an idea to retain
  \\begin{warnbox}{TITLE} ... \\end{warnbox}  a pitfall or a caveat
  \\begin{exbox}{TITLE} ... \\end{exbox}      a worked example
  \\term{...}                                a term being introduced
plus amsmath, booktabs, tabularx, enumitem, hyperref, float and tikz.
Use \\section and \\subsection. Do not invent environments, do not \\usepackage anything.

How to write it:
  - This is a standalone course document. It must read as if written from the
    subject matter itself. Never refer to how the material reached you, to a
    session, a speaker, a slide, or to anything being said, heard or shown.
    No "the lecturer explained", no "as we saw", no "in this session".
    Write in the impersonal register of a textbook chapter.
  - Cover the whole session in depth. This replaces the student's notes, so keep
    the detail, the intermediate steps and the examples. Do not compress it into
    an outline.
  - Reconstruct mathematics properly. The source material renders formulas in
    words ("sigma squared over two"); write them as real LaTeX, in display mode
    when they matter, and define every symbol you introduce.
  - Where the source material describes a diagram -- a curve, a flow, a tree, a
    chart -- redraw it as a simple \\begin{figure}[H] with a tikzpicture and a
    caption. Only from what the material actually describes, never invented, and
    keep it plain: axes, labels, arrows, boxes. Prefer no figure to a wrong one.
  - Where the source is garbled or inaudible, rely on the surrounding argument to
    restore the intended meaning. If something is genuinely unrecoverable, leave
    it out rather than guessing at a fact.
  - Follow the order in which the material was developed; do not reorganise it
    into your own plan.

Begin your reply with a single line:
SUBTITLE: <a short subtitle naming the topics covered, no more than ten words>
then the LaTeX body.
"""


OCR = config.BIN / "kispy-ocr"


READ_PROMPT = """\
Read this file: {path}

These are course pages the student added themselves: handwritten notes, a
photocopy or a screenshot. Transcribe them faithfully, page by page.

  - Formulas in LaTeX, as exactly as you can read them.
  - Where a page carries a diagram or a sketch, do not skip it: describe it
    precisely enough to be redrawn, on a line starting with [FIGURE] -- what the
    axes are, what is plotted, the labels, the arrows, the boxes and their order.
  - Invent nothing, complete nothing, correct nothing. If a passage is illegible,
    write [illegible] rather than guessing.
  - No commentary of your own: only what is on the pages.
"""


def _codex_cmd(model: str, sandbox: str = "read-only") -> list[str]:
    cmd = [
        "codex", "exec", "--ephemeral", "--skip-git-repo-check",
        "--color", "never", "--sandbox", sandbox,
    ]
    if model:
        cmd += ["--model", model]
    cmd.append("-")
    return cmd


def _codex_error(r: subprocess.CompletedProcess, out: str) -> RuntimeError:
    blob = ((r.stderr or "") + "\n" + out).lower()
    if any(x in blob for x in ("not logged in", "login required", "codex login", "sign in")):
        return RuntimeError("codex-cli-not-logged-in")
    return RuntimeError(f"codex-cli-failed: {(r.stderr or out)[:400]}")


def via_files_cli(prompt: str, model: str, timeout: int,
                  files: list[pathlib.Path]) -> str:
    """Run Codex on user-supplied documents in an isolated temporary workspace.

    Files are copied into the workspace first. Images are also attached with
    --image; PDFs and text documents can be inspected with read-only commands
    while any conversion scratch files stay inside the temporary directory.
    """
    with tempfile.TemporaryDirectory() as sandbox:
        root = pathlib.Path(sandbox)
        staged: list[pathlib.Path] = []
        for i, src in enumerate(files):
            dest = root / f"{i + 1:02d}-{src.name}"
            shutil.copy2(src, dest)
            staged.append(dest)

        names = "\n".join(f"- {p.name}" for p in staged)
        full_prompt = prompt + (
            "\n\nThe files available in the current working directory are:\n" + names
            if staged else ""
        )
        cmd = _codex_cmd(model, "workspace-write")
        image_suffixes = {".png", ".jpg", ".jpeg", ".heic", ".webp"}
        for p in staged:
            if p.suffix.lower() in image_suffixes:
                cmd[2:2] = ["--image", str(p)]
        try:
            r = subprocess.run(cmd, input=full_prompt, capture_output=True, text=True,
                               cwd=root, timeout=timeout)
        except FileNotFoundError:
            raise RuntimeError("codex-cli-not-installed")
    out = (r.stdout or "").strip()
    if r.returncode != 0 or not out:
        raise _codex_error(r, out)
    return out


def read_by_model(path: pathlib.Path, cfg: dict) -> str:
    """Hand a scan to Codex rather than relying only on shape-based OCR."""
    try:
        return via_files_cli(
            READ_PROMPT.format(path=path.name),
            cfg["documents"].get("model", ""),
            cfg["documents"].get("timeout_seconds", 900),
            [path],
        )
    except Exception:
        return ocr(path)


def ocr(path: pathlib.Path) -> str:
    """Fallback: macOS Vision. Offline and instant, but it only recognises shapes."""
    if not OCR.exists():
        return ""
    try:
        r = subprocess.run([str(OCR), str(path)], capture_output=True, text=True, timeout=300)
        return r.stdout.strip()
    except Exception:
        return ""


def _docx_images(path: pathlib.Path) -> int:
    """How many pictures a .docx carries. python-docx reads text only, so a
    classmate's notes full of hand-drawn figures arrive here as prose with holes;
    saying so is better than letting it pass unnoticed."""
    try:
        import zipfile
        with zipfile.ZipFile(path) as z:
            return sum(1 for n in z.namelist()
                       if n.startswith("word/media/")
                       and n.lower().endswith((".png", ".jpg", ".jpeg", ".emf", ".gif")))
    except Exception:
        return 0


def extract(path: pathlib.Path, cfg: dict | None = None) -> tuple[str, bool]:
    """Returns the text and whether it was read off an image (hence approximate)."""
    suffix = path.suffix.lower()
    try:
        if suffix == ".pdf":
            import pypdf
            r = pypdf.PdfReader(str(path))
            text = "\n".join((p.extract_text() or "") for p in r.pages)
            if len(text.split()) < 20:                 # a scan, or an image-only export
                seen = read_by_model(path, cfg) if cfg else ocr(path)
                if len(seen.split()) > len(text.split()):
                    return seen, True
            return text, False
        if suffix in {".png", ".jpg", ".jpeg", ".heic", ".webp"}:
            return (read_by_model(path, cfg) if cfg else ocr(path)), True
        if suffix == ".pptx":
            from pptx import Presentation
            out = []
            for i, slide in enumerate(Presentation(str(path)).slides, 1):
                bits = [sh.text for sh in slide.shapes if getattr(sh, "has_text_frame", False)]
                if bits:
                    out.append(f"--- slide {i} ---\n" + "\n".join(bits))
            return "\n".join(out), False
        if suffix == ".docx":
            import docx
            return "\n".join(p.text for p in docx.Document(str(path)).paragraphs), False
        if suffix in {".txt", ".md", ".tex", ".csv"}:
            return path.read_text(encoding="utf-8", errors="replace"), False
    except Exception:
        return "", False
    return "", False


def gather(folder: pathlib.Path, cfg: dict | None = None) -> tuple[list[tuple[str, str, bool]], list[str]]:
    """Everything in the session folder that is not our own output, plus notes on
    what could not be read -- a silent skip loses your material."""
    out, notes = [], []
    for p in sorted(folder.iterdir()):
        if not p.is_file() or p.name.startswith((".", "~$")) or p.suffix.lower() in {".tex", ".pdf"} \
                and p.stem.startswith(folder.parent.name):   # our own output, never a source
            continue
        text, scanned = extract(p, cfg)
        text = text.strip()
        if text:
            out.append((p.name, text[:MAX_DOC_CHARS], scanned))
            if p.suffix.lower() == ".docx":
                n = _docx_images(p)
                if n:
                    notes.append(f"{p.name}: {n} embedded image(s) were not read "
                                 "(only the text of a .docx is extracted)")
        else:
            notes.append(f"unreadable document, ignored: {p.name}")
    return out, notes


def prompt_for(subject: str, number: int, lecturer: str,
               transcript: str, documents: list[tuple[str, str, bool]]) -> str:
    parts = [RULES, f"\nCourse: {subject}", f"Lecture number: {number}"]
    if lecturer:
        parts.append(f"Taught by: {lecturer}")
    for name, text, scanned in documents:
        if scanned:
            parts.append(
                f"\n=== Pages read off the image: {name} ===\n"
                "Read off the page rather than from a text layer, so it is approximate: "
                "symbols may be misread and passages marked [illegible]. Use it to see "
                "which topics were covered and how they were developed, but never state "
                "a fact that rests on it alone. Lines marked [FIGURE] describe a diagram "
                "drawn on the page: redraw it.\n" + text)
        else:
            parts.append(f"\n=== Supporting material: {name} ===\n{text}")
    parts.append(f"\n=== Session material ===\n{transcript}")
    return "\n".join(parts)


def _split(reply: str) -> tuple[str, str]:
    m = re.match(r"\s*SUBTITLE\s*:\s*(.+)", reply)
    if m:
        return m.group(1).strip().rstrip("."), reply[m.end():].lstrip()
    return "", reply.strip()


def via_cli(prompt: str, model: str, timeout: int) -> str:
    """Run a text-only Codex turn with no writable project workspace."""
    with tempfile.TemporaryDirectory() as sandbox:
        try:
            r = subprocess.run(
                _codex_cmd(model, "read-only"),
                input=prompt, capture_output=True, text=True,
                cwd=sandbox, timeout=timeout,
            )
        except FileNotFoundError:
            raise RuntimeError("codex-cli-not-installed")
    out = (r.stdout or "").strip()
    if r.returncode != 0 or not out:
        raise _codex_error(r, out)
    return out


def via_api(prompt: str, model: str) -> str:
    from openai import OpenAI
    client = OpenAI()
    response = client.responses.create(
        model=model or "gpt-5.6",
        reasoning={"effort": "high"},
        max_output_tokens=64000,
        input=prompt,
    )
    out = (response.output_text or "").strip()
    if not out:
        raise RuntimeError("the model returned no text")
    return out


def generate(prompt: str, cfg: dict) -> tuple[str, str]:
    s = cfg["synthesis"]
    if s["backend"] == "codex_cli":
        try:
            return _split(via_cli(prompt, s.get("model", ""), s["timeout_seconds"]))
        except RuntimeError:
            if not os.environ.get("OPENAI_API_KEY"):
                raise
    return _split(via_api(prompt, s.get("model", "")))
