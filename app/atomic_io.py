"""Atomic file writes that degrade gracefully on Docker bind mounts.

The standard write-tmp-then-rename pattern gives atomic, durable writes — but
it breaks when the destination is a single-file Docker bind mount. You cannot
``rename()`` over the mountpoint, so ``os.replace()`` raises ``OSError`` with
``errno.EBUSY``. In that case we fall back to writing in place: not atomic, but
the only option for a bind-mounted file.
"""

from __future__ import annotations

import errno
import json
import os
from pathlib import Path


def atomic_write_text(path, text: str) -> None:
    """Write ``text`` to ``path`` atomically, falling back to an in-place
    write when ``path`` is a Docker bind-mounted file (EBUSY on rename).

    ``path`` may be a ``str`` or ``pathlib.Path``."""
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    try:
        os.replace(tmp, path)
    except OSError as exc:
        if exc.errno != errno.EBUSY:
            raise
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        try:
            os.unlink(tmp)
        except OSError:
            pass


def atomic_write_json(path, data, *, indent: int = 2) -> None:
    """Serialise ``data`` as JSON and write it atomically (see
    :func:`atomic_write_text`)."""
    atomic_write_text(path, json.dumps(data, indent=indent))
