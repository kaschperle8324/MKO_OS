#!/bin/sh
# Baut mko-claude_<version>_all.deb ins Verzeichnis dist/
set -eu
cd "$(dirname "$0")/.."
VERSION=$(python3 -c "import mko_claude; print(mko_claude.__version__)")
MAIL=${MAINTAINER_MAIL:-info@steuerbar-it.de}
ROOT=$(mktemp -d)
trap 'rm -rf "$ROOT"' EXIT
chmod 755 "$ROOT"

PY="$ROOT/usr/lib/python3/dist-packages"
mkdir -p "$PY" "$ROOT/usr/bin" "$ROOT/DEBIAN" "$ROOT/usr/share/applications" "$ROOT/usr/share/kglobalaccel" \
         "$ROOT/etc/xdg/autostart" "$ROOT/usr/share/kio/servicemenus" \
         "$ROOT/usr/share/icons/hicolor/scalable/apps" "$ROOT/usr/share/doc/mko-claude"

cp -r mko_claude "$PY/"
find "$PY" -name __pycache__ -type d -prune -exec rm -rf {} +
cat > "$ROOT/usr/bin/mko-claude" <<'PYEOF'
#!/usr/bin/python3
from mko_claude.app import main
raise SystemExit(main())
PYEOF
chmod 755 "$ROOT/usr/bin/mko-claude"

install -m644 data/applications/mko-claude.desktop "$ROOT/usr/share/applications/"
# Über dieses Verzeichnis meldet KDE das Tastenkürzel (X-KDE-Shortcuts) automatisch an.
install -m644 data/applications/mko-claude.desktop "$ROOT/usr/share/kglobalaccel/"
install -m644 data/autostart/mko-claude.desktop "$ROOT/etc/xdg/autostart/"
install -m755 data/servicemenus/*.desktop "$ROOT/usr/share/kio/servicemenus/"
install -m644 data/icons/mko-claude.svg "$ROOT/usr/share/icons/hicolor/scalable/apps/"
install -m644 README.md "$ROOT/usr/share/doc/mko-claude/"
install -m644 ../LICENSE "$ROOT/usr/share/doc/mko-claude/copyright"

sed -e "s/@VERSION@/$VERSION/" -e "s/@MAIL@/$MAIL/" packaging/control > "$ROOT/DEBIAN/control"
echo "/etc/xdg/autostart/mko-claude.desktop" > "$ROOT/DEBIAN/conffiles"

mkdir -p dist
dpkg-deb --root-owner-group --build "$ROOT" "dist/mko-claude_${VERSION}_all.deb"
