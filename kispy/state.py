"""The job's memory. One file on disk, so the work survives a lid closing."""
from __future__ import annotations
import datetime, json, os, pathlib, tempfile
from . import config

IDLE, RECORDING, PAUSED = "idle", "recording", "paused"
DOCUMENTS, TRANSCRIBING, SYNTHESISING, BUILDING = "documents", "transcribing", "synthesising", "building"
DONE, FAILED = "done", "failed"

RUNNING_STAGES = (TRANSCRIBING, SYNTHESISING, BUILDING)
HUMAN = {
    IDLE: "nothing running", RECORDING: "recording", PAUSED: "paused",
    DOCUMENTS: "waiting for your documents", TRANSCRIBING: "transcribing",
    SYNTHESISING: "writing the document", BUILDING: "compiling the PDF",
    DONE: "done", FAILED: "stopped on an error",
}


def _path() -> pathlib.Path:
    return config.state_dir() / "job.json"


def load() -> dict:
    p = _path()
    if not p.exists():
        return {"stage": IDLE}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"stage": IDLE}


def save(job: dict) -> dict:
    """Atomic: a job file half-written by a sleeping Mac would be worse than none."""
    job["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    p = _path()
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(job, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, p)
    return job


def set_stage(stage: str, **fields) -> dict:
    job = load()
    job["stage"] = stage
    job.update(fields)
    return save(job)


def update_fields(expect_stage: str, **fields) -> bool:
    """Write fields without touching the stage, and only while the job is still in
    the stage the caller saw. The watchdog must never overwrite a stage the
    pipeline has moved on from -- doing so once cost a whole lecture."""
    job = load()
    if job.get("stage") != expect_stage:
        return False
    job.update(fields)
    save(job)
    return True


def clear() -> None:
    _path().unlink(missing_ok=True)


def alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False
