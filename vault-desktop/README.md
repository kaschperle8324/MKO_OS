# mko-vault – Passwörter aus dem eigenen Vaultwarden

Version 0.1.0 · Teil von MKO OS · Lizenz GPL-3.0

Bindet einen selbst betriebenen [Vaultwarden](https://github.com/dani-garcia/vaultwarden)-Server
(Bitwarden-kompatibel) in den KDE-Desktop ein. Es gibt kein eigenes Fenster und kein eigenes
Kryptoverfahren: Die Arbeit macht die offizielle Bitwarden-CLI (`bw serve`), mko-vault ist nur
die Oberfläche drumherum.

## Funktionen

- **Schloss-Symbol in der Taskleiste.** Es zeigt, ob der Tresor gesperrt oder entsperrt ist.
  Ein Klick (oder **Meta+Umschalt+P**) öffnet das Popup unten rechts.
- **Popup:**
  - gesperrt: Feld für das Master-Passwort
  - entsperrt: Suchfeld und Trefferliste (Name, Benutzername, Adresse). Statt Favicons gibt es
    einen farbigen Anfangsbuchstaben, es wird also nichts aus dem Netz nachgeladen.
  - je Eintrag die Knöpfe **Benutzername kopieren**, **Passwort kopieren**, **TOTP kopieren**
    (falls hinterlegt) und **Seite öffnen**. Dazu gibt es Tastenkürzel: Enter oder Strg+C
    kopiert das Passwort, Strg+B den Benutzernamen, Strg+T den TOTP-Code und Strg+U öffnet die
    Seite. Mit ↑/↓ wechselt man den Eintrag.
  - Rechtsklick auf einen Eintrag öffnet dieselben Aktionen als Menü.
- **KRunner** (Alt+Leertaste): `pw strato` sucht ab zwei Buchstaben nach dem Präfix. Enter
  kopiert das Passwort. Als Zusatzaktionen gibt es Benutzername, TOTP und Seite öffnen. Ist der
  Tresor gesperrt, erscheint nur „Tresor entsperren …“, und das öffnet das Popup.
- **Zwischenablage:** Kopierte Werte landen nicht im Klipper-Verlauf und werden nach 30 Sekunden
  wieder gelöscht (siehe *Sicherheit*).
- **Automatische Sperre** nach 15 Minuten ohne Benutzung, beim Sperren des Bildschirms und vor
  Bereitschaft oder Ruhezustand.
- **Assistent in der Seitenleiste (mko-claude):** Er kann prüfen, ob der Tresor entsperrt ist,
  Einträge suchen (er sieht nur Name, Benutzername und Adresse) und ein Passwort *in die
  Zwischenablage legen lassen*. **Lesen kann er Geheimnisse nie.**
- **Google Chrome:** Die offizielle Bitwarden-Erweiterung wird per Richtlinie installiert. Die
  Adresse des eigenen Servers trägst du einmal in der Erweiterung ein (Anmeldeseite →
  „Angemeldet bei: selbst gehostet“).

## Einrichtung

```sh
sudo apt install ./mko-vault_0.1.0_all.deb
```

Die Bitwarden-CLI gibt es nicht als Debian-Paket. Im MKO OS-Abbild ist sie bereits enthalten.
Sonst installierst du sie so:

```sh
sudo npm install -g @bitwarden/cli
```

Fehlt `bw`, bieten das Popup und der Einrichtungsdialog „Bitwarden-CLI installieren“ an. Der
Knopf öffnet ein Konsole-Fenster mit genau diesem Befehl.

Danach rufst du **„Vaultwarden einrichten“** auf (Startmenü oder `mko-vault setup`):

1. **Server** (z. B. `https://vault.example`) und **E-Mail** (z. B. `name@beispiel.de`) eintragen.
2. Die Anmeldung auswählen:
   - **Im Terminal** (Standard): Es öffnet sich ein Konsole-Fenster mit `bw login`. Dort gibst
     du das Master-Passwort und gegebenenfalls den Zwei-Faktor-Code ein (Authenticator-App,
     E-Mail, YubiKey). Die Ausgabe von `bw login` mit dem Sitzungsschlüssel wird verworfen, und
     direkt danach wird gesperrt.
   - **Mit API-Schlüssel** (`client_id` / `client_secret` aus dem Web-Tresor unter
     Einstellungen → Sicherheit → Schlüssel): Hier ist keine Zwei-Faktor-Abfrage nötig. Das
     `client_secret` liegt nur im KDE-Schlüsselbund. Entsperren musst du trotzdem mit dem
     Master-Passwort.
3. Zum Entsperren klickst du auf das Schloss in der Taskleiste oder drückst Meta+Umschalt+P.

Die Bitwarden-CLI bekommt ein eigenes Datenverzeichnis, `~/.config/mko-os/bitwarden-cli`
(`BITWARDENCLI_APPDATA_DIR`). Ein eventuell selbst genutztes `bw` bleibt davon unberührt.

### Einstellungen

`~/.config/mko-os/vault.toml` (wird vom Einrichtungsdialog geschrieben):

| Schlüssel | Standard | Bedeutung |
|---|---|---|
| `url` | – | Adresse des Vaultwarden-Servers |
| `email` | – | Konto |
| `autolock_minutes` | 15 | Sperre nach so vielen Minuten ohne Benutzung (0 = nur Bildschirmsperre/Ruhezustand) |
| `clipboard_seconds` | 30 | Zwischenablage nach so vielen Sekunden leeren (0 = nie) |
| `lock_on_screenlock` | true | beim Sperren des Bildschirms sperren |
| `lock_on_suspend` | true | vor Bereitschaft/Ruhezustand sperren |
| `confirm_show` | true | `mko-vault get … --show` erst nach Rückfrage im Dialog |
| `login_method` | terminal | `terminal` oder `apikey` |
| `client_id` | – | nur für `apikey` |

## Befehle

| Befehl | Zweck |
|---|---|
| `mko-vault` / `mko-vault toggle` | Popup ein- und ausblenden (startet bei Bedarf den Dienst) |
| `mko-vault daemon` | Dienst starten (Autostart). Er öffnet von sich aus keine Fenster. |
| `mko-vault setup` | Einrichtungsdialog |
| `mko-vault unlock` | im Terminal entsperren (Passwortabfrage ohne Echo), sonst Popup |
| `mko-vault lock` | sofort sperren |
| `mko-vault sync` | mit dem Server abgleichen |
| `mko-vault status` | gesperrt, entsperrt oder nicht angemeldet |
| `mko-vault search strato` | Einträge auflisten (ohne Passwörter) |
| `mko-vault get "Strato Webmail" [--field password\|username\|totp]` | in die Zwischenablage kopieren |
| `mko-vault get "Strato Webmail" --show` | im Terminal ausgeben, erst nach Bestätigung im Dialog |
| `mko-vault mcp` | MCP-Server für den Assistenten in der Seitenleiste (stdio) |
| `mko-vault quit` | Dienst beenden (sperrt vorher) |

Passt bei `get` mehr als ein Eintrag, listet der Befehl die Treffer auf und kopiert nichts.

## Sicherheit

**Master-Passwort.** Es wird nie gespeichert, nie protokolliert und nie als Befehlszeilenargument
übergeben. Das Eingabefeld wird sofort nach dem Absenden geleert. Das Passwort geht nur einmal per
`POST /unlock` an den eigenen `bw serve`-Prozess.

**Sitzungsschlüssel.** Den entsperrten Tresorschlüssel hält allein der Kindprozess `bw serve`
im Arbeitsspeicher. Die Antwort auf `/unlock`, die den Sitzungsschlüssel enthält, wird sofort
verworfen. Es gibt kein `BW_SESSION` in einer Datei oder Umgebungsvariable. Beim Sperren,
Beenden und Abmelden wird `POST /lock` geschickt. Schlägt das fehl, wird `bw serve` beendet und
neu gestartet. Stirbt der Dienst, beendet der Kernel auch `bw serve` (`PR_SET_PDEATHSIG`).

**Wer an `bw serve` herankommt.** `bw serve` hat keine eigene Anmeldung: Wer die Schnittstelle
erreicht, kann einen entsperrten Tresor lesen. Deshalb lauscht er auf einem Unix-Socket in
`$XDG_RUNTIME_DIR/mko-vault/` (Rechte 0700, nur der eigene Benutzer). Sehr alte CLI-Versionen
ohne Unix-Socket bekommen ersatzweise `127.0.0.1` mit einem zufälligen freien Port. Andere Rechner
kommen dann nicht heran, andere Benutzer desselben Rechners schon, solange der Tresor entsperrt
ist. `bw serve` lehnt außerdem Anfragen mit `Origin`-Kopfzeile (Webseiten im Browser) und
unbekanntem `Host` ab. Der Steuer-Socket des Dienstes liegt im selben privaten Ordner.

**Zwischenablage.**

- Werte werden mit dem zusätzlichen MIME-Typ `x-kde-passwordManagerHint` = `secret`
  angeboten. Damit übernimmt Klipper sie nicht in den Verlauf.
- Unter X11 klappt das immer. Unter Wayland erlaubt KWin das Setzen der Zwischenablage nur dem
  Fenster mit Fokus. Aus dem Popup klappt es daher genauso.
- Kommt der Wunsch ohne eigenes Fenster (KRunner, Assistent, Kommandozeile), übernimmt das
  `wl-copy`, mit `--sensitive`, wenn die installierte Version das kennt (ab wl-clipboard 2.3;
  ob Klipper diesen Hinweis auswertet, ist nicht geprüft). **Einschränkung:** `wl-copy` kann den
  KDE-Hinweis nicht mitschicken. In diesem Fall kann Klipper den Wert in den Verlauf aufnehmen,
  bis er nach `clipboard_seconds` gelöscht wird. Wer das nicht möchte, kopiert aus dem Popup (Meta+Umschalt+P) oder stellt in Klipper „Verlauf
  speichern“ ab.
- Nach `clipboard_seconds` wird die Zwischenablage geleert, aber nur, wenn noch genau der
  kopierte Wert darin steht. Den Vergleich macht der Dienst im Speicher. An `wl-copy` geht der
  Wert über die Standardeingabe, nie als Argument.

**Was nie herausgegeben wird.** Suche, KRunner, Benachrichtigungen und der MCP-Server liefern
nur id, Name, Benutzername, Adressen und die Angabe „TOTP vorhanden“. Passwörter, Notizen,
Kartendaten, Identitäten und verborgene Zusatzfelder geben sie nie heraus. Kopieren lassen sich
nur Anmeldedaten-Einträge. Einträge mit „Master-Passwort erneut abfragen“ lassen sich nur im
Popup kopieren, nach erneuter Eingabe. Über KRunner, den Assistenten oder die Kommandozeile
geht das nicht.

**Der Assistent kann nie ein Geheimnis lesen.** Der MCP-Server (`mko-vault mcp`) spricht nur
mit dem Dienst. „suchen“ liefert die oben genannten unkritischen Felder. Dabei wird doppelt
gefiltert: Schon der Dienst gibt nur diese Felder heraus, und der MCP-Server lässt nur diese
Schlüssel durch. „passwort_kopieren“ legt den Wert im Dienst in die Zwischenablage und meldet
dem Assistenten nur „kopiert“. Entsperren kann er nicht, er kann nur das Entsperr-Fenster
öffnen lassen. Grenze: Ein Programm, das unter deinem Benutzerkonto beliebige Befehle ausführen
darf, kommt bei *entsperrtem* Tresor an die Daten, wie bei jedem Passwortmanager. Erlaube dem
Assistenten deshalb keine freien Shell-Befehle wie `wl-paste`, `curl --unix-socket …` oder
`mko-vault get --show`. `--show` fragt zusätzlich im Dialog nach (`confirm_show`).

**Protokolle.** Der Dienst schreibt nur Zustände („bw serve bereit“, „gesperrt
(Inaktivität)“), nie Werte. Die Tests prüfen das: Kein Testpasswort taucht in Protokoll,
Benachrichtigungen, KRunner-Treffern oder MCP-Antworten auf.

**Grenzen.** Python kann Zeichenketten nicht gezielt im Speicher überschreiben. Ein kopiertes
Passwort liegt deshalb kurz im Speicher des Dienstes, bis die Speicherbereinigung es entfernt.

## Chrome-Erweiterung

Das Paket legt `/etc/opt/chrome/policies/managed/mko-vault.json` ab (und dieselbe Datei für
Chromium unter `/etc/chromium/policies/managed/`):

```json
{"ExtensionSettings": {"nngceckbapebfimnlniiiahkandclblb": {
  "installation_mode": "normal_installed",
  "update_url": "https://clients2.google.com/service/update2/crx"}}}
```

`nngceckbapebfimnlniiiahkandclblb` ist „Bitwarden Password Manager“ im Chrome Web Store.
Mit `normal_installed` installiert Chrome die Erweiterung von selbst. Abschalten lässt sie sich,
deinstallieren nicht. Unter `chrome://policy` siehst du, ob die Richtlinie greift. Setzt eine
andere Richtlinien-Datei ebenfalls `ExtensionSettings`, gewinnt nur eine davon. Dann gehört der
Eintrag in diese Datei.

## Hinweise

- **Meta+Umschalt+P:** Bewusst nicht Meta+P, das Plasma für „Bildschirm umschalten“ verwendet. Ändern unter
  Systemeinstellungen → Tastatur → Kurzbefehle → „Passwörter (Vaultwarden)“.
- **TOTP:** Den Code liefert `bw serve`. Lehnt der Server das ab (beim offiziellen
  Bitwarden-Server nur mit Premium; Vaultwarden gibt allen Premium), berechnet mko-vault den
  Code selbst (RFC 6238, auch `otpauth://` und Steam).
- **Server wechseln:** Den Einrichtungsdialog erneut aufrufen. Bei anderem Server oder anderem
  Konto meldet er die CLI vorher ab.

## Entwicklung

```sh
# Oberfläche, Entsperren, Suche, Zwischenablage, automatische Sperre (Bildschirmfotos /tmp/vault_*.png)
dbus-run-session -- env QT_QPA_PLATFORM=offscreen python3 tests/ui_check.py
# MCP-Server: keine Geheimnisse in den Antworten
dbus-run-session -- python3 tests/mcp_check.py
# KRunner-Dienst und Kommandozeile gegen den laufenden Dienst
dbus-run-session -- python3 tests/dbus_check.py
packaging/build-deb.sh
```

Die Tests nutzen `tests/mock_bw_serve.py`, einen Nachbau der Bitwarden-CLI (`bw status`,
`config server`, `login`, `serve` mit den Endpunkten `/status`, `/unlock`, `/lock`, `/sync`,
`/list/object/items`, `/object/item/{id}`, `/object/totp/{id}`). Eingebunden wird er über
`MKO_VAULT_BW=/pfad/zu/mock_bw_serve.py`, das Master-Passwort ist `geheim`.

## Änderungen

- **0.1.0** – Erste Fassung: `bw serve` auf privatem Unix-Socket, Taskleisten-Symbol und Popup
  (Meta+Umschalt+P), KRunner „pw …“, Zwischenablage ohne Klipper-Verlauf mit automatischem Leeren,
  automatische Sperre (Inaktivität, Bildschirmsperre, Ruhezustand), Einrichtung mit
  Zwei-Faktor-Anmeldung im Terminal oder API-Schlüssel, MCP-Server ohne Lesezugriff auf
  Geheimnisse, Bitwarden-Erweiterung für Chrome.
