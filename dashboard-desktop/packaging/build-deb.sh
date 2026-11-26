#!/bin/sh
# Baut mko-dashboard_<version>_all.deb ins Verzeichnis dist/
set -eu
cd "$(dirname "$0")/.."
VERSION=$(python3 -c "import mko_dashboard; print(mko_dashboard.__version__)")
MAIL=${MAINTAINER_MAIL:-info@steuerbar-it.de}
ROOT=$(mktemp -d)
trap 'rm -rf "$ROOT"' EXIT
chmod 755 "$ROOT"

PY="$ROOT/usr/lib/python3/dist-packages"
mkdir -p "$PY" "$ROOT/usr/bin" "$ROOT/DEBIAN" "$ROOT/usr/share/applications" "$ROOT/usr/share/kglobalaccel" \
         "$ROOT/etc/xdg/autostart" "$ROOT/usr/share/doc/mko-dashboard"
cp -r mko_dashboard "$PY/"
find "$PY" -name __pycache__ -type d -prune -exec rm -rf {} +
cat > "$ROOT/usr/bin/mko-dashboard" <<'PYEOF'
#!/usr/bin/python3
from mko_dashboard.app import main
raise SystemExit(main())
PYEOF
chmod 755 "$ROOT/usr/bin/mko-dashboard"
install -m644 data/mko-dashboard.desktop "$ROOT/usr/share/applications/"
# Über dieses Verzeichnis meldet KDE das Tastenkürzel (X-KDE-Shortcuts) an.
install -m644 data/mko-dashboard.desktop "$ROOT/usr/share/kglobalaccel/"
install -m644 data/mko-dashboard-autostart.desktop "$ROOT/etc/xdg/autostart/mko-dashboard.desktop"
install -m644 README.md "$ROOT/usr/share/doc/mko-dashboard/"
install -m644 ../LICENSE "$ROOT/usr/share/doc/mko-dashboard/copyright"
sed -e "s/@VERSION@/$VERSION/" -e "s/@MAIL@/$MAIL/" packaging/control > "$ROOT/DEBIAN/control"
echo "/etc/xdg/autostart/mko-dashboard.desktop" > "$ROOT/DEBIAN/conffiles"
mkdir -p dist
dpkg-deb --root-owner-group --build "$ROOT" "dist/mko-dashboard_${VERSION}_all.deb"
