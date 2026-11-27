"""Fenster und Posteingang-Popup gegen den Nachbau prüfen und Bildschirmfotos speichern.

  MOCK_PAPERLESS_MORE=1 python3 tests/mock_paperless.py 8765 &
  PAPERLESS_URL=http://127.0.0.1:8765 PAPERLESS_TOKEN=testtoken QT_QPA_PLATFORM=offscreen python3 tests/gui_check.py
Bildschirmfotos: /tmp/paperless_*.png; Rückgabewert 1, wenn eine Prüfung fehlschlägt.
"""

import os
import sys
import tempfile

sys.path.insert(0, ".")
os.environ.setdefault("XDG_CACHE_HOME", tempfile.mkdtemp(prefix="pl-cache-"))
os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp(prefix="pl-cfg-"))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtGui import QIcon  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from mko_paperless.library import Library  # noqa: E402
from mko_paperless.popup import InboxPopup  # noqa: E402
from mko_paperless.qtbits import ImageCache  # noqa: E402
from mko_paperless.window import DOC_ROLE, DocumentWindow  # noqa: E402

app = QApplication([])
QIcon.setThemeSearchPaths(QIcon.themeSearchPaths() + ["/usr/share/icons"])
QIcon.setThemeName("breeze")
failures = []


def check(cond, text):
    print(("OK    " if cond else "FEHLER") + " " + text)
    if not cond:
        failures.append(text)


lib = Library()
win = DocumentWindow(lib)
win.resize(1400, 880)
win.show()
steps = []


def step(ms):
    def deco(fn):
        steps.append((ms, fn))
        return fn
    return deco


@step(2500)
def inbox():
    check(win.sidebar.topLevelItem(0).text(1) not in ("", "0"), f"Posteingang-Zähler: {win.sidebar.topLevelItem(0).text(1)}")
    n = win.model.rowCount()
    check(n >= 2, f"Posteingang zeigt {n} Dokumente")
    check(len(win.thumbs.mem) >= 1, f"Vorschaubilder geladen: {len(win.thumbs.mem)}")
    win.grab().save("/tmp/paperless_1_posteingang.png")


@step(400)
def all_docs():
    item = win.sidebar.topLevelItem(1)
    win.sidebar.setCurrentItem(item)


@step(2500)
def select():
    n = win.model.rowCount()
    check(n >= 5, f"Alle Dokumente: {n}")
    win.view.setCurrentIndex(win.model.index(1))


@step(2000)
def detail():
    check(win.detail.isVisible(), "Detailbereich sichtbar")
    doc = win.model.index(1).data(DOC_ROLE)
    check(win.detail.title.text() == doc["title"], "Titel im Formular")
    win.grab().save("/tmp/paperless_2_detail.png")
    win.detail.title.setText(doc["title"] + " (geprüft)")
    win.detail.title.textEdited.emit(win.detail.title.text())
    check(win.detail.b_save.isEnabled(), "Speichern wird aktiv nach Änderung")
    win.detail.save()


@step(1500)
def saved():
    doc = win.model.index(1).data(DOC_ROLE)
    check(doc["title"].endswith("(geprüft)"), f"gespeichert: {doc['title']}")


@step(100)
def search():
    win.search.setText("Lieferschein")


@step(2000)
def searched():
    titles = [win.model.index(i).data(DOC_ROLE)["title"] for i in range(win.model.rowCount())]
    check(titles and all("Lieferschein" in t for t in titles), f"Suche: {titles}")
    win.grab().save("/tmp/paperless_3_suche.png")
    win.search.clear()


@step(100)
def tag_filter():
    for i in range(win.sidebar.topLevelItemCount()):
        sec = win.sidebar.topLevelItem(i)
        for j in range(sec.childCount()):
            if sec.child(j).text(0) == "Haus":
                win.sidebar.setCurrentItem(sec.child(j))
                return
    check(False, "Tag „Haus“ in der Seitenleiste")


@step(2000)
def tagged():
    ok = all(3 in win.model.index(i).data(DOC_ROLE)["tags"] for i in range(win.model.rowCount()))
    check(win.model.rowCount() > 0 and ok, f"Tag-Filter Haus: {win.model.rowCount()} Dokumente")


popup = None


@step(100)
def open_popup():
    global popup
    popup = InboxPopup(lib, ImageCache(lambda i: lib.thumbnail(i), "thumbs-popup"))
    popup.resize(460, 620)
    popup.show()
    popup.refresh()


@step(2500)
def popup_shot():
    check(popup.list.count() >= 2, f"Popup: {popup.list.count()} Einträge")
    popup.grab().save("/tmp/paperless_4_popup.png")
    first = popup.list.itemWidget(popup.list.item(0))
    popup._done(first.doc)


@step(2000)
def popup_done():
    check(popup.list.count() >= 1, f"nach Erledigt: {popup.list.count()} Einträge")
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
QTimer.singleShot(40000, app.quit)
app.exec()
print("Alle Prüfungen bestanden." if not failures else f"{len(failures)} Fehler")
sys.exit(1 if failures else 0)
