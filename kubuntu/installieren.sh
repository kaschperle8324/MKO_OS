#!/bin/bash
# MKO-OS-Bausteine auf Kubuntu installieren (Claude-Seitenleiste, E-Mail, Paperless, Immich, Frigate,
# Vaultwarden, Haus-Anzeige, Virenprüfung, Willkommensfenster) – aus der signierten MKO-OS-Paketquelle.
#
#   curl -fsSL https://kaschperle8324.github.io/MKO_OS/kubuntu.sh | sudo bash
#
# Optionen:
#   --alles       alle Zusatzprogramme ohne Nachfrage (Chrome, VSCodium, GIMP, FileZilla, Remmina,
#                 OpenVPN, Docker, Firewall)
#   --nur-bausteine   nur die MKO-Bausteine, keine Zusatzprogramme, keine Firewall-Änderung
#   --entfernen   MKO-Bausteine und Paketquelle wieder entfernen (Zusatzprogramme bleiben)
#
# Voraussetzung: Kubuntu 25.04 oder neuer (KDE Plasma 6), 64 Bit (amd64).
# Es werden keine Zugangsdaten abgefragt – die Verbindungen richtet danach das Willkommensfenster ein.
set -euo pipefail

# MKO_REPO_URL/MKO_KEY_FPR nur zum Testen gegen eine lokale Quelle
REPO_URL="${MKO_REPO_URL:-https://kaschperle8324.github.io/MKO_OS/apt}"
KEY_URL="$REPO_URL/mko-os.asc"
KEY_FPR="${MKO_KEY_FPR:-4336916057CC8CE53CA1702015AE5F4724EB1DE6}"
KEYRING=/usr/share/keyrings/mko-os.gpg
# Übergangsquelle; das Paket mko-desktop bringt danach die dauerhafte /etc/apt/sources.list.d/mko-os.sources
SETUP_SOURCE=/etc/apt/sources.list.d/mko-os-einrichtung.sources
export DEBIAN_FRONTEND=noninteractive

MODE=fragen
for arg in "$@"; do
  case "$arg" in
    --alles) MODE=alles ;;
    --nur-bausteine) MODE=keine ;;
    --entfernen) MODE=entfernen ;;
    -h|--help) echo "Optionen: --alles | --nur-bausteine | --entfernen"; exit 0 ;;
    *) echo "Unbekannte Option: $arg" >&2; exit 2 ;;
  esac
done

blau() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
info() { printf '    %s\n' "$*"; }
fehler() { printf '\n\033[1;31mFehler:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || fehler "Bitte mit sudo starten:  curl -fsSL https://kaschperle8324.github.io/MKO_OS/kubuntu.sh | sudo bash"

# Benutzer, der sudo aufgerufen hat (für Docker-Gruppe und Hinweise)
NUTZER="${SUDO_USER:-}"
[ "$NUTZER" = root ] && NUTZER=""

# Fragen auch dann stellen können, wenn das Skript über eine Pipe kommt
TTY=""
if [ -r /dev/tty ] && [ -w /dev/tty ] && { : </dev/tty; } 2>/dev/null; then TTY=/dev/tty; fi
frage() {  # frage "Text" [j|n]  → 0 = ja
  local vorgabe="${2:-j}" antwort
  case "$MODE" in alles) return 0 ;; keine) return 1 ;; esac
  if [ -z "$TTY" ]; then [ "$vorgabe" = j ]; return; fi
  local hinweis="[J/n]"; [ "$vorgabe" = n ] && hinweis="[j/N]"
  printf '    %s %s ' "$1" "$hinweis" >"$TTY"
  read -r antwort <"$TTY" || antwort=""
  antwort="${antwort:-$vorgabe}"
  case "$antwort" in [jJyY]*) return 0 ;; *) return 1 ;; esac
}

