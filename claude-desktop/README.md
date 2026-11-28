# mko-claude – Claude als Seitenleiste in KDE Plasma

Version 0.3.1 · Teil von MKO OS · Lizenz GPL-3.0

**Super+C** öffnet Claude als Seitenleiste am rechten Bildschirmrand, ohne ein
Programmfenster und ohne Browser. Ein zweites Super+C, Esc oder ein Klick
daneben blendet sie wieder aus. Das Gespräch bleibt erhalten, auch über einen
Neustart hinweg.

Dahinter läuft **Claude Code** mit deinem Claude-Abo. Claude kann deshalb auf
dem Rechner arbeiten: Dateien lesen, Befehle ausführen und mit Paperless
arbeiten.

## Funktionen

- **Seitenleiste:** Antworten erscheinen schon während des Schreibens. Darunter steht, welche Werkzeuge Claude gerade nutzt (✓ fertig, ✗ Fehler).
- **Freigaben:** Bevor Claude etwas verändert (Befehl ausführen, Datei schreiben, Paperless ändern), erscheint eine Karte mit **Erlauben**, **Immer erlauben** (für dieses Gespräch) und **Ablehnen**. Nur lesende Werkzeuge laufen ohne Rückfrage.
- **E-Mail eingebaut:** Ist `mko-mail` eingerichtet, kann Claude in allen Postfächern suchen und Mails lesen. Antworten legt Claude als Entwurf an und öffnet sie dir zur Prüfung im Schreibfenster. Sofort senden geht nur nach Freigabe.
- **Paperless eingebaut:** Ist `mko-paperless` eingerichtet, kann Claude suchen, Dokumente samt OCR-Text lesen, Merkmale setzen, Notizen anhängen und hochladen.
- **Dateien anhängen:** über die Büroklammer oder per Drag-and-drop auf die Seitenleiste.
- **Dolphin-Kontextmenü „Claude“:**
  - Claude fragen …
  - Zusammenfassen
  - Erklären: Was muss ich tun?
  - Bei PDFs und Bildern zusätzlich: Mit Claude in Paperless ablegen
- **Symbol im Systemabschnitt:** Ein Klick öffnet und schließt die Seitenleiste. Das Menü bietet „Neues Gespräch“ und „Beenden“.
- **Tasten:**

  | Taste | Wirkung |
  |---|---|
  | Enter | senden |
  | Umschalt+Enter | neue Zeile |
  | Esc | Anhang entfernen, Antwort stoppen oder ausblenden |
  | Strg+N | neues Gespräch |

  Mit der Stecknadel bleibt die Seitenleiste offen, wenn du woanders hinklickst.

## Einrichtung

```sh
sudo apt install ./mko-claude_0.3.1_all.deb
```

Beim ersten Öffnen prüft die Seitenleiste zwei Dinge:

1. **Claude Code fehlt:** Die Schaltfläche „Claude Code installieren“ startet das offizielle Installationsskript in einem Terminal.
2. **Nicht angemeldet:** Die Schaltfläche „Bei Claude anmelden“ führt `claude auth login` aus. Die Anmeldung mit deinem Claude-Konto läuft im Browser.

Super+C ist nach der nächsten Anmeldung aktiv. Bis dahin öffnet das Symbol im
Systemabschnitt die Seitenleiste. Das Tastenkürzel lässt sich unter
Systemeinstellungen → Tastatur → Kurzbefehle → Claude ändern.

## Konfiguration

`~/.config/mko-os/claude.toml` (alle Angaben optional):

```toml
width = 460                    # Breite der Seitenleiste in Pixeln
model = ""                     # leer = Standardmodell von Claude Code
hide_on_focus_loss = true
workdir = "~"                  # Arbeitsverzeichnis von Claude
allowed_tools = ["Read", "Glob", "Grep", "WebSearch", "WebFetch", "TodoWrite",
                 "mcp__paperless__suchen", "mcp__paperless__filtern", "mcp__paperless__posteingang",
                 "mcp__paperless__dokument", "mcp__paperless__merkmale",
                 "mcp__mail__konten", "mcp__mail__posteingang", "mcp__mail__suchen", "mcp__mail__lesen"]
extra_args = []                # zusätzliche Argumente für claude
```

Weitere MCP-Server, zum Beispiel für Home Assistant, trägst du in
`~/.config/mko-os/claude-mcp.json` ein. Das Format ist dasselbe wie bei
`--mcp-config` (`{"mcpServers": {…}}`).

## Befehle

| Befehl | Zweck |
|---|---|
| `mko-claude` / `mko-claude toggle` | ein-/ausblenden |
| `mko-claude show` / `hide` | öffnen / ausblenden |
| `mko-claude ask --files a.pdf --action zusammenfassen` | mit Dateien und Auftrag öffnen (`fragen`, `zusammenfassen`, `erklaeren`, `paperless`) |
| `mko-claude ask --prompt "…"` | Frage direkt stellen |
| `mko-claude quit` | beenden |

## Technik

- Es läuft immer nur eine Instanz. Weitere Aufrufe schicken ihren Befehl über einen lokalen Socket an sie.
- Claude Code läuft als `claude --print --input-format stream-json --output-format stream-json --permission-prompt-tool stdio`. Freigaben kommen als `can_use_tool`-Anfragen und werden in der Seitenleiste beantwortet.
- Unter Wayland dürfen Programme ihre Fensterposition nicht selbst setzen. Deshalb legt die Seitenleiste eine KWin-Fensterregel an (`kwinrulesrc`, Gruppe `mko-claude`) und passt sie an die Bildschirmgröße an.

## Entwicklung

```sh
QT_QPA_PLATFORM=offscreen MKO_CLAUDE_BIN=$PWD/tests/fake_claude.py python3 tests/ui_check.py
packaging/build-deb.sh
```

`tests/fake_claude.py` ahmt Claude Code nach: Antworten, Werkzeuge, Freigaben
und fehlende Anmeldung. So lässt sich die Oberfläche ohne Konto testen.

## Änderungen

- **0.4.0** – Breite einstellbar: linken Rand ziehen, Umschalter breit/schmal (auch Doppelklick auf den Rand), Strg+Plus/Minus, Strg+0 = Standard; die Breite bleibt gespeichert. Antworten brechen jetzt überall sauber um – auch lange Pfade, Adressen, Wörter und Code-Zeilen.
- **0.3.2** – Erkennt, ob es auf MKO OS oder Kubuntu läuft, und sagt Claude das passende System.
- **0.3.1** – Vaultwarden-Werkzeuge (nur Namen; Passwörter nie sichtbar, Kopieren nur nach Freigabe).
- **0.3.0** – Immich- und Frigate-Werkzeuge werden automatisch eingebunden, sobald eingerichtet.
- **0.2.0** – E-Mail-Werkzeuge (mko-mail) werden automatisch eingebunden; lesende Mail-Werkzeuge ohne Rückfrage.
- **0.1.0** – Erste Fassung: Seitenleiste, Freigabe-Karten, Paperless-Anbindung, Dolphin-Kontextmenü, Symbol im Systemabschnitt.
