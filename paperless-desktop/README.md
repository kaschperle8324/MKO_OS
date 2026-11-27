# mko-paperless – Paperless-ngx im KDE-Desktop

Version 0.2.0 · Teil von MKO OS · Lizenz GPL-3.0

Paperless wird zu einem Teil des Systems: Dokumente liegen als Laufwerk im
Dateimanager, die Plasma-Suche findet sie per Volltext, und neue Dokumente im
Posteingang melden sich als Systembenachrichtigung. Eine Webseite muss dafür
nicht geöffnet werden.

## Funktionen

### Paperless als Laufwerk (`~/Paperless`)

```
Paperless/
├── Posteingang/            Dokumente mit Posteingangs-Tag
├── Alle Dokumente/
├── Korrespondenten/<Name>/
├── Dokumenttypen/<Name>/
├── Tags/<Name>/
├── Nach Jahr/<Jahr>/
├── Suche/<Suchbegriff>/    Ordner anlegen = Volltextsuche speichern
└── Hochladen/              Datei hineinziehen = Upload
```

Dateinamen: `2025-10-31 Stromrechnung Oktober [123].pdf` (Datum, Titel, Paperless-ID).

| Aktion im Dateimanager | Wirkung in Paperless |
|---|---|
| Datei öffnen | Archiv-PDF wird geladen (lokal zwischengespeichert) |
| Datei in einen Ordner kopieren | Upload mit den Merkmalen des Ordners (z. B. `Korrespondenten/Amazon` → Korrespondent Amazon, `Tags/Haus` → Tag Haus) |
| Umbenennen | Titel ändern |
| Verschieben `Tags/A` → `Tags/B` | Tag A entfernen, Tag B setzen |
| Verschieben `Posteingang` → `Tags/Haus` | erledigt + Tag Haus |
| Verschieben nach `Korrespondenten/X` | Korrespondent X setzen |
| Löschen in `Posteingang` | als erledigt markieren |
| Löschen in `Tags/X` | nur Tag X entfernen |
| Löschen in `Korrespondenten/X` / `Dokumenttypen/X` | Zuordnung entfernen |
| Löschen in `Alle Dokumente` | nur mit `allow_delete = true`, landet im Paperless-Papierkorb |
| Neuer Ordner in `Tags`, `Korrespondenten`, `Dokumenttypen` | wird in Paperless angelegt |
| Neuer Ordner in `Suche` | gespeicherte Volltextsuche |
| Tags im Dolphin-Infobereich ändern | Tags in Paperless ändern |
| Bearbeitetes PDF speichern (z. B. Okular) | neue Fassung wird mit Titel, Korrespondent, Typ und Tags des Originals hochgeladen; das Original bleibt erhalten |

Korrespondent und Typ stehen zusätzlich im Kommentarfeld der Datei
(`user.xdg.comment`), der Link zur Weboberfläche in `user.xdg.origin.url`.

### Suche in KRunner

`Alt+Leertaste` und tippen: Paperless-Treffer erscheinen mit Datum,
Korrespondent und Typ. Mit dem Präfix `pl ` (z. B. `pl stromrechnung`) werden
nur Paperless-Treffer gezeigt. Zusatzaktionen: „Im Dateimanager zeigen“ und
„In Paperless öffnen“.

### Zugriff für Claude (MCP-Server)

`mko-paperless mcp` stellt Paperless als Werkzeugsatz für Claude bereit
(wird von der Claude-Seitenleiste automatisch eingebunden):
`suchen`, `filtern`, `posteingang`, `dokument` (inkl. OCR-Text), `merkmale`,
`bearbeiten` (Titel, Korrespondent, Typ, Datum, Tags, erledigt), `notiz`,
`hochladen`. Neue Korrespondenten/Tags legt Claude nur mit ausdrücklichem
`anlegen` an.

### Benachrichtigungen

Neue Dokumente im Posteingang erscheinen als Benachrichtigung mit Vorschaubild
und den Knöpfen **Öffnen**, **Erledigt** und **In Paperless**. Ab vier neuen
Dokumenten gibt es eine Sammelmeldung.

## Einrichtung

```sh
sudo apt install ./mko-paperless_0.2.0_all.deb
mko-paperless setup        # oder im Startmenü: „Paperless einrichten“
```

Abgefragt werden die Adresse (z. B. `http://paperless.lan:8000`) und die
Anmeldung (API-Token oder Benutzer + Passwort; das Passwort wird nicht
gespeichert, nur das daraus erzeugte Token). Das Token liegt im
KDE-Schlüsselbund. Danach laufen die Dienste automatisch bei jeder Anmeldung.

Paperless muss vom Rechner aus erreichbar sein, also im Heimnetz oder über VPN.

## Konfiguration

`~/.config/mko-os/paperless.toml`

```toml
url = "http://paperless.lan:8000"
mountpoint = "/home/benutzer/Paperless"
allow_delete = false        # true: Löschen in "Alle Dokumente" -> Paperless-Papierkorb
poll_interval = 60          # Sekunden zwischen Posteingangs-Abfragen
notify = true
cache_ttl = 30              # Sekunden, wie lange Ordnerinhalte zwischengespeichert werden
verify_tls = true
runner_require_prefix = false   # true: KRunner sucht nur mit "pl "
```

## Befehle

| Befehl | Zweck |
|---|---|
| `mko-paperless setup [--gui]` | Verbindung einrichten |
| `mko-paperless status` | Verbindung und Laufwerk prüfen |
| `mko-paperless search <Begriff>` | Volltextsuche im Terminal |
| `mko-paperless open <ID> [--web\|--folder]` | Dokument öffnen |
| `mko-paperless browse` | Laufwerk im Dateimanager öffnen |
| `mko-paperless mount [Pfad]` | Laufwerk von Hand einhängen |
| `mko-paperless mcp` | MCP-Server für Claude (stdio) |

Dienste: `systemctl --user status mko-paperless-fs mko-paperless-notify`
Protokoll: `journalctl --user -u mko-paperless-fs -f`

## Entwicklung

```sh
python3 tests/mock_paperless.py 8765 &          # Paperless-Nachbau für Tests
export PAPERLESS_URL=http://127.0.0.1:8765 PAPERLESS_TOKEN=testtoken
python3 -m mko_paperless mount /tmp/pl      # Laufwerk testen
dbus-run-session -- python3 tests/dbus_check.py   # KRunner + Benachrichtigungen
packaging/build-deb.sh                         # Paket nach dist/
```

## Änderungen

- **0.2.2** – Adresse wird tolerant gelesen (z. B. „http//…“, fehlendes http://).
- **0.2.1** – Neuere Paperless-Server, die alte API-Versionen ablehnen („Invalid version in Accept header“, HTTP 406):
  passende Version wird automatisch ausgehandelt.
- **0.2.0** – MCP-Server für Claude; Laufwerk übersteht Abstürze (hängender Mount wird gelöst,
  unerwartete Fehler werden gemeldet statt das Laufwerk zu beenden).
- **0.1.0** – Erste Fassung: Laufwerk, Tag-Bearbeitung per Dateiattribut,
  KRunner-Suche, Posteingang-Benachrichtigungen, Debian-Paket.
