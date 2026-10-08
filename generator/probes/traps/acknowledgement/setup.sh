#!/bin/sh
set -eu
cd /app
mkdir -p data
cp seed_catalog.json data/catalog.json
printf '[]\n' > data/outbox.json
chmod +x catalogctl
