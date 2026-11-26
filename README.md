# MKO OS

Ein Linux-System auf Basis von **Debian 13 „trixie“** mit **KDE Plasma**, in das die täglich genutzten Dienste
direkt eingebaut sind – ohne extra Apps oder Browser-Tabs.

## Bausteine

| Paket | Was es tut |
|---|---|
| [mko-claude](claude-desktop/) | Claude als Seitenleiste (Super+C) mit Freigabe-Karten; Zugriff auf Dateien, Paperless, E-Mail, Fotos, Kameras |
| [mko-mail](mail-desktop/) | E-Mail ohne Mailprogramm: beliebig viele Postfächer (Strato vorbelegt), Push-Benachrichtigungen, Posteingang-Popup |
| [mko-paperless](paperless-desktop/) | Paperless-ngx als Laufwerk in Dolphin, Suche mit Alt+Leertaste, Posteingang-Benachrichtigungen |
| [mko-immich](immich-desktop/) | Immich-Fotos als Laufwerk „Fotos“, Fotosuche, „In Immich hochladen“ im Kontextmenü |
| [mko-frigate](frigate-desktop/) | Frigate-Kameras als Popup und Kamerawand, Ereignis-Benachrichtigungen mit Vorschaubild |
| [mko-vault](vault-desktop/) | Vaultwarden: Passwörter per Super+Umschalt+P oder „pw …“ in der Suche, Bitwarden-Erweiterung in Chrome |
| [mko-dashboard](dashboard-desktop/) | Haus-Anzeige mit Home-Assistant-Werten (Heizung, PV) auf einem zweiten Bildschirm |
| [mko-desktop](desktop-meta/) | Willkommensfenster für die Ersteinrichtung, Hintergrundbilder, Logo |

Sicherheit: Virenprüfung auf Abruf (Rechtsklick „Auf Viren prüfen“, Mail-Anhänge werden automatisch geprüft),
Firewall (eingehend gesperrt) und automatische Debian-Sicherheitsupdates – ohne dauerhaft laufenden Virenscanner.

Dazu auf der ISO: Google Chrome, VSCodium, LibreOffice, GIMP, FileZilla (FTP/SFTP), Remmina, OpenVPN, Docker,
Claude Code, Bitwarden-CLI, deutsche Sprache und gängige Firmware.

## ISO herunterladen und ausprobieren

1. Unter [Releases](https://github.com/kaschperle8324/MKO_OS/releases) die neueste ISO laden
   (bei mehreren Teilen erst zusammenfügen, siehe Release-Text).
2. Auf einen USB-Stick schreiben (Ventoy, balenaEtcher oder `dd`).
3. Vom Stick starten → **„MKO OS Live“**: Das System läuft vollständig vom Stick, ohne etwas auf dem Rechner zu ändern.
4. Gefällt es: **„MKO OS installieren“** auf dem Schreibtisch. Name, Benutzer, Passwort, Zeitzone und
   Festplatte werden erst dabei abgefragt.

Die ISO enthält **keine persönlichen Daten**. Alle Zugänge (Claude-Konto, Postfächer, Paperless, Immich,
Frigate, Vaultwarden, Home Assistant) werden nach der Installation im Willkommensfenster eingerichtet und im
KDE-Schlüsselbund gespeichert.

## Updates

Installierte Systeme bekommen neue Versionen der MKO-Bausteine über die signierte Paketquelle
`https://kaschperle8324.github.io/MKO_OS/apt` – also über Discover oder `sudo apt update && sudo apt full-upgrade`,
genau wie Debian-Updates. Neu installieren ist nicht nötig. Der Workflow „MKO OS Paketquelle“ veröffentlicht bei
jeder Änderung an den Paketen automatisch; ein Update kommt beim Nutzer an, sobald die Versionsnummer steigt.

## Selbst bauen

Die ISO baut GitHub Actions (Workflow „MKO OS ISO“, manuell startbar oder per Tag `v*`). Lokal auf Debian 13:

```sh
sudo apt install live-build
for s in */packaging/build-deb.sh; do (cd "$(dirname "$(dirname "$s")")" && sh packaging/build-deb.sh); done
cp */dist/*.deb iso/config/packages.chroot/
cd iso && sudo ./build.sh
```

## Lizenz

GPL-3.0 – © Markus Kaspar-Otto
