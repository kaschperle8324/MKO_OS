# mko-mail – E-Mail als Teil des KDE-Desktops

Version 0.1.1 · Teil von MKO OS · Lizenz GPL-3.0

E-Mail ohne eigenes Mailprogramm. Ein Hintergrunddienst hält alle Postfächer
per IMAP IDLE (Push) im Blick. Neue Mails erscheinen sofort als
Systembenachrichtigung, und der Posteingang öffnet sich als Popup aus der
Taskleiste.

## Funktionen

- **Mehrere Postfächer.** Bei Strato reichen Adresse und Passwort, die Server
  sind vorbelegt. Dazu kommen Google (mit App-Passwort) und beliebige eigene
  Server. Weitere Postfächer lassen sich jederzeit hinzufügen. Jedes Postfach
  bekommt eine eigene Farbe.
- **Benachrichtigung bei neuer Mail** mit Absender, Betreff und den Knöpfen
  **Öffnen**, **Gelesen**, **Archivieren** und **Mit Claude antworten**. Ab vier
  neuen Mails gibt es eine Sammelmeldung.
- **Symbol in der Taskleiste** mit der Zahl ungelesener Mails. Der Tooltip
  zeigt sie je Postfach.
- **Posteingang-Popup** (Klick aufs Symbol):
  - gemeinsamer Posteingang aller Postfächer oder Filter auf ein Postfach
  - Lesen ohne externe Inhalte (keine Tracking-Pixel)
  - Antworten, Allen antworten, Weiterleiten (mit Anhängen), Archivieren, Löschen
  - Rechtsklick auf eine Mail: als gelesen oder ungelesen markieren
- **Anhänge:** öffnen, speichern oder **in Paperless ablegen** (PDFs und Bilder). Ist ClamAV installiert, werden
  Anhänge beim Öffnen der Mail im Hintergrund geprüft (✓ bzw. ⚠ am Anhang); bei einem Fund warnt die Anzeige vor dem
  Öffnen, und der Assistent bekommt solche Anhänge nicht.
- **Schreiben** in einem eigenen Fenster:
  - Absender aus allen Postfächern wählbar
  - Anhänge, Signatur je Postfach, Entwürfe
  - Kopie landet in „Gesendet“
  - Beantwortete Mails werden markiert
- **Standardprogramm für `mailto:`-Links:** Ein Klick auf eine Mailadresse im
  Browser öffnet direkt das Schreibfenster.
- **Claude:** Die Seitenleiste (`mko-claude`) kann in allen Postfächern
  suchen, Mails lesen, Antworten als Entwurf vorbereiten, archivieren und
  Anhänge speichern oder nach Paperless schicken. Sofort senden geht nur nach
  Freigabe in der Seitenleiste.
- **Ordner:** Archiv, Gesendet, Entwürfe und Papierkorb erkennt der Dienst
  automatisch (IMAP SPECIAL-USE oder übliche deutsche und englische Namen).
  Fehlt ein Archivordner, wird „Archiv“ angelegt.

## Einrichtung

```sh
sudo apt install ./mko-mail_0.1.1_all.deb
```

Nach der nächsten Anmeldung startet der Dienst automatisch. Ohne Postfach
öffnet sich „Postfach hinzufügen“ von selbst. Später findest du es im Menü
des Taskleisten-Symbols oder unter `mko-mail add`. Vor dem Speichern wird
die Verbindung zu IMAP und SMTP geprüft. Passwörter liegen im
KDE-Schlüsselbund.

Voreinstellungen:

| Anbieter | Posteingang | Postausgang |
|---|---|---|
| Strato | imap.strato.de:993 SSL/TLS | smtp.strato.de:465 SSL/TLS |
| Google | imap.gmail.com:993 SSL/TLS | smtp.gmail.com:465 SSL/TLS (App-Passwort nötig) |

Damit Links auf Mailadressen das Schreibfenster öffnen, stellst du unter
Systemeinstellungen → Standardanwendungen → E-Mail-Programm „E-Mail
schreiben“ ein.

### Google-Kalender

Die Termine erscheinen im Uhr-Popup der Taskleiste. Dafür sorgt die
KDE-PIM-Ressource „Google Groupware“, die einmal eingerichtet werden muss:

1. Den Menüpunkt „Google-Kalender verbinden …“ wählen. Das Kalenderprogramm öffnet sich.
2. In der Kalenderliste Rechtsklick → „Kalender hinzufügen“ → „Google Groupware“.
3. Mit dem Google-Konto anmelden.
4. Rechtsklick auf die Uhr → „Digitaluhr einrichten“ → „Kalender“ → „PIM-Ereignisse“ einschalten.

Dafür werden `korganizer` und `kdepim-addons` gebraucht.

## Befehle

| Befehl | Zweck |
|---|---|
| `mko-mail` | Posteingang ein- und ausblenden |
| `mko-mail compose --to … --subject … --body … --attach datei.pdf` | Schreibfenster öffnen |
| `mko-mail add` / `mko-mail accounts` | Postfach hinzufügen / verwalten |
| `mko-mail list [--account adresse] [--unread]` | Posteingang im Terminal |
| `mko-mail mcp` | MCP-Server für Claude (stdio) |
| `mko-mail quit` | Dienst beenden |

Die Postfächer stehen in `~/.config/mko-os/mail.toml`, eines je `[[account]]`.

## Entwicklung

```sh
python3 tests/mailserver.py start && python3 tests/mailserver.py seed   # Dovecot + SMTP lokal
QT_QPA_PLATFORM=offscreen MKO_MAIL_PASSWORD=geheim python3 tests/ui_check.py
packaging/build-deb.sh
```

## Änderungen

- **0.1.1** – Virenprüfung der Anhänge mit ClamAV (falls installiert).
- **0.1.0** – Erste Fassung: Postfächer mit Strato- und Google-Vorlage, IMAP-IDLE-Push,
  Benachrichtigungen mit Aktionen, Posteingang-Popup, Schreiben/Antworten/Weiterleiten,
  Anhänge nach Paperless, MCP-Server für Claude, `mailto:`-Handler.
