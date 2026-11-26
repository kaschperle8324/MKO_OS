"""Anhänge mit ClamAV prüfen (falls installiert). Ein Aufruf prüft alle Anhänge einer Nachricht."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile

FOUND = re.compile(r"^(.*): (.+) FOUND$")


def scanner() -> str | None:
    return os.environ.get("MKO_CLAMSCAN") or shutil.which("clamscan")


def scan(attachments) -> dict[int, str | None] | None:
    """Gibt je Anhang (Index) die gefundene Signatur oder None zurück; None insgesamt = keine Prüfung möglich."""
    exe = scanner()
    if not exe or not attachments:
        return None
    with tempfile.TemporaryDirectory(prefix="mko-mail-scan-") as tmp:
        paths = []
        for i, att in enumerate(attachments):
            path = os.path.join(tmp, f"{i:03d}")
            with open(path, "wb") as fh:
                fh.write(att.data)
            paths.append(path)
        try:
            res = subprocess.run([exe, "--infected", "--no-summary", "--stdout", *paths],
                                 capture_output=True, text=True, timeout=300)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if res.returncode not in (0, 1):   # z. B. Signaturen fehlen noch
            return None
        result: dict[int, str | None] = {i: None for i in range(len(paths))}
        for line in res.stdout.splitlines():
            m = FOUND.match(line.strip())
            if m and m.group(1) in paths:
                result[paths.index(m.group(1))] = m.group(2)
        return result
