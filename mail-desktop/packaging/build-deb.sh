#!/bin/sh
# Baut mko-mail_<version>_all.deb ins Verzeichnis dist/
set -eu
cd "$(dirname "$0")/.."
VERSION=$(python3 -c "import mko_mail; print(mko_mail.__version__)")
MAIL=${MAINTAINER_MAIL:-info@steuerbar-it.de}
ROOT=$(mktemp -d)
trap 'rm -rf "$ROOT"' EXIT
chmod 755 "$ROOT"

PY="$ROOT/usr/lib/python3/dist-packages"
mkdir -p "$PY" "$ROOT/usr/bin" "$ROOT/DEBIAN" "$ROOT/usr/share/applications" \
         "$ROOT/etc/xdg/autostart" "$ROOT/usr/share/doc/mko-mail"

cp -r mko_mail "$PY/"
find "$PY" -name __pycache__ -type d -prune -exec rm -rf {} +
cat > "$ROOT/usr/bin/mko-mail" <<'PYEOF'
#!/usr/bin/python3
from mko_mail.app import main
raise SystemExit(main())
PYEOF
chmod 755 "$ROOT/usr/bin/mko-mail"

install -m644 data/applications/*.desktop "$ROOT/usr/share/applications/"
install -m644 data/autostart/mko-mail.desktop "$ROOT/etc/xdg/autostart/"
install -m644 README.md "$ROOT/usr/share/doc/mko-mail/"
install -m644 ../LICENSE "$ROOT/usr/share/doc/mko-mail/copyright"

sed -e "s/@VERSION@/$VERSION/" -e "s/@MAIL@/$MAIL/" packaging/control > "$ROOT/DEBIAN/control"
echo "/etc/xdg/autostart/mko-mail.desktop" > "$ROOT/DEBIAN/conffiles"

mkdir -p dist
dpkg-deb --root-owner-group --build "$ROOT" "dist/mko-mail_${VERSION}_all.deb"
