"""What happens after the lecture. Every stage checks whether its own output
already exists, so a job interrupted by a closing lid resumes where it stopped
rather than starting over."""
from __future__ import annotations
import datetime, pathlib, subprocess, traceback
from . import audit, build, config, recorder, schedule, shred, state, synth, transcribe


def ask(question: str, buttons: tuple[str, str], default: str, timeout: int = 3600) -> str:
    """A macOS dialog. If the Mac is asleep this simply waits until it is not."""
    b = '", "'.join(buttons)
    script = (f'display dialog {question!r} buttons {{"{b}"}} default button "{default}" '
              f'with title "Kispy" giving up after {timeout}')
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if r.returncode != 0 or "gave up:true" in r.stdout:
        return buttons[0]
    m = [p for p in r.stdout.split(", ") if p.startswith("button returned:")]
    return m[0].split(":", 1)[1].strip() if m else buttons[0]


def notify(title: str, text: str, subtitle: str = "") -> None:
    sub = f' subtitle "{subtitle}"' if subtitle else ""
    subprocess.run(["osascript", "-e",
                    f'display notification "{text}" with title "{title}"{sub}'], check=False)


def session_folder(slot: schedule.Slot, cfg: dict) -> pathlib.Path:
    subject_dir = schedule.folder_for(slot.subject, pathlib.Path(cfg["output"]["root"]), learn=True)
    name = f"{slot.start:%Y-%m-%d} — Lecture {slot.number}"     # date first: Finder sorts by it
    folder = subject_dir / name
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def run(job: dict) -> None:
    cfg = config.load()
    work = config.work_dir()
    folder = pathlib.Path(job["folder"])
    stem = f"{folder.parent.name} — Lecture {job['number']}"
    wav = work / "lecture.wav"
    transcript_path = work / "transcript.txt"

    try:
        # ---- one audio file out of the segments -------------------------------
        if not wav.exists():
            recorder.concatenate([pathlib.Path(p) for p in job.get("segments", [])], wav)

        # ---- the student's own material ---------------------------------------
        if job["stage"] == state.DOCUMENTS:
            answer = ask("Anything of your own to add — slides, notes, a photo of the board?",
                         ("No", "Yes"), "Yes")
            if answer == "Yes":
                subprocess.run(["open", str(folder)], check=False)
                ask("Drop your files in the folder, then continue.",
                    ("Continue", "Continue"), "Continue")
            state.set_stage(state.TRANSCRIBING)

        # ---- speech to text ----------------------------------------------------
        if not transcript_path.exists():
            state.set_stage(state.TRANSCRIBING)
            text = transcribe.run(wav, job["subject"], cfg, work / "transcript")
            transcript_path.write_text(text, encoding="utf-8")
        transcript = transcript_path.read_text(encoding="utf-8")
        if len(transcript.split()) < 50:
            raise RuntimeError("almost nothing was captured — check the microphone")

        # ---- the document ------------------------------------------------------
        state.set_stage(state.SYNTHESISING)
        documents, notes = synth.gather(folder, cfg)
        prompt = synth.prompt_for(job["subject"], job["number"], job.get("lecturer", ""),
                                  transcript, documents)
        subtitle, body = synth.generate(prompt, cfg)

        leaked = build.leaks(body)
        if leaked:
            subtitle, body = synth.generate(
                prompt + "\n\nYour previous attempt referred to how the material was "
                f"obtained (it used: {', '.join(leaked)}). Rewrite it so no such word "
                "appears anywhere: the document must stand on its own.", cfg)

        # ---- compile -----------------------------------------------------------
        state.set_stage(state.BUILDING)
        year = cfg["output"]["year"] or build.academic_label(start_month=schedule.term_start_month())
        header = build.title_block(job["subject"], job["number"], job.get("lecturer", ""),
                                   subtitle, cfg["output"]["program"], year)
        tex = build.assemble(body, header, folder / f"{stem}.tex")
        pdf, errors, log = build.compile_pdf(tex, folder)
        if pdf is None:
            subtitle, body = synth.generate(
                prompt + "\n\nYour previous body failed to compile. Fix it and return the "
                f"corrected body only.\n\n{errors}", cfg)
            tex = build.assemble(body, header, folder / f"{stem}.tex")
            pdf, errors, log = build.compile_pdf(tex, folder)
        if pdf is None:
            raise RuntimeError(f"the document does not compile:\n{errors}")

        # ---- proofreading: report, never rewrite --------------------------------
        findings = list(notes)
        if cfg["audit"].get("enabled", True):
            tex_text = tex.read_text(encoding="utf-8")
            try:
                import pypdf
                pages = len(pypdf.PdfReader(str(pdf)).pages)
            except Exception:
                pages = 0
            findings += (audit.structure(tex_text) + audit.typography(log)
                         + audit.density(tex_text, len(transcript.split()), pages))
            if cfg["audit"].get("coverage", True):
                findings += audit.coverage(transcript, tex_text, cfg)

        # ---- nothing of the session survives ------------------------------------
        shred.clean(work)
        state.set_stage(state.DONE, pdf=str(pdf), findings=findings,
                        finished_at=datetime.datetime.now().isoformat(timespec="seconds"))
        notify("Kispy", f"Lecture {job['number']} · {folder.parent.name}", "Document ready")

    except Exception as exc:                       # the audio is kept, always
        state.set_stage(state.FAILED, error=f"{exc}", trace=traceback.format_exc()[-2000:])
        # say so now: a silent failure is only discovered hours later, too late to redo
        notify("Kispy failed", str(exc)[:90].replace('"', "'"), "The audio is kept")
