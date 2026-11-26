#!/bin/sh
# Baut mko-desktop_<version>_all.deb ins Verzeichnis dist/
set -eu
cd "$(dirname "$0")/.."
VERSION=$(python3 -c "import mko_welcome; print(mko_welcome.__version__)")
MAIL=${MAINTAINER_MAIL:-info@steuerbar-it.de}
ROOT=$(mktemp -d)
trap 'rm -rf "$ROOT"' EXIT
chmod 755 "$ROOT"
PY="$ROOT/usr/lib/python3/dist-packages"
mkdir -p "$PY" "$ROOT/usr/bin" "$ROOT/DEBIAN" "$ROOT/usr/share/applications" "$ROOT/etc/xdg/autostart" \
         "$ROOT/usr/share/wallpapers/MKO-OS" "$ROOT/usr/share/icons/hicolor/scalable/apps" \
         "$ROOT/usr/share/pixmaps" "$ROOT/usr/share/doc/mko-desktop"
cp -r mko_welcome "$PY/"
find "$PY" -name __pycache__ -type d -prune -exec rm -rf {} +
printf '#!/usr/bin/python3\nfrom mko_welcome.app import main\nraise SystemExit(main())\n' > "$ROOT/usr/bin/mko-welcome"
printf '#!/usr/bin/python3\nfrom mko_welcome.virenscan import main\nraise SystemExit(main())\n' > "$ROOT/usr/bin/mko-virenscan"
chmod 755 "$ROOT/usr/bin/mko-welcome" "$ROOT/usr/bin/mko-virenscan"
mkdir -p "$ROOT/usr/share/kio/servicemenus"
install -m755 data/servicemenus/mko-virenscan.desktop "$ROOT/usr/share/kio/servicemenus/"
install -m644 data/mko-welcome.desktop data/mko-tastenkuerzel.desktop "$ROOT/usr/share/applications/"
install -m644 data/mko-tastenkuerzel-autostart.desktop "$ROOT/etc/xdg/autostart/mko-tastenkuerzel.desktop"
install -m644 data/mko-welcome-autostart.desktop "$ROOT/etc/xdg/autostart/mko-welcome.desktop"
cp -r data/wallpaper/. "$ROOT/usr/share/wallpapers/MKO-OS/"
install -m644 data/mko-os.svg "$ROOT/usr/share/icons/hicolor/scalable/apps/"
install -m644 data/mko-os.svg "$ROOT/usr/share/pixmaps/"
install -m644 README.md "$ROOT/usr/share/doc/mko-desktop/"
install -m644 ../LICENSE "$ROOT/usr/share/doc/mko-desktop/copyright"
sed -e "s/@VERSION@/$VERSION/" -e "s/@MAIL@/$MAIL/" packaging/control > "$ROOT/DEBIAN/control"
printf '/etc/xdg/autostart/mko-welcome.desktop\n/etc/xdg/autostart/mko-tastenkuerzel.desktop\n' > "$ROOT/DEBIAN/conffiles"
mkdir -p dist
dpkg-deb --root-owner-group --build "$ROOT" "dist/mko-desktop_${VERSION}_all.deb"
