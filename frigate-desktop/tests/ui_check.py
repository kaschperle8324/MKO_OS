"""Oberflächen-, Benachrichtigungs- und Aktionstest gegen den Frigate-Nachbau (tests/mock_frigate.py).

python3 tests/mock_frigate.py --port 18971 --interval 0 &
QT_QPA_PLATFORM=offscreen FRIGATE_URL=http://127.0.0.1:18971 FRIGATE_USER=admin FRIGATE_PASSWORD=geheim \
    python3 tests/ui_check.py
Speichert Bildschirmfotos nach /tmp/frigate_*.png; Rückgabewert 1, wenn eine Prüfung fehlschlägt.
"""

import json
import os
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="frigate-test-"))
for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
    os.environ[var] = str(TMP / var.lower())
os.environ["MKO_FRIGATE_SNAPSHOTS"] = str(TMP / "Bilder")
os.environ.setdefault("FRIGATE_URL", "http://127.0.0.1:18971")
os.environ.setdefault("FRIGATE_USER", "admin")
os.environ.setdefault("FRIGATE_PASSWORD", "geheim")
MOCK = os.environ["FRIGATE_URL"]

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtGui import QIcon  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

sys.path.insert(0, ".")
from mko_frigate import actions  # noqa: E402
from mko_frigate import config as C  # noqa: E402
from mko_frigate.api import AuthError, Frigate  # noqa: E402

s = C.Settings(url=MOCK, username="admin", poll_interval=2, verify_tls=False)
C.save(s)

app = QApplication([])
app.setQuitOnLastWindowClosed(False)
QIcon.setThemeSearchPaths(QIcon.themeSearchPaths() + ["/usr/share/icons"])
QIcon.setThemeName("breeze")

from mko_frigate.app import Controller  # noqa: E402
from mko_frigate.ui import SetupDialog  # noqa: E402
from mko_frigate.wall import CameraWall  # noqa: E402

played = []
actions._spawn = lambda cmd: played.append(cmd) or True      # keine Player starten
ctl = Controller(app)
popup = ctl.popup
popup.pinned = True
notes = []
ctl.notifier.show = lambda summary, body, acts, ctx=None, **kw: notes.append(
    {"summary": summary, "body": body, "actions": list(acts), "image": kw.get("image"), "ctx": ctx})
log, failures = [], []


def check(cond, text):
    log.append(("OK   " if cond else "FEHL ") + text)
    if not cond:
        failures.append(text)


def mock(path, body):
    req = urllib.request.Request(MOCK + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req).read())


def later(ms, fn):
    QTimer.singleShot(ms, fn)


# --------------------------------------------------------------- Popup
def step_grid():
    popup.show_popup()
    popup.resize(680, 540)
    later(3500, shot_grid)


def shot_grid():
    popup.grab().save("/tmp/frigate_1_raster.png")
    check(len(popup.grid.images) == 3, f"Raster: {len(popup.grid.images)}/3 Kamerabilder geladen")
    check(popup.grid_feed.active, "Raster: Aktualisierung läuft, solange sichtbar")
    popup.open_camera("haustuer")
    later(2500, shot_single)


def shot_single():
    popup.grab().save("/tmp/frigate_2_einzeln.png")
    img = popup.single.images.get("haustuer")
    check(img is not None and img.height() >= 700, f"Einzelansicht: großes Bild ({img and img.height()} px hoch)")
    check(popup.single_feed.active and not popup.grid_feed.active, "Einzelansicht: nur noch Einzelbild wird abgerufen")
    popup.b_live.click()
    check(any("rtsp://127.0.0.1:8554/haustuer_main" in c for c in played),
          f"Live-Stream: {played[-1] if played else '–'}")
    popup.b_events.click()
    later(2500, shot_events)


def shot_events():
    popup.grab().save("/tmp/frigate_3_ereignisse.png")
    n = popup.list.count()
    check(n > 0, f"Ereignisliste: {n} Einträge für Haustür")
    ended = next(e for e in popup.events if e.get("end_time"))
    popup._clip(ended)
    popup._save_event(ended)
    later(2000, step_actions)