# ---------------------------------------------------------------------------------------------- Entfernen
if [ "$MODE" = entfernen ]; then
  blau "MKO-Bausteine entfernen"
  mapfile -t pakete < <(dpkg-query -W -f='${Package} ${Status}\n' 'mko-*' 2>/dev/null | awk '/installed$/{print $1}')
  if [ "${#pakete[@]}" -gt 0 ]; then
    apt-get purge -y "${pakete[@]}"
  else
    info "Keine MKO-Pakete installiert."
  fi
  # zur Laufzeit erzeugte Python-Zwischenspeicher bleiben sonst als leere Ordner zurück
  rm -rf /usr/lib/python3/dist-packages/mko_*
  rm -f "$SETUP_SOURCE" /etc/apt/sources.list.d/mko-os.sources "$KEYRING"
  apt-get update -qq || true
  info "Fertig. Persönliche Einstellungen liegen weiter unter ~/.config/mko-os (bei Bedarf löschen),"
  info "Zugangsdaten im KDE-Schlüsselbund (kwalletmanager, Ordner „mko-*“)."
  exit 0
fi

# ---------------------------------------------------------------------------------------------- Prüfen
blau "System prüfen"
[ -r /etc/os-release ] || fehler "/etc/os-release fehlt – unbekanntes System."
# shellcheck disable=SC1091
. /etc/os-release
case " ${ID:-} ${ID_LIKE:-} " in
  *" ubuntu "*) ;;
  *) fehler "Dieses Skript ist für Kubuntu. Erkannt: ${PRETTY_NAME:-unbekannt}. Für Debian 13 gibt es MKO OS als ISO." ;;
esac
[ "$(dpkg --print-architecture)" = amd64 ] || fehler "Nur für 64-Bit-PCs (amd64)."
ver="${VERSION_ID:-0}"
if dpkg --compare-versions "$ver" lt 25.04; then
  fehler "Kubuntu $ver hat noch KDE Plasma 5. Die MKO-Bausteine brauchen Plasma 6 und PySide6 –
       also Kubuntu 25.04 oder neuer (z. B. 26.04 LTS). Ein Upgrade geht mit:  sudo do-release-upgrade"
fi
plasma=$(dpkg-query -W -f='${Version}' plasma-workspace 2>/dev/null || true)
if [ -z "$plasma" ]; then
  info "Hinweis: KDE Plasma ist nicht installiert – die Bausteine sind für den Plasma-Desktop gebaut."
  frage "Trotzdem fortfahren?" n || exit 1
elif dpkg --compare-versions "$plasma" lt 4:6; then
  fehler "Plasma $plasma gefunden, benötigt wird Plasma 6."
fi
info "${PRETTY_NAME:-Ubuntu $ver}, Plasma ${plasma:-–}, Benutzer: ${NUTZER:-(unbekannt)}"

# ---------------------------------------------------------------------------------------------- Fragen
CHROME=0; CODIUM=0; BUERO=0; GIMP=0; FTP=0; REMOTE=0; VPN=0; DOCKER=0; FIREWALL=0
if [ "$MODE" != keine ]; then
  blau "Zusatzprogramme (wie auf der MKO-OS-ISO)"
  command -v google-chrome-stable >/dev/null || { frage "Google Chrome (für die Bitwarden-Erweiterung von mko-vault)?" && CHROME=1; } || true
  command -v codium >/dev/null || { frage "VSCodium?" && CODIUM=1; } || true
  dpkg -s libreoffice-writer >/dev/null 2>&1 || { frage "LibreOffice?" && BUERO=1; } || true
  command -v gimp >/dev/null || { frage "GIMP?" && GIMP=1; } || true
  command -v filezilla >/dev/null || { frage "FileZilla (FTP/SFTP)?" && FTP=1; } || true
  command -v remmina >/dev/null || { frage "Remmina (Fernzugriff RDP/VNC)?" && REMOTE=1; } || true
  dpkg -s network-manager-openvpn >/dev/null 2>&1 || { frage "OpenVPN für die Netzwerkverwaltung?" && VPN=1; } || true
  command -v docker >/dev/null || { frage "Docker?" n && DOCKER=1; } || true
  if ! ufw status 2>/dev/null | grep -q "Status: active"; then
    frage "Firewall einschalten (eingehend gesperrt, ausgehend frei)?" && FIREWALL=1 || true
  fi
