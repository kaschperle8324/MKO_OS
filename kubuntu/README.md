# MKO-Bausteine auf Kubuntu

Alles, was MKO OS zusätzlich zu Debian mitbringt, lässt sich auch auf ein vorhandenes **Kubuntu** installieren –
ohne Neuinstallation. So können beide Varianten direkt verglichen werden.

```sh
curl -fsSL https://kaschperle8324.github.io/MKO_OS/kubuntu.sh | sudo bash
```

Das Skript fragt nach den Zusatzprogrammen und installiert dann:

| Was | Woher |
|---|---|
| Claude-Seitenleiste, E-Mail, Paperless, Immich, Frigate, Vaultwarden, Haus-Anzeige, Willkommensfenster, Virenprüfung auf Abruf | MKO-OS-Paketquelle (signiert, dieselben Pakete wie auf MKO OS) |
| Claude Code, Bitwarden-CLI | npm, wöchentlich automatisch aktualisiert |
| ClamAV, automatische Sicherheitsupdates | Kubuntu |
| auf Wunsch: Google Chrome, VSCodium, LibreOffice, GIMP, FileZilla, Remmina, OpenVPN, Docker, Firewall | Kubuntu bzw. Herstellerquellen |

Danach einmal ab- und wieder anmelden: Das Fenster **„Willkommen bei MKO OS“** öffnet sich, dort werden die
Zugänge eingerichtet (gespeichert im KDE-Schlüsselbund). Das vorhandene Aussehen (Design, Hintergrund) bleibt
auf Kubuntu unverändert; der MKO-Hintergrund steht unter *Hintergrundbild* zur Auswahl.

## Optionen

```sh
curl -fsSL https://kaschperle8324.github.io/MKO_OS/kubuntu.sh | sudo bash -s -- --alles          # alles ohne Rückfrage
curl -fsSL https://kaschperle8324.github.io/MKO_OS/kubuntu.sh | sudo bash -s -- --nur-bausteine  # keine Zusatzprogramme
curl -fsSL https://kaschperle8324.github.io/MKO_OS/kubuntu.sh | sudo bash -s -- --entfernen      # wieder entfernen
```

Beim Entfernen bleiben Zusatzprogramme, `~/.config/mko-os` und die Einträge im Schlüsselbund erhalten.

## Voraussetzungen

- **Kubuntu 25.04 oder neuer** (getestet mit 26.04 LTS). Kubuntu 24.04 hat noch Plasma 5 und kein PySide6 –
  dort vorher `sudo do-release-upgrade`.
- 64-Bit-PC (amd64).

## Updates

Die Paketquelle bleibt eingetragen; neue Versionen kommen über Discover oder
`sudo apt update && sudo apt full-upgrade` – gleichzeitig mit MKO OS.
