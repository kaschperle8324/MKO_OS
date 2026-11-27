"""Fotos-Fenster, Betrachter und Popup gegen den Nachbau prüfen und Bildschirmfotos speichern.

  MOCK_IMMICH_MORE=1 python3 tests/mock_immich.py 8766 &
  IMMICH_URL=http://127.0.0.1:8766 IMMICH_API_KEY=testkey QT_QPA_PLATFORM=offscreen python3 tests/gui_check.py
Bildschirmfotos: /tmp/immich_*.png; Rückgabewert 1, wenn eine Prüfung fehlschlägt.
"""

import os
import sys
import tempfile

sys.path.insert(0, ".")
os.environ.setdefault("XDG_CACHE_HOME", tempfile.mkdtemp(prefix="im-cache-"))
os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp(prefix="im-cfg-"))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtGui import QIcon  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from mko_immich.library import Library  # noqa: E402
from mko_immich.popup import PhotoPopup  # noqa: E402
from mko_immich.window import ITEM_ROLE, PhotoWindow, show_filter  # noqa: E402

app = QApplication([])
QIcon.setThemeSearchPaths(QIcon.themeSearchPaths() + ["/usr/share/icons"])
QIcon.setThemeName("breeze")
failures = []


def check(cond, text):
    print(("OK    " if cond else "FEHLER") + " " + text)
    if not cond:
        failures.append(text)


lib = Library()
win = PhotoWindow(lib)
win.resize(1400, 900)
win.show()
steps = []


def step(ms):
    def deco(fn):
        steps.append((ms, fn))
        return fn
    return deco


@step(3500)
def timeline():
    rows = win.model.rows
    headers = [r["header"] for r in rows if "header" in r]
    assets = [r for r in rows if "id" in r]
    check(len(headers) >= 2, f"Monatsüberschriften: {headers[:4]}")
    check(len(assets) >= 10, f"Fotos in der Zeitachse: {len(assets)}")
    check(len(win.thumbs.mem) >= 5, f"Vorschaubilder geladen: {len(win.thumbs.mem)}")
    names = [win.sidebar.topLevelItem(i).text(0) for i in range(win.sidebar.topLevelItemCount())]
    check("PERSONEN" in names and "ALBEN" in names, f"Seitenleiste: {names}")
    win.grab().save("/tmp/immich_1_zeitachse.png")
    idx = next(win.model.index(i) for i, r in enumerate(rows) if "id" in r)
    win._open_index(idx)


@step(1500)
def viewer():
    check(win.pages.currentWidget() is win.viewer, "Betrachter offen")
    check(win.viewer.image.pixmap() is not None and not win.viewer.image.pixmap().isNull(), "Bild angezeigt")
    win.grab().save("/tmp/immich_2_betrachter.png")
    first = win.viewer.current["id"]
    win.viewer.go_next()
    check(win.viewer.current["id"] != first, "Weiterblättern")
    global was
    was = bool(win.viewer.current.get("isFavorite"))
    win.viewer.toggle_fav()


@step(1200)
def faved():
    check(bool(win.viewer.current.get("isFavorite")) != was, "Favorit umgeschaltet")
    win.close_viewer()
    show_filter(win, {"kind": "favorites"})


@step(2000)
def favorites():
    assets = win.model.assets()
    check(assets and all(a.get("isFavorite") for a in assets), f"Favoriten: {len(assets)}")
    for i in range(win.sidebar.topLevelItemCount()):
        sec = win.sidebar.topLevelItem(i)
        for j in range(sec.childCount()):
            if sec.child(j).text(0) == "Urlaub Ostsee":
                win.sidebar.setCurrentItem(sec.child(j))


@step(2000)
def album():
    names = [a["originalFileName"] for a in win.model.assets()]
    check(sorted(names) == ["IMG_0001.jpg", "IMG_0002.jpg"], f"Album Urlaub Ostsee: {names}")
    win.search.setText("Strand")
    win.reload()


@step(2000)
def search():
    names = [a["originalFileName"] for a in win.model.assets()]
    check("IMG_0001.jpg" in names, f"Suche Strand: {names}")
    win.search.clear()
    show_filter(win, {"kind": "timeline"})


@step(2500)
def select():
    win.view.selectAll()
    n = len(win.selected_ids())
    check(n >= 5 and win.act_trash.isVisible(), f"Mehrfachauswahl: {n}")


@step(300)
def selected_shot():
    win.grab().save("/tmp/immich_3_auswahl.png")
    win.view.clearSelection()


popup = None
was = False


@step(200)
def open_popup():
    global popup
    popup = PhotoPopup(lib, win.thumbs)
    popup.resize(540, 660)
    popup.show()
    popup.refresh()


@step(2500)
def popup_shot():
    check(len(popup.model.assets()) >= 10, f"Popup neueste: {len(popup.model.assets())}")
    popup.grab().save("/tmp/immich_4_popup.png")
    popup._mode("albums")


@step(1500)
def popup_albums():
    check(popup.album_list.count() >= 2, f"Popup Alben: {popup.album_list.count()}")
    popup.grab().save("/tmp/immich_5_popup_alben.png")
    app.quit()


def run(i=0):
    if i >= len(steps):
        return
    ms, fn = steps[i]

    def go():
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            failures.append(f"{fn.__name__}: {exc}")
        run(i + 1)

    QTimer.singleShot(ms, go)


run()
QTimer.singleShot(60000, app.quit)
app.exec()
print("Alle Prüfungen bestanden." if not failures else f"{len(failures)} Fehler")
sys.exit(1 if failures else 0)
