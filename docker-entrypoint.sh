#!/usr/bin/env sh
# Container start: seed the default lineup into the data volume on first run, then launch the server.
# (The desktop app does the same copy itself; running from source doesn't, so it's done here.)
DATA="${TUNERBOX_DATA:-/data}"
mkdir -p "$DATA"
if [ ! -f "$DATA/channels.csv" ] && [ -f /app/data/channels.csv ]; then
  cp /app/data/channels.csv "$DATA/channels.csv"
fi
exec "$@"
