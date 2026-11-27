#!/bin/sh
# Baut mko-paperless_<version>_all.deb ins Verzeichnis dist/
set -eu
cd "$(dirname "$0")/.."
VERSION=$(python3 -c "import mko_paperless; print(mko_paperless.__version__)")
MAIL=${MAINTAINER_MAIL:-info@steuerbar-it.de}
ROOT=$(mktemp -d)
trap 'rm -rf "$ROOT"' EXIT
chmod 755 "$ROOT"

PY="$ROOT/usr/lib/python3/dist-packages"
mkdir -p "$PY" "$ROOT/usr/bin" "$ROOT/DEBIAN" \
         "$ROOT/usr/lib/systemd/user" "$ROOT/usr/share/krunner/dbusplugins" \
         "$ROOT/usr/share/dbus-1/services" "$ROOT/usr/share/applications" \
         "$ROOT/usr/share/doc/mko-paperless"

cp -r mko_paperless "$PY/"
find "$PY" -name __pycache__ -type d -prune -exec rm -rf {} +
cat > "$ROOT/usr/bin/mko-paperless" <<'PYEOF'
#!/usr/bin/python3
from mko_paperless.cli import main
raise SystemExit(main())
PYEOF
chmod 755 "$ROOT/usr/bin/mko-paperless"

install -m644 data/systemd/*.service "$ROOT/usr/lib/systemd/user/"
install -m644 data/krunner/mko-paperless.desktop "$ROOT/usr/share/krunner/dbusplugins/"
install -m644 data/dbus/de.mkoos.PaperlessRunner.service "$ROOT/usr/share/dbus-1/services/"
install -m644 data/applications/*.desktop "$ROOT/usr/share/applications/"
mkdir -p "$ROOT/etc/xdg/autostart"
install -m644 data/autostart/mko-paperless-ui.desktop "$ROOT/etc/xdg/autostart/"
install -m644 README.md "$ROOT/usr/share/doc/mko-paperless/"
install -m644 ../LICENSE "$ROOT/usr/share/doc/mko-paperless/copyright"

sed -e "s/@VERSION@/$VERSION/" -e "s/@MAIL@/$MAIL/" packaging/control > "$ROOT/DEBIAN/control"
install -m755 packaging/postinst packaging/prerm "$ROOT/DEBIAN/"
printf '/etc/xdg/autostart/mko-paperless-ui.desktop\n' > "$ROOT/DEBIAN/conffiles"

mkdir -p dist
dpkg-deb --root-owner-group --build "$ROOT" "dist/mko-paperless_${VERSION}_all.deb"
