#!/bin/sh
# Build the archive bootstrap and the empty update-client transition.
set -eu
out=${1:?usage: build-archive-debs.sh OUTPUT}
mkdir -p "$out"
packaging=$(dirname "$0")/../packaging
export SOURCE_DATE_EPOCH=1794873600
for package in "$packaging"/*-archive-keyring "$packaging/mainline-update"; do
    dpkg-deb --root-owner-group --build "$package" "$out/"
done
