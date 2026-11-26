"""Prüft das Laufwerk gegen den Nachbau: Ordner, Lesen, Hochladen, Alben, Suche, Aushängen.

Voraussetzung: tests/mock_immich.py läuft, IMMICH_URL/IMMICH_API_KEY sind gesetzt.
Aufruf: python3 tests/fs_check.py [Einhängepunkt]
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

MNT = Path(sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp(prefix="mko-immich-"))
URL = os.environ["IMMICH_URL"]
HDR = {"x-api-key": os.environ["IMMICH_API_KEY"]}


def state() -> dict:
    return requests.get(f"{URL}/api/_test/state", headers=HDR, timeout=10).json()


def check(cond, text):
    print(("OK    " if cond else "FEHLER") + " " + text)
    if not cond:
        raise SystemExit(1)


def wait_for(fn, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.25)
    return False


def tree(path: Path, indent=0):
    for p in sorted(path.iterdir()):
        print("   " + "  " * indent + p.name + ("/" if p.is_dir() else f"  ({p.stat().st_size} B)"))
        if p.is_dir():
            tree(p, indent + 1)


def main():
    MNT.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen([sys.executable, "-m", "mko_immich", "mount", str(MNT)])
    try:
        check(wait_for(lambda: os.path.ismount(MNT)), f"eingehängt unter {MNT}")
        tree(MNT)
        top = sorted(os.listdir(MNT))
        check(top == sorted(["Alben", "Zeitachse", "Favoriten", "Suche", "Hochladen"]), f"oberste Ordner {top}")
        check(sorted(os.listdir(MNT / "Alben")) == ["Garten", "Urlaub Ostsee"], "Alben gelistet")
        check(sorted(os.listdir(MNT / "Zeitachse")) == ["2025", "2026"], "Jahre in der Zeitachse")
        check(sorted(os.listdir(MNT / "Zeitachse/2026")) == ["01 Januar", "04 April"], "Monate 2026")
        garten = sorted(os.listdir(MNT / "Alben/Garten"))
        check(len(garten) == 3 and sum("[" in n for n in garten) == 2, f"Namenskollision mit Kennzeichen: {garten}")
        check("2026-04-03 11-00-00 IMG_0100.jpg" in garten, "Dateiname Datum + Originalname")
        favs = os.listdir(MNT / "Favoriten")
        check(favs == ["2025-07-14 18-02-44 IMG_0002.jpg"], f"Favoriten {favs}")

        # Lesen: Größe aus exifInfo, nachgefragte Größe, ganz ohne Größe
        f1 = MNT / "Alben/Urlaub Ostsee/2025-07-14 09-30-05 IMG_0001.jpg"
        data = f1.read_bytes()
        check(data[:2] == b"\xff\xd8" and len(data) == f1.stat().st_size, f"JPEG gelesen ({len(data)} B)")
        jan = MNT / "Zeitachse/2026/01 Januar"
        hund = jan / "2026-01-20 15-45-12 Hund im Schnee.jpg"
        check(hund.stat().st_size > 0 and len(hund.read_bytes()) == hund.stat().st_size, "Größe einzeln nachgefragt")
        rezept = jan / "2026-01-21 08-00-00 Scan Rezept.jpg"
        check(rezept.read_bytes()[:2] == b"\xff\xd8" and len(rezept.read_bytes()) > 500, "ohne Größenangabe vollständig gelesen")
        url = os.getxattr(f1, "user.xdg.origin.url").decode()
        check(url.startswith(URL + "/photos/"), f"xattr Link {url}")
        print("   Kommentar:", os.getxattr(f1, "user.xdg.comment").decode())

        # Schreibschutz
        try:
            with open(f1, "ab") as fh:
                fh.write(b"x")
            check(False, "Schreiben in vorhandenes Foto verhindert")
        except OSError as exc:
            check(True, f"Schreiben in vorhandenes Foto verhindert ({exc.strerror})")
        try:
            (MNT / "Zeitachse/2026/04 April/neu.jpg").write_bytes(b"x")
            check(False, "Ablegen in der Zeitachse verhindert")
        except OSError as exc:
            check(True, f"Ablegen in der Zeitachse verhindert ({exc.strerror})")

        # Hochladen per cp in ein Album
        src = Path(tempfile.mkdtemp()) / "Neues Bild.jpg"
        src.write_bytes(f1.read_bytes()[:-2] + b"\x00\x01\xff\xd9")
        subprocess.run(["cp", str(src), str(MNT / "Alben/Garten/")], check=True)
        check(wait_for(lambda: "Neues Bild.jpg" in state()["albums"]["Garten"]), "cp ins Album: hochgeladen + zugeordnet")

        # Hochladen in "Hochladen"
        src2 = src.with_name("Drop.jpg")
        src2.write_bytes(src.read_bytes() + b"\x00")
        shutil.copy(src2, MNT / "Hochladen/Drop.jpg")
        check(wait_for(lambda: any(a["name"] == "Drop.jpg" for a in state()["assets"].values())), "cp nach Hochladen")

        # KIO-Art: erst .part schreiben, dann umbenennen
        part = MNT / "Hochladen/Kio Bild.jpg.part"
        part.write_bytes(src.read_bytes() + b"\x01\x02")
        time.sleep(1.5)
        check(not any(a["name"].startswith("Kio Bild") for a in state()["assets"].values()), ".part wird nicht hochgeladen")
        os.rename(part, MNT / "Hochladen/Kio Bild.jpg")
        os.utime(MNT / "Hochladen/Kio Bild.jpg", (1700000000, 1700000000))
        check(wait_for(lambda: any(a["name"] == "Kio Bild.jpg" for a in state()["assets"].values())), ".part -> Name: hochgeladen")
        kio = next(a for a in state()["assets"].values() if a["name"] == "Kio Bild.jpg")
        check(kio["local"].startswith("2023-11-14"), f"Dateidatum übernommen ({kio['local']})")

        # Album anlegen und hineinkopieren (vorhandenes Foto -> Duplikat erkannt, nur zugeordnet)
        os.mkdir(MNT / "Alben/Neues Album")
        check("Neues Album" in state()["albums"], "mkdir in Alben legt Album an")
        before = len(state()["assets"])
        shutil.copyfile(f1, MNT / "Alben/Neues Album/kopie.jpg")
        check(wait_for(lambda: state()["albums"]["Neues Album"] == ["IMG_0001.jpg"]), "Kopie vorhandenes Foto -> zugeordnet")
        check(len(state()["assets"]) == before, "kein doppeltes Foto angelegt")

        # Verschieben zwischen Alben
        os.rename(MNT / "Alben/Neues Album/2025-07-14 09-30-05 IMG_0001.jpg",
                  MNT / "Alben/Garten/2025-07-14 09-30-05 IMG_0001.jpg")
        st = state()["albums"]
        check(st["Neues Album"] == [] and "IMG_0001.jpg" in st["Garten"], "mv Album -> Album")

        # Entfernen aus Album (Foto bleibt)
        os.unlink(MNT / "Alben/Garten/2026-04-03 11-00-00 IMG_0100.jpg")
        st = state()
        check("IMG_0100.jpg" not in st["albums"]["Garten"], "rm im Album entfernt nur die Zuordnung")
        check(any(a["name"] == "IMG_0100.jpg" and not a["trashed"] for a in st["assets"].values()), "Foto bleibt in Immich")

        # Löschen in der Zeitachse ohne allow_delete verboten
        try:
            os.unlink(MNT / "Zeitachse/2026/04 April/2026-04-03 11-00-00 IMG_0100.jpg")
            check(False, "Löschen in der Zeitachse verhindert")
        except PermissionError:
            check(True, "Löschen in der Zeitachse ohne allow_delete verhindert")

        # Gespeicherte Suche
        os.mkdir(MNT / "Suche/strand")
        hits = sorted(os.listdir(MNT / "Suche/strand"))
        check(len(hits) == 2 and all("IMG_000" in h for h in hits), f"Suche/strand: {hits}")
        os.rmdir(MNT / "Suche/strand")
        check("strand" not in os.listdir(MNT / "Suche"), "Suche entfernt")
        tree(MNT / "Alben")
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            subprocess.run(["fusermount3", "-uz", str(MNT)], check=False)
    check(not os.path.ismount(MNT), f"nach SIGTERM ausgehängt (Rückgabe {proc.returncode})")
    print("Alle Laufwerksprüfungen bestanden.")


if __name__ == "__main__":
    main()
