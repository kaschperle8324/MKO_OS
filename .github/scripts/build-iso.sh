#!/bin/bash
# Läuft im Container debian:trixie (privilegiert): baut alle MKO-Pakete und daraus die Live-ISO.
set -euxo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends live-build python3 dpkg-dev ca-certificates curl gpg \
  debootstrap squashfs-tools xorriso grub-pc-bin grub-efi-amd64-bin grub-efi-amd64-signed shim-signed \
  mtools dosfstools syslinux syslinux-common isolinux

mkdir -p iso/config/packages.chroot
rm -f iso/config/packages.chroot/*.deb

# Alle Paketnamen der Listen vorab prüfen (meldet alle fehlenden auf einmal)
sed -i "s/^Components: main$/Components: main contrib non-free non-free-firmware/" /etc/apt/sources.list.d/debian.sources
apt-get update -qq
missing=""
for p in $(grep -hv '^\s*#' iso/config/package-lists/*.list.chroot | grep -v '^mko-' | tr -s ' \n' '\n'); do
  apt-cache show "$p" > /dev/null 2>&1 || missing="$missing $p"
done
if [ -n "$missing" ]; then echo "FEHLENDE PAKETE:$missing"; exit 1; fi
for script in */packaging/build-deb.sh; do
  dir=$(dirname "$(dirname "$script")")
  (cd "$dir" && rm -rf dist && sh packaging/build-deb.sh)
  cp "$dir"/dist/*.deb iso/config/packages.chroot/
done
ls -l iso/config/packages.chroot/

# Installierbarkeit vorab prüfen (schneller Fehler statt nach 30 Minuten im ISO-Bau)
apt-get install -y --dry-run ./iso/config/packages.chroot/*.deb > /dev/null

cd iso
./build.sh
