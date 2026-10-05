# How Kispy works

The whole thing is about 2 500 lines of Python, two short Swift files and a LaTeX
preamble. This page is for when you want to change something, or when you want to
know what is actually running on your machine before you trust it with your
lectures.

## Where everything lives

```
~/.local/lib/kispy/           the program and its private virtualenv
         kispy/               the Python package
         style/preamble.tex   the house style of the PDF
         .venv/

~/.local/bin/kispy            the command
             kispy-watch      the watchdog loop
             kispy-cal        Swift, reads the Calendar app (EventKit)
             kispy-ocr        Swift, offline OCR (Vision)

~/.local/share/kispy/         whisper.cpp and the speech models
~/.local/state/kispy/         job.json, handled.json, work/ — transient, wiped
~/.config/kispy/              config.toml, timetable.json, folders.toml, glossary/

~/Library/LaunchAgents/com.kispy.watch.plist
```

Nothing else is written anywhere, except the `.tex` and `.pdf` in your course
folder.

## The modules

| file | what it is responsible for |
|---|---|
| `cli.py` | Every command. Also `autostart`, `autostop` and `watch_capture`, which the watchdog calls. |
| `wizard.py` | `kispy setup`, and installing or removing the launchd agent. |
| `schedule.py` | Reading a timetable from any of the three sources, merging spellings, numbering lectures, and deciding which folder a subject belongs to. |
| `timetable.py` | Turning a screenshot, a PDF or three typed lines into `timetable.json`. |
| `recorder.py` | ffmpeg. Device resolution by name, starting and stopping cleanly, joining segments. |
| `state.py` | One JSON file, written atomically. The job's entire memory. |
| `pipeline.py` | What happens after the lecture, stage by stage, resumably. |
| `transcribe.py` | whisper.cpp, and the per-course glossary. |
| `synth.py` | Reading your documents, building the prompt, calling Claude. |
| `build.py` | Assembling the `.tex` and compiling it with tectonic. |
| `audit.py` | The checks run on the finished document. |
| `shred.py` | Overwriting and unlinking the working material. |
| `tui.py` | The app, and the creature. |

## The state machine

```
idle ──start──▶ recording ⇄ paused ──stop──▶ documents ──▶ transcribing
                                                              │
                              done ◀── building ◀── synthesising
                                 ▲                      │
                                 └──── failed ◀─────────┘
```

The stage lives in `~/.local/state/kispy/job.json`, written through a temporary
file and `os.replace`, so a Mac that sleeps mid-write never leaves half a job
behind. Every stage after `documents` checks whether its own output already exists
before doing the work, which is what makes the pipeline resumable: close the lid
during transcription, open it at home, and it carries on from the transcript.

`handled.json` is a short ledger of sessions already recorded, stopped or
discarded, so the watchdog never starts the same lecture twice.

## Things that are the way they are for a reason

**The microphone is chosen by name.** AVFoundation numbers its inputs, and the
numbers move: plug in headphones, walk past with an iPhone, and index 1 is no
longer your Mac. Kispy stores the name and refuses to start if that exact input is
absent, rather than silently recording something else.

**`nohup` and a process group.** The recorder runs
`nohup caffeinate -i ffmpeg …` detached in a new session. `caffeinate` keeps the
Mac from idling to sleep and dies with its child. `nohup` matters because a stray
`SIGHUP` — closing the terminal that started it — leaves ffmpeg alive but silently
stops it writing, which turns a three-hour lecture into an empty file. Stopping
sends `SIGINT` to the whole **process group**, not to the recorded pid: that pid is
the wrapper, and ffmpeg, the process that must flush the WAV header, is its child.

**The watchdog loops itself.** launchd throttles jobs that exit in under ten
seconds, and `StartInterval` never fired reliably for a job this short. So the
agent runs `kispy-watch`, a `while :; do kispy tick; sleep 60; done` loop, under
`KeepAlive`.