def step_actions():
    ended = next(e for e in popup.events if e.get("end_time"))
    clip = C.CACHE_DIR / "clips" / f"{ended['id']}.mp4"
    check(clip.exists() and clip.stat().st_size > 64, f"Clip heruntergeladen: {clip.name}")
    check(any(str(clip) in c for c in played), "Clip an Player übergeben")
    snaps = list((TMP / "Bilder").glob("*.jpg"))
    check(bool(snaps), f"Schnappschuss gespeichert: {[p.name for p in snaps]}")
    popup.hide()
    check(not popup.grid_feed.active and not popup.single_feed.active, "Popup versteckt: keine Bildabrufe mehr")
    # Benachrichtigung: Person an der Haustür (gemeldet) und Hund im Garten (gefiltert)
    notes.clear()
    mock("/test/event", {"camera": "haustuer", "label": "person"})
    mock("/test/event", {"camera": "garten", "label": "dog"})
    later(5000, step_notes)


def step_notes():
    check(len(notes) == 1, f"Benachrichtigungen: {len(notes)} (erwartet 1, Hund gefiltert)")
    if notes:
        n = notes[0]
        check(n["summary"] == "Person – Haustür", f"Titel: {n['summary']!r}")
        check(n["actions"] == ["default", "clip", "mute"], f"Aktionen: {n['actions']}")
        check(bool(n["image"]) and Path(n["image"]).exists(), f"Vorschaubild: {n['image']}")
        ctl._notification_action("default", n["ctx"])
        check(popup.isVisible() and popup.stack.currentIndex() == popup.PAGE_SINGLE and popup.camera == "haustuer",
              "„Ansehen“ öffnet die Haustür-Einzelansicht")
        popup.hide()
    notes.clear()
    mock("/test/event", {"camera": "einfahrt", "label": "car", "count": 5})
    later(5000, step_burst)


def step_burst():
    check(len(notes) == 1 and notes[0]["summary"].startswith("5 neue"),
          f"Sammelmeldung: {[n['summary'] for n in notes]}")
    notes.clear()
    ctl._notification_action("mute", None)
    check(len(notes) == 1 and "stumm" in notes[0]["summary"].lower(), "„Stumm 1 h“ bestätigt")
    notes.clear()
    mock("/test/event", {"camera": "haustuer", "label": "person"})
    later(5000, step_muted)


def step_muted():
    check(not notes, f"Stumm: keine Meldung ({len(notes)})")
    C.save_state(muted_until=0)
    mock("/test/end", {})
    later(500, step_wall)


# ---------------------------------------------------------- Kamerawand
wall = None


def step_wall():
    global wall
    wall = CameraWall(ctl.fr)
    wall.resize(1600, 900)
    wall.show()
    later(3500, shot_wall)


def shot_wall():
    wall.grab().save("/tmp/frigate_4_wand.png")
    check(len(wall.grid.images) == 3, f"Kamerawand: {len(wall.grid.images)}/3 Bilder")
    wall.resize(1024, 768)
    wall.grab().save("/tmp/frigate_5_wand_4zu3.png")
    wall._toggle_focus("garten")
    later(2000, shot_wall_focus)


def shot_wall_focus():
    wall.grab().save("/tmp/frigate_6_wand_einzeln.png")
    check(wall.grid.cameras == ["garten"], "Kamerawand: Klick zeigt nur eine Kamera")
    wall.close()
    step_setup()


# ---------------------------------------------------------- Einrichtung
dlg = None


def step_setup():
    global dlg
    dlg = SetupDialog(C.load())
    dlg.password.setText("geheim")
    dlg.show()
    dlg.test_btn.click()
    later(2000, shot_setup)


def shot_setup():
    dlg.grab().save("/tmp/frigate_7_einrichten.png")
    check("Verbunden" in dlg.status.text(), f"Einrichtung: {dlg.status.text()}")
    dlg.close()
    try:
        Frigate(C.Settings(url=MOCK, username="admin"), secret="falsch").check()
        check(False, "falsches Passwort wird abgelehnt")
    except AuthError as exc:
        check(True, f"falsches Passwort wird abgelehnt: {exc}")
    popup.set_client(None)
    popup.show()
    popup.resize(680, 540)
    later(300, shot_empty)


def shot_empty():
    popup.grab().save("/tmp/frigate_8_nicht_eingerichtet.png")
    check(popup.stack.currentIndex() == popup.PAGE_EMPTY, "ohne Einrichtung: Hinweis statt Dialog")
    ctl.quit()


later(1500, step_grid)
app.exec()
print("\n".join(log))
shutil.rmtree(TMP, ignore_errors=True)
print("FEHLER: " + "; ".join(failures) if failures else "Alle Prüfungen bestanden.")
sys.exit(1 if failures else 0)
