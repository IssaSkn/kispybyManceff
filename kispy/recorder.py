"""Audio capture. Deliberately dumb: ffmpeg writing a file, nothing else.

No model is loaded and the GPU stays idle while the lecture is on -- transcription
happens afterwards. caffeinate is not optional: a Mac on battery idles to sleep
in a minute or two, which would end the recording in the first five minutes.
"""
from __future__ import annotations
import os, pathlib, re, signal, subprocess, time


def devices() -> list[str]:
    """Audio inputs as macOS currently sees them."""
    r = subprocess.run(["ffmpeg", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
                       capture_output=True, text=True)
    out, seen = [], False
    for line in r.stderr.splitlines():
        if "audio devices" in line:
            seen = True
            continue
        if seen:
            m = re.search(r"\[\d+\]\s+(.*)$", line)
            if not m:
                break
            out.append(m.group(1).strip())
    return out


def resolve(wanted: str) -> str:
    """avfoundation indices shift whenever an iPhone or a headset appears, so the
    device is named, not numbered. Refuse rather than record the wrong microphone."""
    available = devices()
    if not wanted:
        raise RuntimeError("no microphone configured (audio.device) -- run `kispy setup`")
    for name in available:
        if name.lower() == wanted.lower():
            return name
    for name in available:                       # tolerate a partial name
        if wanted.lower() in name.lower():
            return name
    raise RuntimeError(f"microphone \u00ab {wanted} \u00bb not found; available: "
                       + (", ".join(available) or "none"))


def segment_path(work: pathlib.Path, index: int) -> pathlib.Path:
    return work / f"segment-{index:02d}.wav"


def start(work: pathlib.Path, index: int, device: str, rate: int) -> int:
    """Begin a segment, detached from this terminal. Returns the ffmpeg pid."""
    out = segment_path(work, index)
    name = resolve(device)
    cmd = [
        # nohup: a stray SIGHUP leaves ffmpeg alive but silently stops it writing,
        # which would turn a three-hour lecture into an empty file.
        "/usr/bin/nohup",
        "/usr/bin/caffeinate", "-i",          # dies with the child it wraps
        "ffmpeg", "-nostdin", "-loglevel", "error",
        "-f", "avfoundation", "-i", f":{name}",
        "-ar", str(rate), "-ac", "1", "-c:a", "pcm_s16le",
        "-y", str(out),
    ]
    log = open(work / "record.log", "ab")
    proc = subprocess.Popen(cmd, stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                            start_new_session=True)
    return proc.pid


def stop(pid: int, timeout: float = 15.0) -> bool:
    """SIGINT so ffmpeg writes a correct WAV header; escalate only if it hangs.

    The signal goes to the whole process group: the recorded pid is the nohup /
    caffeinate wrapper, and ffmpeg -- the one that must flush the file -- is its
    child. Signalling the wrapper alone leaves ffmpeg running and the recording
    truncated. start_new_session=True makes that pid the group leader.
    """
    if not pid:
        return False
    try:
        os.killpg(os.getpgid(pid), signal.SIGINT)
    except (ProcessLookupError, PermissionError):
        try:
            os.kill(pid, signal.SIGINT)
        except (ProcessLookupError, PermissionError):
            return False
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _group_alive(pid):
            return True
        time.sleep(0.2)
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    return True


def _group_alive(pid: int) -> bool:
    try:
        os.killpg(os.getpgid(pid), 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def concatenate(segments: list[pathlib.Path], out: pathlib.Path) -> pathlib.Path:
    """Join the segments; the breaks between them simply do not exist in the audio."""
    usable = [s for s in segments if s.exists() and s.stat().st_size > 1024]
    if not usable:
        raise RuntimeError("no sound was captured")
    if len(usable) == 1:
        usable[0].replace(out)
        return out
    listing = out.with_suffix(".txt")
    listing.write_text("".join(f"file '{s}'\n" for s in usable), encoding="utf-8")
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y",
                    "-f", "concat", "-safe", "0", "-i", str(listing),
                    "-c", "copy", str(out)], check=True)
    listing.unlink(missing_ok=True)
    for s in usable:
        s.unlink(missing_ok=True)
    return out
