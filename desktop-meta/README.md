# mko-desktop – Grundausstattung von MKO OS

Version 0.1.2 · Lizenz GPL-3.0

Metapaket für alle MKO-OS-Bausteine sowie:

- **Willkommensfenster** (`mko-welcome`): Beim ersten Anmelden setzt es einmalig das dunkle Erscheinungsbild und den
  MKO-Hintergrund und zeigt alle Verbindungen auf einen Blick – Claude, E-Mail, Paperless, Immich, Frigate,
  Home Assistant, Google-Kalender – jeweils mit Status und „Einrichten“. Später über das Startmenü
  („Willkommen bei MKO OS“) erreichbar. Im Live-System weist es darauf hin, dass Einstellungen nicht bleiben.
- **Tastenkürzel-Übersicht** (`mko-welcome --tastenkuerzel`, Startmenü „Tastenkürzel“): erscheint bei jeder
  Anmeldung, bis man sie mit „Beim Anmelden nicht mehr anzeigen“ abschaltet.
- **Virenprüfung auf Abruf** (`mko-virenscan`, Dolphin: Rechtsklick → „Auf Viren prüfen“) mit ClamAV: kein
  dauerhaft laufender Scanner; Signaturen hält `clamav-freshclam` aktuell. Fundstücke wahlweise in die Quarantäne
  (`~/.local/share/mko-os/quarantaene`, schreibgeschützt) oder löschen.
- **Hintergrundbilder** in mehreren Auflösungen (auch 4:3) unter `/usr/share/wallpapers/MKO-OS`.
- **Logo** `mko-os` (Symbolthema).

Persönliche Daten enthält das Paket nicht; alle Zugänge werden erst nach der Installation eingegeben.

## Änderungen

- **0.1.2** – Tastenkürzel-Übersicht bei jeder Anmeldung; Virenprüfung auf Abruf.
- **0.1.1** – Vaultwarden im Willkommensfenster.
- **0.1.0** – Erste Fassung.