fi

# ---------------------------------------------------------------------------------------------- Paketquelle
blau "MKO-OS-Paketquelle einrichten"
apt-get update -qq || true
apt-get install -y -qq curl gpg ca-certificates >/dev/null
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
curl -fsSL "$KEY_URL" -o "$tmp/mko-os.asc" || fehler "Schlüssel nicht erreichbar: $KEY_URL"
gpg --batch --quiet --dearmor <"$tmp/mko-os.asc" >"$tmp/mko-os.gpg"
fpr=$(gpg --batch --with-colons --show-keys "$tmp/mko-os.gpg" 2>/dev/null | awk -F: '/^fpr/{print $10; exit}')
[ "$fpr" = "$KEY_FPR" ] || fehler "Unerwarteter Signierschlüssel ($fpr) – Abbruch."
install -m644 "$tmp/mko-os.gpg" "$KEYRING"
if [ ! -f /etc/apt/sources.list.d/mko-os.sources ]; then
  cat >"$SETUP_SOURCE" <<EOF
Types: deb
URIs: $REPO_URL
Suites: stable
Components: main
Architectures: amd64
Signed-By: $KEYRING
EOF
fi
info "Schlüssel $KEY_FPR geprüft."

# Fremdquellen für Chrome und VSCodium (bleiben für Updates eingetragen)
install -d -m755 /etc/apt/keyrings
if [ "$CHROME" = 1 ]; then
  curl -fsSL https://dl.google.com/linux/linux_signing_key.pub | gpg --batch --yes --dearmor -o /etc/apt/keyrings/google-chrome.gpg
  echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/google-chrome.gpg] https://dl.google.com/linux/chrome/deb/ stable main" \
    >/etc/apt/sources.list.d/google-chrome.list
fi
if [ "$CODIUM" = 1 ]; then
  curl -fsSL https://gitlab.com/paulcarroty/vscodium-deb-rpm-repo/raw/master/pub.gpg | gpg --batch --yes --dearmor -o /etc/apt/keyrings/vscodium.gpg
  echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/vscodium.gpg] https://download.vscodium.com/debs vscodium main" \
    >/etc/apt/sources.list.d/vscodium.list
fi
apt-get update -qq

# ---------------------------------------------------------------------------------------------- Pakete
blau "MKO-Bausteine installieren"
apt-get install -y mko-desktop npm
# ab jetzt gilt die dauerhafte Quelle aus mko-desktop
[ -f /etc/apt/sources.list.d/mko-os.sources ] && rm -f "$SETUP_SOURCE"

extra=()
[ "$CHROME" = 1 ] && extra+=(google-chrome-stable)
[ "$CODIUM" = 1 ] && extra+=(codium)
[ "$BUERO" = 1 ] && extra+=(libreoffice libreoffice-l10n-de libreoffice-help-de)
[ "$GIMP" = 1 ] && extra+=(gimp)
[ "$FTP" = 1 ] && extra+=(filezilla)
[ "$REMOTE" = 1 ] && extra+=(remmina remmina-plugin-rdp remmina-plugin-vnc)
[ "$VPN" = 1 ] && extra+=(network-manager-openvpn)
[ "$DOCKER" = 1 ] && extra+=(docker.io)
[ "$FIREWALL" = 1 ] && extra+=(ufw plasma-firewall)
# automatische Sicherheitsupdates (auf Kubuntu meist schon vorhanden)
extra+=(unattended-upgrades mpv)
if [ "${#extra[@]}" -gt 0 ]; then
  blau "Zusatzprogramme installieren"
  apt-get install -y "${extra[@]}"
