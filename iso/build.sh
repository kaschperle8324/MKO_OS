#!/bin/sh
# Baut die MKO-OS-ISO. Erwartet: Debian 13, Root-Rechte, live-build.
# Die MKO-Pakete (.deb) müssen vorher in config/packages.chroot/ liegen (macht der GitHub-Workflow).
set -e
cd "$(dirname "$0")"
ls config/packages.chroot/*.deb >/dev/null 2>&1 || { echo "Keine MKO-Pakete in config/packages.chroot/"; exit 1; }
lb clean --purge || true
lb config
# Bootmenü-Texte anpassen („Live system“ -> „MKO OS Live“)
mkdir -p config/bootloaders
cp -r /usr/share/live/build/bootloaders/. config/bootloaders/
grep -rl "Live system" config/bootloaders | xargs -r sed -i 's/Live system/MKO OS Live/g'
grep -rl "Debian GNU/Linux" config/bootloaders | xargs -r sed -i 's#Debian GNU/Linux#MKO OS#g'
lb build 2>&1 | tee build.log
ls -lh ./*.iso
