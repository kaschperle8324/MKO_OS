# mko-frigate – Kameras als Teil des KDE-Desktops

Version 0.1.0 · Teil von MKO OS · Lizenz GPL-3.0

Bindet einen selbst betriebenen [Frigate NVR](https://frigate.video) direkt in
den Desktop ein. Ein Hintergrunddienst legt ein Kamerasymbol in die
Taskleiste, meldet erkannte Personen oder Autos als Systembenachrichtigung mit
Vorschaubild und zeigt alle Kameras mit einem Klick als Popup. Getestet mit
der API von Frigate 0.14 bis 0.16.

## Funktionen

- **Kamera-Popup** (Klick aufs Symbol in der Taskleiste) unten rechts:
  - alle Kameras im Raster, Bilder werden etwa alle 1,5 Sekunden erneuert –
    nur solange das Popup offen ist
  - Klick auf eine Kamera öffnet die **Einzelansicht** mit größerem, schneller
    erneuertem Bild und den Knöpfen **Live-Stream**, **Ereignisse**,
    **Schnappschuss** und **Zurück**
  - **Live-Stream** öffnet den RTSP-Restream von Frigate (go2rtc,
    `rtsp://<server>:8554/<kamera>`) in mpv; ohne mpv mit der Standardanwendung
  - **Ereignisse** je Kamera mit Vorschaubild, Objekt, Zeit, Dauer, Treffer
    und Zonen; dazu **Clip abspielen** (wird in den Cache geladen) und
    **Schnappschuss speichern** (nach `~/Bilder/Frigate`)
- **Benachrichtigungen** bei neuen Ereignissen, gefiltert nach Objekten
  (Standard: Person und Auto) und Kameras, mit dem Vorschaubild des Ereignisses
  und den Knöpfen **Ansehen**, **Clip** und **Stumm 1 h**. Ab vier Ereignissen
  auf einmal gibt es eine Sammelmeldung.
- **Kamerawand** im Vollbild für einen zweiten Bildschirm: dunkle Ansicht mit
  Uhr, Raster passt sich der Kamerazahl an, Klick auf eine Kamera zeigt nur
  diese, `Esc` schließt.
- **Assistent:** Der Assistent in der Seitenleiste (`mko-claude`) kann über
  den MCP-Server Kameras und Ereignisse abfragen und Schnappschüsse ablegen.
- **Ohne Einrichtung** startet der Dienst still; das Popup zeigt dann einen
  Hinweis mit dem Knopf „Frigate einrichten …“. Der Dialog öffnet sich nie
  von selbst.

## Einrichtung

```sh
sudo apt install ./mko-frigate_0.1.0_all.deb
mko-frigate setup --gui        # oder ohne --gui: Fragen im Terminal
```

Nach der nächsten Anmeldung startet der Dienst automatisch. Vor dem Speichern
wird die Verbindung geprüft. Das Passwort liegt im KDE-Schlüsselbund (Dienst
„mko-frigate“), ohne Schlüsselbund in `~/.config/mko-os/frigate.secret` mit
Rechten 0600.

| Zugang | Adresse | Anmeldung |
|---|---|---|
| Authentifiziert (empfohlen) | `https://frigate.example:8971` | Benutzer und Passwort aus Frigate |
| Intern, ohne Anmeldung | `http://frigate.example:5000` | keine – Port nur im eigenen Netz freigeben |
| Mit Token | `https://frigate.example:8971` | Benutzer leer lassen, Token als Passwort |

Bei einem selbstsignierten Zertifikat auf Port 8971 „TLS-Zertifikat prüfen“
abschalten. Für den Live-Stream muss Port 8554 (go2rtc) erreichbar sein.

Die Einstellungen stehen in `~/.config/mko-os/frigate.toml`:

```toml
url = "https://frigate.example:8971"
verify_tls = true
username = "betrachter"
rtsp_base = ""                    # leer = rtsp://frigate.example:8554
notify = true
notify_labels = ["person", "car"] # leer = alle Objekte
notify_cameras = []               # leer = alle Kameras, z. B. ["haustuer", "garten"]
poll_interval = 10                # Sekunden
snapshot_dir = ""                 # leer = ~/Bilder/Frigate
```

## Befehle

| Befehl | Zweck |
|---|---|
| `mko-frigate` | Kamera-Popup ein- und ausblenden |
| `mko-frigate daemon` | Dienst starten (Autostart) |
| `mko-frigate setup [--gui]` | Verbindung einrichten |
| `mko-frigate wall [--screen HDMI-A-1]` | Kamerawand im Vollbild (ohne Angabe: zweiter Bildschirm) |
| `mko-frigate wall --list-screens` | erkannte Bildschirme anzeigen |
| `mko-frigate events [--camera haustuer] [--label person] [-n 20]` | letzte Ereignisse im Terminal |
| `mko-frigate mcp` | MCP-Server (stdio) |
| `mko-frigate quit` | Dienst beenden |

### MCP-Werkzeuge

| Werkzeug | Zweck |
|---|---|
| `kameras` | Kameras mit Status und Live-Stream-Adresse |
| `ereignisse` | Ereignisse nach `zeitraum` („heute“, „gestern“, „2h“, „7d“), `kamera`, `objekt` |
| `ereignis` | Einzelheiten zu einem Ereignis |
| `schnappschuss` | aktuelles Kamerabild (oder Ereignis-Schnappschuss) als Datei speichern |
| `statistik` | Version, Laufzeit, Bilder/s, Erkennungsdauer, Speicherplatz |

## Entwicklung

```sh
python3 tests/mock_frigate.py --port 18971 --interval 0 &    # Frigate-Nachbau (admin / geheim)
export FRIGATE_URL=http://127.0.0.1:18971 FRIGATE_USER=admin FRIGATE_PASSWORD=geheim
QT_QPA_PLATFORM=offscreen python3 tests/ui_check.py          # Bildschirmfotos nach /tmp/frigate_*.png
python3 tests/mcp_check.py
packaging/build-deb.sh
```

## Änderungen

- **0.1.0** – Erste Fassung: Kamera-Popup mit Raster, Einzelansicht und
  Ereignissen, Live-Stream über mpv, Clips und Schnappschüsse,
  Benachrichtigungen mit Vorschaubild, Kamerawand, MCP-Server.
