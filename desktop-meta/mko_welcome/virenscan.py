"""Virenprüfung auf Abruf mit ClamAV (Dolphin: Rechtsklick → „Auf Viren prüfen“).

  mko-virenscan DATEI_ODER_ORDNER …

Es läuft kein Scanner dauerhaft im Hintergrund; nur die Signaturen werden von
clamav-freshclam aktuell gehalten. Fundstücke lassen sich in die Quarantäne
(~/.local/share/mko-os/quarantaene) verschieben oder löschen.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import time
from pathlib import Path

QUARANTINE = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "mko-os" / "quarantaene"
FOUND = re.compile(r"^(.*): (.+) FOUND$")


def scanner() -> str | None:
    return os.environ.get("MKO_CLAMSCAN") or shutil.which("clamscan")


def parse(output: str) -> list[tuple[str, str]]:
    return [(m.group(1), m.group(2)) for line in output.splitlines() if (m := FOUND.match(line.strip()))]


def quarantine(path: str) -> Path:
    QUARANTINE.mkdir(parents=True, exist_ok=True)
    os.chmod(QUARANTINE, 0o700)
    target = QUARANTINE / f"{time.strftime('%Y%m%d-%H%M%S')}_{Path(path).name}.gesperrt"
    shutil.move(path, target)
    os.chmod(target, 0o400)
    return target


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    paths = [p for p in argv if not p.startswith("-")]

    from PySide6.QtCore import QProcess, Qt, QTimer
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import (QApplication, QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                                   QMessageBox, QProgressBar, QPushButton, QVBoxLayout)

    QApplication.setDesktopFileName("mko-virenscan")
    app = QApplication(sys.argv[:1])
    dlg = QDialog()
    dlg.setWindowTitle("Virenprüfung")
    dlg.setWindowIcon(QIcon.fromTheme("security-high", QIcon.fromTheme("dialog-warning")))
    dlg.resize(620, 380)
    lay = QVBoxLayout(dlg)
    head = QLabel()
    head.setWordWrap(True)
    lay.addWidget(head)
    bar = QProgressBar()
    bar.setRange(0, 0)
    lay.addWidget(bar)
    hits = QListWidget()
    hits.hide()
    lay.addWidget(hits, 1)
    info = QLabel("Die Prüfung lädt zuerst die Virensignaturen – das dauert etwa 20 Sekunden.")
    info.setWordWrap(True)
    info.setStyleSheet("color: gray")
    lay.addWidget(info)
    row = QHBoxLayout()
    btn_q = QPushButton(QIcon.fromTheme("folder-locked"), "In Quarantäne verschieben")
    btn_d = QPushButton(QIcon.fromTheme("edit-delete"), "Löschen")
    btn_c = QPushButton("Abbrechen")
    for b in (btn_q, btn_d):
        b.hide()
        row.addWidget(b)
    row.addStretch()
    row.addWidget(btn_c)
    lay.addLayout(row)
    btn_c.clicked.connect(dlg.close)

    names = ", ".join(Path(p).name or p for p in paths[:3]) + (f" und {len(paths) - 3} weitere" if len(paths) > 3 else "")
    exe = scanner()
    if not paths:
        head.setText("Keine Dateien angegeben.")
        bar.hide()
    elif not exe:
        head.setText("<b>ClamAV ist nicht installiert.</b><br>Installieren mit: <code>sudo apt install clamav clamav-freshclam</code>")
        bar.hide()
        btn_c.setText("Schließen")
    else:
        head.setText(f"<b>Prüfe:</b> {names}")
        proc = QProcess(dlg)
        proc.setProcessChannelMode(QProcess.MergedChannels)
        found: list[tuple[str, str]] = []

        def finished(code, _status):
            out = bytes(proc.readAllStandardOutput()).decode(errors="replace")
            bar.hide()
            info.hide()
            btn_c.setText("Schließen")
            if "No supported database files found" in out or "cl_load" in out:
                head.setText("<b>Die Virensignaturen fehlen noch.</b><br>Sie werden nach dem Start automatisch "
                             "heruntergeladen (Internet nötig). Bitte in einigen Minuten erneut prüfen.")
                return
            found.extend(parse(out))
            if code == 0 or (code == 1 and not found):
                head.setText(f"<b style='color:#27ae60'>✓ Keine Bedrohung gefunden.</b><br>{names}")
            elif found:
                head.setText(f"<b style='color:#da4453'>⚠ {len(found)} Bedrohung(en) gefunden.</b> "
                             "Am besten in die Quarantäne verschieben.")
                hits.show()
                for path, sig in found:
                    hits.addItem(QListWidgetItem(QIcon.fromTheme("dialog-warning"), f"{path}\n    {sig}"))
                btn_q.show()
                btn_d.show()
            else:
                head.setText("<b>Die Prüfung ist mit einem Fehler abgebrochen.</b>")
                hits.show()
                hits.addItem(out.strip()[-800:])

        def act(delete: bool):
            if delete and QMessageBox.question(dlg, "Löschen", "Die gefundenen Dateien endgültig löschen?") != QMessageBox.Yes:
                return
            done = []
            for path, _sig in found:
                try:
                    if delete:
                        os.remove(path)
                    else:
                        quarantine(path)
                    done.append(path)
                except OSError as exc:
                    QMessageBox.warning(dlg, "Virenprüfung", f"{path}: {exc}")
            head.setText(("Gelöscht" if delete else f"In Quarantäne verschoben ({QUARANTINE})") + f": {len(done)} Datei(en).")
            btn_q.hide()
            btn_d.hide()

        btn_q.clicked.connect(lambda: act(False))
        btn_d.clicked.connect(lambda: act(True))
        btn_c.clicked.connect(lambda: proc.state() != QProcess.NotRunning and proc.kill())
        proc.finished.connect(finished)
        QTimer.singleShot(0, lambda: proc.start(exe, ["-r", "--infected", "--no-summary", "--stdout", *paths]))

    dlg.show()
    if os.environ.get("MKO_SCREENSHOT"):
        QTimer.singleShot(int(os.environ.get("MKO_SCREENSHOT_DELAY", "1500")),
                          lambda: (dlg.grab().save(os.environ["MKO_SCREENSHOT"]), app.quit()))
    dlg.finished.connect(app.quit)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
