"""Umbruch und Breite der Seitenleiste prüfen (Bildschirmfotos /tmp/claude_breite_*.png).

  QT_QPA_PLATFORM=offscreen MKO_CLAUDE_BIN=$PWD/tests/fake_claude.py python3 tests/wrap_check.py
"""

import os
import sys
import tempfile

sys.path.insert(0, ".")
os.environ.setdefault("XDG_STATE_HOME", tempfile.mkdtemp(prefix="claude-state-"))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtGui import QIcon  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from mko_claude import settings as st  # noqa: E402
from mko_claude.ui import MessageLabel, PermissionCard, Sidebar, ToolLine  # noqa: E402

app = QApplication([])
QIcon.setThemeSearchPaths(QIcon.themeSearchPaths() + ["/usr/share/icons"])
QIcon.setThemeName("breeze")
failures = []


def check(cond, text):
    print(("OK    " if cond else "FEHLER") + " " + text)
    if not cond:
        failures.append(text)


TEXT = """Hier ist das Ergebnis der Prüfung:

1. Die Datei liegt unter `/home/markus/Paperless/Korrespondenten/Finanzamt_Werra-Meissner/Einkommensteuerbescheid_2025_endgueltig.pdf`
2. Link: https://kaschperle8324.github.io/MKO_OS/apt/dists/stable/main/binary-amd64/Packages.gz?wirklich=lange-adresse-ohne-leerzeichen

```bash
sudo apt-get install -y mko-desktop mko-claude mko-paperless mko-immich mko-frigate mko-vault mko-dashboard && systemctl --user restart mko-paperless-fs.service
```

| Paket | Version |
|---|---|
| mko-paperless | 0.3.0 |

Einwortbandwurm: Donaudampfschifffahrtselektrizitätenhauptbetriebswerkbauunterbeamtengesellschaft."""

cfg = st.load()
bar = Sidebar(cfg)
bar.resize(360, 900)
bar.show()
msg = MessageLabel(TEXT)
bar._add(msg)
bar._add(ToolLine("Bash", "ls -la /home/markus/Paperless/Korrespondenten/Finanzamt_Werra-Meissner/Unterordner/2025"))
card = PermissionCard("r1", "Befehl ausführen", "rm -rf /home/markus/Downloads/tmp_ordner_mit_sehr_langem_namen_ohne_leerzeichen/*.tmp", "")
bar._add(card)


def wide_enough(width):
    inner = bar.scroll.viewport().width()
    over = [w for w in bar.chat.findChildren(MessageLabel) if w.width() > inner]
    check(not over, f"Breite {width}: kein Text breiter als die Leiste ({inner}px)")
    check(bar.scroll.horizontalScrollBar().maximum() == 0, f"Breite {width}: keine waagerechte Bildlaufleiste")
    check(bar.chat.sizeHint().width() <= inner + 2 or bar.chat.width() <= inner + 2,
          f"Breite {width}: Inhalt passt ({bar.chat.width()} ≤ {inner})")


def s1():
    wide_enough(360)
    bar.grab().save("/tmp/claude_breite_360.png")
    hmin = msg.height()
    bar.resize(820, 900)
    QTimer.singleShot(400, lambda: s2(hmin))


def s2(hmin):
    wide_enough(820)
    check(msg.height() < hmin, f"breiter → niedriger ({hmin} → {msg.height()})")
    bar.grab().save("/tmp/claude_breite_820.png")
    from PySide6.QtTest import QTest
    from PySide6.QtCore import Qt
    before = bar.width()
    bar.input.setFocus()
    QTest.keyClick(bar.input, Qt.Key_Minus, Qt.ControlModifier)
    check(bar.width() == min(before - 80, bar._max_width()), f"Strg+Minus im Eingabefeld: {before} → {bar.width()}")
    check(st.saved_width() == bar.width(), "Breite gespeichert")
    st.save_width(612)
    check(st.load().width == 612, "gespeicherte Breite wird geladen")
    app.quit()


QTimer.singleShot(800, s1)
QTimer.singleShot(10000, app.quit)
app.exec()
print("Alle Prüfungen bestanden." if not failures else f"{len(failures)} Fehler")
sys.exit(1 if failures else 0)
