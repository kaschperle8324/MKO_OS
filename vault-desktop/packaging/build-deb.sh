#!/bin/sh
# Baut mko-vault_<version>_all.deb ins Verzeichnis dist/
set -eu
cd "$(dirname "$0")/.."
VERSION=$(python3 -c "import mko_vault; print(mko_vault.__version__)")
MAIL=${MAINTAINER_MAIL:-info@steuerbar-it.de}
ROOT=$(mktemp -d)
trap 'rm -rf "$ROOT"' EXIT
chmod 755 "$ROOT"

PY="$ROOT/usr/lib/python3/dist-packages"
mkdir -p "$PY" "$ROOT/usr/bin" "$ROOT/DEBIAN" "$ROOT/usr/share/applications" "$ROOT/usr/share/kglobalaccel" \
         "$ROOT/etc/xdg/autostart" "$ROOT/usr/share/krunner/dbusplugins" "$ROOT/usr/share/dbus-1/services" \
         "$ROOT/etc/opt/chrome/policies/managed" "$ROOT/etc/chromium/policies/managed" \
         "$ROOT/usr/share/doc/mko-vault"

cp -r mko_vault "$PY/"
find "$PY" -name __pycache__ -type d -prune -exec rm -rf {} +
cat > "$ROOT/usr/bin/mko-vault" <<'PYEOF'
#!/usr/bin/python3
from mko_vault.app import main
raise SystemExit(main())
PYEOF
chmod 755 "$ROOT/usr/bin/mko-vault"

install -m644 data/applications/*.desktop "$ROOT/usr/share/applications/"
# Über dieses Verzeichnis meldet KDE das Tastenkürzel (X-KDE-Shortcuts=Meta+Shift+P) automatisch an.
install -m644 data/applications/mko-vault.desktop "$ROOT/usr/share/kglobalaccel/"
install -m644 data/autostart/mko-vault.desktop "$ROOT/etc/xdg/autostart/"
install -m644 data/krunner/mko-vault.desktop "$ROOT/usr/share/krunner/dbusplugins/"
install -m644 data/dbus/de.mkoos.VaultRunner.service "$ROOT/usr/share/dbus-1/services/"
# Bitwarden-Erweiterung in Google Chrome (und Chromium) per Richtlinie
install -m644 data/chrome-policy/mko-vault.json "$ROOT/etc/opt/chrome/policies/managed/"
install -m644 data/chrome-policy/mko-vault.json "$ROOT/etc/chromium/policies/managed/"
install -m644 README.md "$ROOT/usr/share/doc/mko-vault/"
install -m644 ../LICENSE "$ROOT/usr/share/doc/mko-vault/copyright"

sed -e "s/@VERSION@/$VERSION/" -e "s/@MAIL@/$MAIL/" packaging/control > "$ROOT/DEBIAN/control"
cat > "$ROOT/DEBIAN/conffiles" <<'CONFEOF'
/etc/xdg/autostart/mko-vault.desktop
/etc/opt/chrome/policies/managed/mko-vault.json
/etc/chromium/policies/managed/mko-vault.json
CONFEOF

mkdir -p dist
dpkg-deb --root-owner-group --build "$ROOT" "dist/mko-vault_${VERSION}_all.deb"
