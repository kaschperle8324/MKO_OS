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
