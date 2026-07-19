#!/usr/bin/env bash
# Installs the Shadowsocks clients used by the project on macOS.
set -euo pipefail

if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew is required. Install it from https://brew.sh/ and run this script again." >&2
  exit 1
fi

# shadowsocks-rust provides `sslocal`, which supports the HTTP listener used
# for OpenAI. shadowsocks-libev provides `ss-local` for the Telegram SOCKS5
# listener.
brew install shadowsocks-rust shadowsocks-libev

for binary in /opt/homebrew/bin/sslocal /opt/homebrew/bin/ss-local; do
  if [[ ! -x "$binary" ]]; then
    echo "Installation completed, but $binary was not found." >&2
    exit 1
  fi
done

echo "Shadowsocks clients are ready."