fi
if [ "$CHROME" = 1 ]; then
  # Chrome legt eine eigene Quelle an – doppelte Einträge entfernen
  for f in /etc/apt/sources.list.d/google-chrome*.list /etc/apt/sources.list.d/google-chrome*.sources; do
    if [ -e "$f" ] && [ "$f" != /etc/apt/sources.list.d/google-chrome.list ]; then rm -f "$f"; fi
  done
fi

# ---------------------------------------------------------------------------------------------- Claude Code, Bitwarden-CLI
blau "Claude Code und Bitwarden-CLI"
hat_claude=0
command -v claude >/dev/null && hat_claude=1
if [ -n "$NUTZER" ]; then
  heim=$(getent passwd "$NUTZER" | cut -d: -f6)
  [ -x "$heim/.local/bin/claude" ] && hat_claude=1
fi
npm_pakete=()
[ "$hat_claude" = 0 ] && npm_pakete+=(@anthropic-ai/claude-code)
command -v bw >/dev/null || npm_pakete+=(@bitwarden/cli)
if [ "${#npm_pakete[@]}" -gt 0 ]; then
  npm install -g --no-fund --no-audit "${npm_pakete[@]}"
else
  info "Bereits vorhanden."
fi
# wöchentliche Aktualisierung (Timer aus mko-desktop)
systemctl enable --now mko-npm-update.timer >/dev/null 2>&1 || true

# ---------------------------------------------------------------------------------------------- Sicherheit, Dienste
blau "Sicherheit und Dienste"
if [ ! -f /etc/apt/apt.conf.d/20auto-upgrades ]; then
  printf 'APT::Periodic::Update-Package-Lists "1";\nAPT::Periodic::Unattended-Upgrade "1";\n' \
    >/etc/apt/apt.conf.d/20auto-upgrades
fi
info "Automatische Sicherheitsupdates: an"
systemctl enable --now clamav-freshclam.service >/dev/null 2>&1 || true
info "Virenprüfung auf Abruf: Dolphin → Rechtsklick → „Auf Viren prüfen“ (Signaturen werden aktualisiert)"
if [ "$FIREWALL" = 1 ]; then
  ufw default deny incoming >/dev/null
  ufw default allow outgoing >/dev/null
  # KDE Connect weiter erreichbar lassen, falls benutzt
  dpkg -s kdeconnect >/dev/null 2>&1 && ufw allow 1714:1764/udp >/dev/null && ufw allow 1714:1764/tcp >/dev/null
  ufw --force enable >/dev/null
  info "Firewall: an (einstellbar unter Systemeinstellungen → Firewall)"
fi
if [ "$DOCKER" = 1 ] && [ -n "$NUTZER" ]; then
  usermod -aG docker "$NUTZER"
  info "$NUTZER ist jetzt in der Gruppe docker (wirkt nach dem nächsten Anmelden)."
fi

# ---------------------------------------------------------------------------------------------- Fertig
blau "Fertig"
info "Installiert: $(dpkg-query -W -f='${Package} ${Version}, ' 'mko-*' 2>/dev/null | sed 's/, $//')"
echo
info "So geht es weiter:"
info " 1. Einmal ab- und wieder anmelden (Tastenkürzel und Autostart werden dabei aktiv)."
info " 2. Das Fenster „Willkommen bei MKO OS“ öffnet sich – dort Claude, Postfächer, Paperless,"
info "    Immich, Frigate, Vaultwarden und Home Assistant verbinden. Jederzeit wieder über das Startmenü."
info " 3. Updates kommen künftig über Discover bzw.  sudo apt update && sudo apt full-upgrade"
info "Entfernen:  curl -fsSL https://kaschperle8324.github.io/MKO_OS/kubuntu.sh | sudo bash -s -- --entfernen"
