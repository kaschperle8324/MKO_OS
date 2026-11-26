# mko-immich – Immich-Fotos im KDE-Desktop

Version 0.1.0 · Teil von MKO OS · Lizenz GPL-3.0

Die eigene Immich-Fotosammlung wird zu einem Teil des Systems: Alben, Zeitachse
und Favoriten liegen als Laufwerk im Dateimanager, Fotos werden per Kopieren
hochgeladen, die Plasma-Suche findet Bilder nach Inhalt („Strand“, „Hund im
Schnee“), und im Kontextmenü von Dolphin gibt es „In Immich hochladen“. Die
Immich-Webseite muss dafür nicht geöffnet werden.

## Funktionen

### Immich als Laufwerk (`~/Fotos`)

```
Fotos/
├── Alben/<Albumname>/          Fotos eines Albums
├── Zeitachse/<JJJJ>/<MM Monat>/ alle Fotos nach Aufnahmedatum (nur lesen)
├── Favoriten/
├── Suche/<Suchbegriff>/        Ordner anlegen = inhaltliche Suche speichern
└── Hochladen/                  Datei hineinziehen = Upload
```

Dateinamen: `2025-07-14 18-02-44 IMG_0002.jpg` (Aufnahmezeit in Ortszeit,
Originalname). Nur wenn zwei Fotos in einem Ordner gleich heißen würden, kommt
ein kurzes Kennzeichen aus der Immich-ID dazu: `… IMG_0101 [a1b2c3d4].jpg`.

| Aktion im Dateimanager | Wirkung in Immich |
|---|---|
| Datei öffnen | Original wird geladen (lokal zwischengespeichert, Größe begrenzt) |
| Datei nach `Alben/X` kopieren | Upload und zu Album X hinzufügen |
| Datei nach `Favoriten` kopieren | Upload und als Favorit markieren |
| Datei nach `Hochladen` kopieren | nur Upload |
| Foto aus der `Zeitachse` nach `Alben/X` kopieren oder verschieben | zu Album X hinzufügen (Immich erkennt das vorhandene Foto, es entsteht kein Duplikat) |
| Verschieben `Alben/A` → `Alben/B` | aus A entfernen, zu B hinzufügen |
| Neuer Ordner in `Alben` | Album wird in Immich angelegt |
| Löschen in `Alben/X` | nur aus Album X entfernen, das Foto bleibt |
| Löschen in `Zeitachse` / `Favoriten` | nur mit `allow_delete = true`, landet im Immich-Papierkorb |
| Neuer Ordner in `Suche` | gespeicherte Suche (höchstens 200 Treffer) |
| Ordner in `Suche` löschen | gespeicherte Suche entfernen |

Vorhandene Fotos sind schreibgeschützt. Alben werden über das Laufwerk nie
gelöscht. Uploads aus Dolphin (`bild.jpg.part` → `bild.jpg`) werden erst nach
dem Umbenennen übertragen; das Dateidatum wird übernommen, falls das Foto
kein EXIF-Datum hat.

Ort, Kamera und Beschreibung stehen im Kommentarfeld der Datei
(`user.xdg.comment`), der Link zur Weboberfläche in `user.xdg.origin.url`.

Hinweis: Vorschaubilder in Dolphin werden aus den Originalen erzeugt, die dafür
geladen werden. Der Zwischenspeicher unter `~/.cache/mko-immich` ist auf
`cache_size_mb` begrenzt.

### Suche in KRunner

`Alt+Leertaste` und tippen: Immich-Treffer erscheinen mit Aufnahmezeit, Ort und
Album. Mit dem Präfix `foto ` (z. B. `foto sonnenuntergang`) werden nur
Immich-Treffer gezeigt; gesucht wird ab drei Buchstaben. Enter öffnet das Foto,
Zusatzaktionen: „Im Dateimanager zeigen“ und „In Immich öffnen“.

Gesucht wird inhaltlich über die Bilderkennung von Immich. Ist sie auf dem
Server abgeschaltet, wird ersatzweise im Dateinamen gesucht.

### Kontextmenü in Dolphin

Rechtsklick auf Bilder oder Videos → **Immich** → **In Immich hochladen** oder
**In Immich-Album hochladen …** (Album auswählen oder neu anlegen). Bereits
vorhandene Fotos erkennt Immich und legt sie nicht doppelt an.

### Werkzeugsatz für den Assistenten (MCP-Server)

