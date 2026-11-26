# mko-dashboard – Haus-Anzeige auf dem zweiten Bildschirm

Version 0.1.0 · Teil von MKO OS · Lizenz GPL-3.0

Die Haus-Anzeige zeigt die Werte von Heizung und PV-Anlage live aus Home
Assistant als Vollbild auf dem 4:3-Bildschirm. Sie liegt **hinter allen
Fenstern**, wirkt also wie ein lebendes Hintergrundbild: Ein Fenster, das du
auf diesen Bildschirm ziehst, liegt darüber. Ein- und ausschalten kannst du sie
jederzeit.

## Was angezeigt wird

**Energie**

| Kachel | Inhalt |
|---|---|
| PV-Erzeugung | Dach und Terrasse, Tagesertrag, Verlauf der letzten 12 h |
| Hausverbrauch | berechnet aus PV, Batterie und Netz; dazu der Anteil der Wärmepumpe |
| Batterie | Ladestand mit Balken, „lädt“ oder „entlädt“, Tageswerte |
| Netz | Bezug oder Einspeisung, Zählerstände |

**Heizung**

| Kachel | Inhalt |
|---|---|
| Wärmepumpe | elektrische und thermische Leistung, COP |
| Vorlauf | Ist- und Sollwert |
| Wohnraum | Ist- und Sollwert, Luftfeuchte |
| Warmwasser | Ist- und Sollwert |
| Heizung heute | Strom und Wärmeertrag |
| Anlagendruck | Die Kachel wird rot, wenn der Druck unter 1,2 bar fällt. |

In der Kopfzeile stehen Uhrzeit, Datum, Außentemperatur, Betriebsart der
Heizung und der Netzstatus.

Die Werte kommen per WebSocket von Home Assistant und ändern sich ohne
Verzögerung. Reißt die Verbindung ab, erscheint ein Hinweis, und die Anzeige
verbindet sich selbst neu.

## Ein- und ausschalten

- Klick auf das Symbol im Systemabschnitt der Taskleiste
- Super+Umschalt+H
- `mko-dashboard on` / `off` / `toggle`

Ob die Anzeige an oder aus ist, bleibt auch nach einem Neustart erhalten. Bei nur einem angeschlossenen Bildschirm belegt sie ihn nicht von selbst; dann im Menü „Bildschirm“ gezielt auswählen. Im
Menü des Symbols kannst du außerdem den Bildschirm wählen (Standard: der
Bildschirm mit 4:3-Seitenverhältnis), die Anzeige in den Vordergrund holen und
die Kacheln bearbeiten.

## Einrichtung

```sh
sudo apt install ./mko-dashboard_0.1.0_all.deb
```

Beim ersten Start fragt die Anzeige nach der Adresse von Home Assistant und nach
einem langlebigen Zugangstoken. Das Token erstellst du in Home Assistant unter
Profil → Sicherheit → Langlebige Zugangstoken. Es wird im KDE-Schlüsselbund
gespeichert. Danach startet die Anzeige bei jeder Anmeldung von selbst.

## Kacheln anpassen

Menü des Symbols → „Werte und Kacheln bearbeiten …“ öffnet
`~/.config/mko-os/dashboard-layout.toml`. Nach dem Speichern übernimmt die
Anzeige die Änderungen sofort. Jede Kachel besteht aus einem Titel, einem Wert
und einer Unterzeile. Darin stehen Platzhalter mit Entitäten und Grundrechenarten:

```toml
[[section.tile]]
title = "PV-Erzeugung"
value = "{sensor.pv_power + sensor.pv_anlage_terrasse_gesamtleistung|power}"
sub   = "Heute {sensor.pv_anlagen_tages_gesamt_produktion|num:1} kWh"
spark = "sensor.pv_power + sensor.pv_anlage_terrasse_gesamtleistung"
color = "#f6c344"
```

| Filter | Wirkung |
|---|---|
| `power` / `abspower` | Watt automatisch als W oder kW (mit oder ohne Vorzeichen) |
| `num:1` | Zahl mit Nachkommastellen |
| `sign:A:B` | Text je nach Vorzeichen |
| `flow:A:B` | Text und Leistung zusammen |
| `map:X=Y,…` | Zustände übersetzen |

Zusätzliche Felder einer Kachel:

| Feld | Wirkung |
|---|---|
| `badge` | Etikett rechts oben |
| `gauge` | Balken von 0 bis 100 |
| `alert` | Bedingung, bei der die Kachel rot wird |
| `spark_hours` | Länge des Verlaufs in Stunden |
| `span` | Breite der Kachel in Spalten |

Hinweis: `sensor.stromleser_e320_power_in_ha` meldet in Home Assistant die
Einheit „kW“, liefert aber Watt. Die Anzeige rechnet deshalb direkt mit Watt.

## Einstellungen

`~/.config/mko-os/dashboard.toml`

```toml
url = "http://homeassistant.local:8123"
verify_tls = true
screen = "auto"        # oder Name aus `mko-dashboard screens`, z. B. "DP-2"
on_top = false         # true: über allen Fenstern statt dahinter
night = ""             # z. B. "23:00-06:00": nachts abgedunkelt
night_dim = 85         # Prozent
pixel_shift = true     # alle 5 Minuten 1–2 Pixel verschieben (schont den Bildschirm)
```

## Entwicklung

```sh
python3 tests/mock_ha.py 8123 &      # Home-Assistant-Nachbau
QT_QPA_PLATFORM=offscreen MKO_HA_URL=http://127.0.0.1:8123 MKO_HA_TOKEN=testtoken python3 tests/ui_check.py
packaging/build-deb.sh
```

## Änderungen

- **0.1.0** – Erste Fassung: Live-Anzeige von Heizung und PV-Anlage, automatische Wahl des 4:3-Bildschirms,
  Ebene hinter allen Fenstern, Ein-/Ausschalter, anpassbare Kacheln, Nachtabdunklung.
