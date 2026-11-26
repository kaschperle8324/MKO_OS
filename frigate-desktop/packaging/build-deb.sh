#!/bin/sh
# Baut mko-frigate_<version>_all.deb ins Verzeichnis dist/
set -eu
cd "$(dirname "$0")/.."
VERSION=$(python3 -c "import mko_frigate; print(mko_frigate.__version__)")
MAIL=${MAINTAINER_MAIL:-info@steuerbar-it.de}
ROOT=$(mktemp -d)
trap 'rm -rf "$ROOT"' EXIT
chmod 755 "$ROOT"
chmod 755 "$ROOT"

PY="$ROOT/usr/lib/python3/dist-packages"
mkdir -p "$PY" "$ROOT/usr/bin" "$ROOT/DEBIAN" "$ROOT/usr/share/applications" \
         "$ROOT/etc/xdg/autostart" "$ROOT/usr/share/doc/mko-frigate"

cp -r mko_frigate "$PY/"
find "$PY" -name __pycache__ -type d -prune -exec rm -rf {} +
cat > "$ROOT/usr/bin/mko-frigate" <<'PYEOF'
#!/usr/bin/python3
from mko_frigate.app import main
raise SystemExit(main())
PYEOF
chmod 755 "$ROOT/usr/bin/mko-frigate"

install -m644 data/applications/*.desktop "$ROOT/usr/share/applications/"
install -m644 data/autostart/mko-frigate.desktop "$ROOT/etc/xdg/autostart/"
install -m644 README.md "$ROOT/usr/share/doc/mko-frigate/"
install -m644 ../LICENSE "$ROOT/usr/share/doc/mko-frigate/copyright"

sed -e "s/@VERSION@/$VERSION/" -e "s/@MAIL@/$MAIL/" packaging/control > "$ROOT/DEBIAN/control"
echo "/etc/xdg/autostart/mko-frigate.desktop" > "$ROOT/DEBIAN/conffiles"

mkdir -p dist
dpkg-deb --root-owner-group --build "$ROOT" "dist/mko-frigate_${VERSION}_all.deb"