`mko-immich mcp` stellt die Fotosammlung als Werkzeugsatz für den Assistenten
in der Seitenleiste (mko-claude) bereit:
`suchen`, `filtern` (Zeitraum, Favoriten), `alben`, `album_inhalt`, `foto`
(Aufnahmezeit, Ort, EXIF, erkannte Personen, Alben, lokaler Pfad im Laufwerk),
`herunterladen` (nach `~/Bilder` oder in einen Ordner), `hochladen` (Dateien
oder Ordner, optional in ein Album), `album_anlegen`, `zu_album_hinzufuegen`.
Gelöscht wird über diesen Weg nichts.

Einbindung: `{"mcpServers": {"immich": {"command": "mko-immich", "args": ["mcp"]}}}`

## Einrichtung

```sh
sudo apt install ./mko-immich_0.1.0_all.deb
mko-immich setup        # oder im Startmenü: „Immich einrichten“
```

Abgefragt werden die Adresse (z. B. `https://immich.example`) und ein
API-Schlüssel. Den legst du in Immich an: **Profilbild → Kontoeinstellungen →
API-Schlüssel → Neuer API-Schlüssel**. Am einfachsten mit der Berechtigung
`all`; mindestens nötig sind Lesen/Hochladen/Herunterladen von Fotos, Alben
lesen/anlegen/ändern und Suchen (für `allow_delete` zusätzlich Löschen).

Der Schlüssel liegt im KDE-Schlüsselbund (ohne Schlüsselbund in
`~/.config/mko-os/immich.key`, nur für dich lesbar). Danach wird das Laufwerk
bei jeder Anmeldung automatisch eingehängt; „Fotos (Immich)“ im Startmenü und
der Eintrag unter *Orte* in Dolphin öffnen es.

Immich muss vom Rechner aus erreichbar sein, also im Heimnetz, über VPN oder
eine eigene Domain. Getestet gegen die API von Immich v1.135, v2.x und v3.x.

## Konfiguration

`~/.config/mko-os/immich.toml`

```toml
url = "https://immich.example"
mountpoint = "/home/benutzer/Fotos"
allow_delete = false        # true: Löschen in Zeitachse/Favoriten -> Immich-Papierkorb
cache_ttl = 60              # Sekunden, wie lange Ordnerinhalte zwischengespeichert werden
cache_size_mb = 2048        # Obergrenze für zwischengespeicherte Originale
verify_tls = true
runner_require_prefix = false   # true: KRunner sucht nur mit "foto "
```

Für Tests und Skripte haben `IMMICH_URL`, `IMMICH_API_KEY` und
`MKO_IMMICH_MOUNT` Vorrang vor der Datei.

## Befehle

| Befehl | Zweck |
|---|---|
| `mko-immich setup [--gui]` | Verbindung einrichten |
| `mko-immich status` | Verbindung und Laufwerk prüfen |
| `mko-immich search <Begriff>` | inhaltliche Suche im Terminal |
| `mko-immich upload <Dateien/Ordner> [--album Name]` | hochladen, optional in ein Album (wird bei Bedarf angelegt) |
| `mko-immich open <ID> [--web\|--folder]` | Foto öffnen |
| `mko-immich browse` | Laufwerk im Dateimanager öffnen |
| `mko-immich mount [Pfad]` | Laufwerk von Hand einhängen |
| `mko-immich runner` | KRunner-Dienst (startet sonst automatisch über D-Bus) |
| `mko-immich mcp` | MCP-Server (stdio) |

Dienst: `systemctl --user status mko-immich-fs`
Protokoll: `journalctl --user -u mko-immich-fs -f`

## Entwicklung

```sh
python3 tests/mock_immich.py 8766 &               # Immich-Nachbau (--style v3 für die v3-API)
export IMMICH_URL=http://127.0.0.1:8766 IMMICH_API_KEY=testkey
python3 tests/fs_check.py /tmp/fotos              # Laufwerk: Ordner, Lesen, Hochladen, Alben, Suche
dbus-run-session -- python3 tests/dbus_check.py   # KRunner
python3 tests/mcp_check.py                        # MCP-Server
packaging/build-deb.sh                            # Paket nach dist/
```

Der Nachbau erzeugt kleine JPEGs in zwei Alben über zwei Jahre und bildet beide
API-Generationen nach: bis v2 (Albumantwort mit Fotos, Blättern über `page`)
und ab v3 (Albuminhalt nur über die Suche, Blättern über `cursor`).

## Änderungen

- **0.1.0** – Erste Fassung: Laufwerk mit Alben, Zeitachse, Favoriten,
  gespeicherten Suchen und Hochladen; KRunner-Suche; Dolphin-Kontextmenü;
  MCP-Server; Debian-Paket.
