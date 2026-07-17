#!/bin/bash
set -euo pipefail

VERSION="1.24.0"
ARCHIVE="shadowsocks-v${VERSION}.aarch64-apple-darwin.tar.xz"
URL="https://github.com/shadowsocks/shadowsocks-rust/releases/download/v${VERSION}/${ARCHIVE}"
EXPECTED_SHA256="bbbceeb2d452b19205e23863484bf7c126108c17b678783674e60bfb3d9a7359"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT/tools/shadowsocks"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

curl -fL "$URL" -o "$TMP/$ARCHIVE"
ACTUAL_SHA256="$(shasum -a 256 "$TMP/$ARCHIVE" | awk '{print $1}')"
if [[ "$ACTUAL_SHA256" != "$EXPECTED_SHA256" ]]; then
  echo "Ошибка проверки SHA-256 архива Shadowsocks" >&2
  exit 1
fi

mkdir -p "$DEST"
tar -xJf "$TMP/$ARCHIVE" -C "$TMP"
SSLOCAL="$(find "$TMP" -type f -name sslocal -print -quit)"
if [[ -z "$SSLOCAL" ]]; then
  echo "В архиве не найден sslocal" >&2
  exit 1
fi
cp "$SSLOCAL" "$DEST/sslocal"
chmod 755 "$DEST/sslocal"
echo "Shadowsocks установлен в $DEST/sslocal"