**`watch_capture` writes with `update_fields`, never `set_stage`.** The watchdog
and the pipeline both touch `job.json`. An early version had the watchdog write
the stage back as `recording` while the pipeline had already moved to
`transcribing` — which relaunched a capture over the workspace and cost a whole
lecture. `update_fields` writes only if the stage is still what the caller saw,
and the watchdog also steps aside entirely when it sees the pipeline process
running.

**The preamble is frozen.** The model is asked for the *body* only — what sits
between `\begin{document}` and `\end{document}` — and is told exactly which
environments exist. Most LaTeX failures from a model come from inventing a package
or an environment. If the body still fails to compile, the error goes back once
with a request to fix it.

**Unicode punctuation is rewritten before compiling.** XeTeX, which tectonic runs,
drops em dashes, curly quotes and the minus sign silently — the character simply
does not appear in the PDF. `latexise()` replaces them with LaTeX spellings, and
the audit checks for any that survived.

**The leak filter matches phrases, not words.** Nothing in the document may hint at
a recording. An early version flagged the word "recorded" and regenerated a
perfectly good document because it contained *"he recorded the outcomes of a
roulette wheel"*. The patterns are phrases now.

**Scans go to a model, not to an OCR engine.** An OCR engine matches shapes; a
model reads the page and understands what it is looking at. On real handwriting
that is the difference between `pupper diag / Lis cenique` and a correct Cholesky
decomposition. `kispy-ocr`, the Vision-based fallback, is only used when the model
route fails.

## The timetable, in detail

Three sources, tried in order when `source = "auto"`:

1. **macOS Calendar**, through `kispy-cal`. EventKit, so no OAuth and no token to
   refresh, and it reflects a change within minutes. The window runs from the
   start of the academic year to a month ahead — it has to cover past sessions,
   otherwise "Lecture 6" cannot be counted. Permission is per-application: a
   terminal that has been granted calendar access is not the same as another one
   that has not.
2. **An imported `timetable.json`**, expanded here into individual sessions.
3. **A published `.ics` URL**, cached locally; a stale cache beats no timetable.

Whichever answers, the same cleanup runs: non-teaching slots dropped by regex
(editable under `[calendar] exclude`), all-day markers dropped by duration,
duplicates removed, spellings merged when they are more than 90 % similar, and
sessions numbered per academic year.

Filing is decided by `folders.toml` first — an explicit table that always wins —
and only then by name similarity, with a boost for initials so that `QMF` finds
*Quantitative Methods in Finance*. Whatever is decided is written back to the
table, so a course cannot drift between two folders from one week to the next.

## Changing things

| you want to | do this |
|---|---|
| change how the PDF looks | edit `~/.local/lib/kispy/style/preamble.tex` |
| change how the document is written | edit `RULES` in `kispy/synth.py` |
| write documents in another language | first line of `RULES` |
| stop a kind of event being treated as a class | add a regex to `[calendar] exclude` |
| fix a course filed in the wrong folder | edit `~/.config/kispy/folders.toml` |
| stop mishearings of a name or acronym | add it to `~/.config/kispy/glossary/_global.txt` |
| use the API instead of your subscription | export `ANTHROPIC_API_KEY`, set `backend = "api"` |
| turn off the model-based proofreading | `[audit] coverage = false` |

After editing anything under `kispy/` in a clone, re-run `./install.sh` — it copies
the package into place.

## Known limits

- **Recording stops when the lid closes.** Clamshell sleep is enforced by the
  firmware on Apple Silicon; `caffeinate` prevents *idle* sleep, not that. Kispy
  detects the dead capture within two minutes and restarts, but the gap is gone.
- **Only the text of a `.docx` is extracted.** A classmate's notes full of
  embedded figures arrive as prose with holes. Kispy counts the images it could
  not read and tells you after the build; the figures themselves are not passed on.
  A hand-drawn diagram on a *scanned* page is handled better: it is described and
  redrawn.
- **One job at a time.** Two overlapping lectures in the timetable mean the
  nearest one wins.
- **The coverage check is only as good as the transcript.** It compares the
  document against the material it was given, not against the lecture.
