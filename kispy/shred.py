"""Remove the working material. Only ever called once the PDF exists."""
from __future__ import annotations
import os, pathlib


def wipe(path: pathlib.Path) -> None:
    try:
        if path.is_file():
            size = path.stat().st_size
            if size:
                with open(path, "r+b", buffering=0) as fh:
                    fh.write(os.urandom(min(size, 1 << 20)))
                    fh.flush()
                    os.fsync(fh.fileno())
            path.unlink()
        elif path.is_dir():
            for child in sorted(path.rglob("*"), key=lambda p: -len(p.parts)):
                wipe(child)
            path.rmdir()
    except OSError:
        pass


def clean(work: pathlib.Path) -> None:
    for child in list(work.iterdir()):
        wipe(child)
