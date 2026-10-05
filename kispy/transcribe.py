"""Speech to text, after the lecture, with whisper.cpp."""
from __future__ import annotations
import pathlib, re, shutil, subprocess
from . import config

GLOSSARY_DIR = config.CONFIG_DIR / "glossary"


def whisper_cli() -> pathlib.Path:
    """Wherever the installer put it, or whatever is on the PATH."""
    for p in (config.SHARE / "whisper.cpp/build/bin/whisper-cli",
              pathlib.Path.home() / ".local/src/whisper.cpp/build/bin/whisper-cli"):
        if p.exists():
            return p
    found = shutil.which("whisper-cli")
    if found:
        return pathlib.Path(found)
    raise RuntimeError("whisper-cli not found -- re-run install.sh")


def glossary(subject: str) -> str:
    """Names, acronyms and jargon for this course. This is where mishearings are
    actually prevented -- correcting them downstream is guesswork.

    Drop a file in ~/.config/kispy/glossary/: _global.txt applies everywhere,
    "<course name>.txt" only to that course.
    """
    parts = []
    for p in (GLOSSARY_DIR / "_global.txt", GLOSSARY_DIR / f"{subject}.txt"):
        if p.exists():
            parts.append(p.read_text(encoding="utf-8"))
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def run(wav: pathlib.Path, subject: str, cfg: dict, out_stem: pathlib.Path) -> str:
    t = cfg["transcribe"]
    prompt = glossary(subject)
    cmd = [
        str(whisper_cli()), "-m", t["model"], "-f", str(wav),
        "-l", t["language"], "-t", str(t["threads"]),
        "-bs", "5", "-bo", "5", "-mc", "64",
        "-otxt", "-of", str(out_stem), "-nt",
    ]
    if pathlib.Path(t.get("vad_model", "")).exists():
        cmd += ["--vad", "-vm", t["vad_model"], "-vmsd", "30", "-vsd", "200"]
    if prompt:
        cmd += ["--prompt", prompt, "--carry-initial-prompt"]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    text = out_stem.with_suffix(".txt").read_text(encoding="utf-8")
    return re.sub(r"[ \t]+", " ", text).strip()
